"""Catalogo demo e recupero dagli errori, sempre con archivi cliente temporanei."""

import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.customer_store import CustomerStore, CustomerStoreError
from backend.product_agent import CATALOG_PATH, ProductAgent
from backend.server import create_app


class Interpreter:
    def interpret(self, question, categories):
        return {"intent": "product", "category_ids": [], "product_ids": [], "use_previous": False}


class DemoAndErrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "customers.json"
        self.store = CustomerStore(self.path)
        self.catalog_path = self.root / "catalog.json"
        self.catalog_path.write_bytes(CATALOG_PATH.read_bytes())
        shutil.copytree(CATALOG_PATH.parent / "categories", self.root / "categories")
        self.products = ProductAgent(self.catalog_path)
        self.interpreter = Interpreter()
        self.app = create_app(self.store, self.interpreter, self.products)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        response = self.client.post("/api/session", json={
            "name": "Prova demo", "email": "demo@example.invalid", "remember": True})
        self.assertEqual(response.status_code, 200, response.text)

    def ask(self, question, request_id=None):
        response = self.client.post("/api/chat", json={
            "message": question, "request_id": request_id or str(uuid4())})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_each_category_shows_three_demo_products(self):
        for category in self.products.list_categories()["data"]:
            with self.subTest(category=category["name"]):
                response = self.ask("Mostrami la categoria " + category["name"])
                records = self.products.list_products(category["id"])["data"]
                self.assertEqual(len(records), 3)
                for product in records:
                    self.assertIn(product["name"], response["reply"])
                self.assertIn("demo", response["reply"].lower())
                self.assertEqual(len(response["links"]), 3)

    def test_search_by_name_returns_one_sheet_and_records_verified_interest(self):
        response = self.ask("Quanto costa il Reggiseno Aurora?")
        self.assertIn("Reggiseno Aurora", response["reply"])
        self.assertIn("24,90", response["reply"])
        self.assertIn("fittizi", response["reply"])
        self.assertEqual(response["links"][0]["url"], "/catalogo?categoria=1&prodotto=1001")
        self.assertNotIn("is_demo:", response["reply"])
        self.assertEqual(response["preferences"]["product_counts"], {"1001": 1})
        self.assertEqual(response["preferences"]["category_counts"], {"1": 1})

    def test_new_product_name_has_priority_over_previous_context(self):
        self.ask("Prodotto 2001")
        response = self.ask("Quanto costa Aurora?")
        self.assertIn("Reggiseno Aurora", response["reply"])
        self.assertNotIn("Pigiama Luna", response["reply"])

    def test_name_search_handles_questions_about_sizes_and_colors(self):
        for question, product_id, expected in (
            ("Che taglie ha Pigiama Luna?", 2001, "S, M, L e XL"),
            ("Quali colori ha Reggiseno Aurora?", 1001, "Bianco, Nero e Cipria"),
        ):
            with self.subTest(question=question):
                response = self.ask(question)
                self.assertIn(expected, response["reply"])
                self.assertIn(f"prodotto={product_id}", response["links"][0]["url"])
                self.assertNotIn("Prodotto ID", response["reply"])

    def test_broad_pigiama_search_respects_explicit_category(self):
        response = self.ask("Pigiama")
        self.assertIn("Pigiama Luna", response["reply"])
        self.assertIn("Pigiama Brezza", response["reply"])
        self.assertNotIn("Pigiama Bimbo Orsetto", response["reply"])
        self.assertEqual(response["preferences"]["product_counts"], {})

    def test_missing_material_and_unavailable_product_are_explicit(self):
        response = self.ask("Di quale materiale è il prodotto 3003?")
        self.assertIn("materiale", response["reply"])
        self.assertIn("non è indicato", response["reply"])
        self.assertIn("fittizi", response["reply"])
        response = self.ask("È disponibile il prodotto 2003?")
        self.assertIn("non disponibile (simulazione)", response["reply"])

    def test_unknown_id_is_not_invented_or_counted_as_product(self):
        response = self.ask("Prodotto 9999")
        self.assertIn("Non trovo una scheda", response["reply"])
        self.assertEqual(response["preferences"]["product_counts"], {})

    def test_tool_exceptions_are_structured_and_original_error_is_not_exposed(self):
        for operation, question in (
            ("list_categories", "Prodotto 1001"),
            ("get_product", "Prodotto 1001"),
            ("list_products", "Mostrami la categoria Intimo"),
            ("search_products", "Reggiseno Aurora"),
        ):
            with self.subTest(operation=operation), patch.object(
                    self.products, operation, side_effect=RuntimeError("private-path")):
                response = self.ask(question)
                self.assertEqual(response["status"], "human_required")
                self.assertTrue(response["saved"])
                self.assertNotIn("private-path", response["reply"])

    def test_malformed_tool_result_becomes_explicit_error(self):
        with patch.object(self.products, "get_product", return_value={"status": "ok"}):
            response = self.ask("Prodotto 1001")
        self.assertEqual(response["status"], "human_required")

    def test_invalid_ollama_output_falls_back_without_losing_question(self):
        with patch.object(self.interpreter, "interpret", return_value={"intent": "product", "category_ids": [True]}):
            response = self.ask("Prodotto 1001")
        self.assertEqual(response["status"], "degraded")
        self.assertFalse(response["llm_available"])
        self.assertTrue(response["saved"])
        self.assertIn("Reggiseno Aurora", response["reply"])

    def test_failed_write_preserves_preferences_and_retry_can_recover(self):
        self.ask("Prodotto 1001")
        request_id = str(uuid4())
        with patch.object(self.store, "record_question", side_effect=CustomerStoreError("private-path")):
            failed = self.ask("Prodotto 2001", request_id)
        self.assertFalse(failed["saved"])
        self.assertTrue(failed["preferences_available"])
        self.assertEqual(failed["preferences"]["product_counts"], {"1001": 1})
        recovered = self.ask("Prodotto 2001", request_id)
        self.assertTrue(recovered["saved"])
        self.assertEqual(recovered["preferences"]["product_counts"], {"1001": 1, "2001": 1})
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(next(iter(document["customers"].values()))["questions"]), 2)

    def test_unreadable_preferences_are_marked_unavailable_not_empty(self):
        with patch.object(self.store, "record_question", side_effect=CustomerStoreError("test")), \
                patch.object(self.store, "get_preferences", side_effect=CustomerStoreError("test")):
            response = self.ask("Prodotto 1001")
        self.assertFalse(response["preferences_available"])
        self.assertFalse(response["saved"])

    def test_corrupt_archive_does_not_stop_public_site_or_overwrite_file(self):
        broken = self.root / "broken.json"
        broken.write_text("{broken-json", encoding="utf-8")
        with patch.dict(os.environ, {"CUSTOMER_DATA_PATH": str(broken)}):
            app = create_app(interpreter=Interpreter(), products=self.products)
        with TestClient(app, raise_server_exceptions=False) as client:
            self.assertEqual(client.get("/").status_code, 200)
            response = client.post("/api/session", json={
                "name": "Test", "email": "test@example.invalid", "remember": True})
            self.assertEqual(response.status_code, 503)
            self.assertIn("non disponibile", response.json()["detail"])
        self.assertEqual(broken.read_text(encoding="utf-8"), "{broken-json")

    def test_session_disappearing_during_request_returns_401(self):
        customer = self.client.get("/api/session").json()["customer"]
        with patch.object(self.store, "get_customer", side_effect=[customer, None]):
            response = self.client.post("/api/chat", json={"message": "Prodotto 1001", "request_id": str(uuid4())})
        self.assertEqual(response.status_code, 401)
        self.assertIn("sessione è scaduta", response.json()["detail"])

    def test_unexpected_failure_returns_json_without_private_details(self):
        with patch.object(self.app.state.chat_service.graph, "invoke", side_effect=RuntimeError("private-path")):
            response = self.client.post("/api/chat", json={"message": "Prodotto 1001", "request_id": str(uuid4())})
        self.assertEqual(response.status_code, 500)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertNotIn("private-path", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_malformed_request_has_readable_validation_error(self):
        response = self.client.post("/api/chat", content="{", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertIsInstance(response.json()["detail"], str)


if __name__ == "__main__":
    unittest.main()
