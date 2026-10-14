"""Esegue il grafo reale e le API con Ollama sostituito da un interprete di test."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.chat_graph import ChatService
from backend.customer_store import CustomerStore, CustomerStoreError
from backend.product_agent import CATALOG_PATH, ProductAgent
from backend.server import COOKIE_NAME, create_app


class Interpreter:
    def __init__(self, result=None, fail=False):
        self.result = result or {"intent": "product", "category_ids": [], "product_ids": [], "use_previous": False}
        self.fail = fail
        self.calls = 0

    def interpret(self, question, categories):
        self.calls += 1
        if self.fail:
            raise ConnectionError("Test: Ollama spento")
        return self.result


class ChatIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = CustomerStore(self.root / "customers.json")
        self.catalog = self.root / "catalog.json"
        document = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        document["products"] = []
        for category in document["categories"]:
            category.pop("products_file", None)
        document.pop("demo_mode", None)
        document.pop("demo_notice", None)
        self.catalog.write_text(json.dumps(document), encoding="utf-8")
        self.products = ProductAgent(self.catalog)
        self.interpreter = Interpreter()
        self.client = TestClient(create_app(self.store, self.interpreter, self.products))
        self.addCleanup(self.client.close)

    def identify(self, client=None, remember=True):
        client = client or self.client
        response = client.post("/api/session", json={"name": "Cliente test", "email": "test@example.invalid", "remember": remember})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def ask(self, message, request_id=None, client=None):
        return (client or self.client).post("/api/chat", json={"message": message, "request_id": request_id or str(uuid4())})

    def test_website_contains_chat_and_private_files_are_not_served(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="chat-toggle"', response.text)
        self.assertIn('src="chat.js"', response.text)
        for path in ("/chat.js", "/style.css", "/script.js"):
            self.assertEqual(self.client.get(path).status_code, 200)
        for path in ("/data/runtime/customers.json", "/backend/customer_store.py", "/.env", "/.git/config"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_chat_requires_identity_and_uses_http_only_cookie(self):
        self.assertEqual(self.ask("Ciao").status_code, 401)
        response = self.identify()
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertNotIn("token", response.json()["customer"])
        self.assertEqual(self.client.get("/api/session").headers["cache-control"], "no-store")

    def test_question_is_persisted_and_preference_visible_from_api(self):
        self.identify()
        response = self.ask("Cerco un pigiama")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["saved"])
        self.assertEqual(data["preferences"]["category_counts"], {"2": 1})

        self.assertEqual(data["status"], "clarification")
        self.assertTrue(any("categoria=2" in link["url"] for link in data["links"]))
        self.assertEqual(self.client.get("/api/preferences").json()["preferences"], data["preferences"])
        document = json.loads(self.store.path.read_text(encoding="utf-8"))
        customer = next(iter(document["customers"].values()))
        self.assertEqual(customer["questions"][0]["question"], "Cerco un pigiama")
        self.assertEqual(customer["questions"][0]["category_ids"], [2])

    def test_optout_allows_chat_without_creating_customer_file(self):
        self.identify(remember=False)
        data = self.ask("Vorrei biancheria").json()
        self.assertFalse(data["saved"])
        self.assertEqual(data["preferences"]["category_counts"], {})
        self.assertFalse(self.store.path.exists())

    def test_duplicate_retry_is_not_saved_or_counted_twice(self):
        self.identify()
        request_id = str(uuid4())
        first = self.ask("Mi interessano i pigiami", request_id)
        second = self.ask("Mi interessano i pigiami", request_id)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(self.interpreter.calls, 1)
        self.assertEqual(self.client.get("/api/preferences").json()["preferences"]["category_counts"], {"2": 1})
        self.assertEqual(self.ask("Vorrei intimo", request_id).status_code, 409)

    def test_same_email_does_not_merge_profiles(self):
        self.identify()
        self.ask("Vorrei intimo")
        other = TestClient(create_app(self.store, Interpreter(), self.products))
        self.addCleanup(other.close)
        self.identify(other)
        self.assertEqual(other.get("/api/preferences").json()["preferences"]["category_counts"], {})
        self.ask("Cerco un pigiama", client=other)
        self.assertEqual(self.client.get("/api/preferences").json()["preferences"]["category_counts"], {"1": 1})

    def test_persisted_profile_survives_server_restart_with_same_cookie(self):
        self.identify()
        self.ask("Vorrei biancheria")
        token = self.client.cookies.get(COOKIE_NAME)
        restarted = TestClient(create_app(CustomerStore(self.store.path), Interpreter(), self.products))
        self.addCleanup(restarted.close)
        restarted.cookies.set(COOKIE_NAME, token)
        self.assertEqual(restarted.get("/api/preferences").json()["preferences"]["category_counts"], {"3": 1})

    def test_followup_uses_context_only_in_same_customer_session(self):
        self.identify()
        self.ask("Cerco pigiami")
        self.assertIn("Pigiameria", self.ask("E le taglie?").json()["reply"])
        self.assertEqual(self.client.get("/api/preferences").json()["preferences"]["category_counts"], {"2": 2})

    def test_unknown_id_is_not_a_product_preference(self):
        self.identify()
        data = self.ask("Prezzo prodotto ID 2001?").json()
        self.assertIn("Non trovo una scheda", data["reply"])
        self.assertEqual(data["preferences"]["product_counts"], {})
        self.assertEqual(data["preferences"]["category_counts"], {})

    def test_found_product_uses_only_real_fields_and_records_verified_reference(self):
        document = json.loads(self.catalog.read_text())
        document["products"] = [{"id": 2001, "category_id": 2, "name": "SOLO TEST", "price_eur": None}]
        self.catalog.write_text(json.dumps(document), encoding="utf-8")
        self.identify()
        data = self.ask("Qual è il prezzo del prodotto 2001?").json()
        self.assertIn("SOLO TEST", data["reply"])
        self.assertIn("non è indicato", data["reply"])
        self.assertNotRegex(data["reply"], r"\d+[,.]\d{2}\s*€")
        self.assertEqual(data["preferences"]["product_counts"], {"2001": 1})
        self.assertEqual(data["preferences"]["category_counts"], {"2": 1})
        category_reply = self.ask("Mostrami la categoria Pigiameria").json()["reply"]
        self.assertIn("SOLO TEST", category_reply)

    def test_negative_interest_does_not_increase_preference_count(self):
        self.identify()
        data = self.ask("Non mi interessano i pigiami").json()
        self.assertTrue(data["saved"])
        self.assertEqual(data["preferences"]["category_counts"], {})

    def test_ollama_failure_is_visible_and_memory_still_works(self):
        self.identify()
        self.interpreter.fail = True
        data = self.ask("Cerco intimo").json()
        self.assertEqual(data["status"], "degraded")
        self.assertFalse(data["llm_available"])
        self.assertTrue(data["saved"])
        self.assertIn("Ollama non è disponibile", data["reply"])
        self.assertEqual(data["preferences"]["category_counts"], {"1": 1})

    def test_model_cannot_invent_product_id(self):
        self.interpreter.result["product_ids"] = [2001]
        self.interpreter.result["category_ids"] = [999]
        self.identify()
        data = self.ask("Ciao").json()
        self.assertNotIn("2001", data["reply"])
        self.assertEqual(data["preferences"]["product_counts"], {})

    def test_years_prices_and_sizes_do_not_become_product_ids(self):
        self.identify()
        self.interpreter.result["product_ids"] = [2026, 42]
        for question in ("Nel 2026 avete pigiami?", "Cerco pigiami taglia 42"):
            data = self.ask(question).json()
            self.assertTrue(any("Pigiameria" in link["label"] for link in data["links"]))
            self.assertNotIn("Non trovo una scheda", data["reply"])
            self.assertEqual(data["preferences"]["product_counts"], {})

    def test_model_cannot_replace_explicit_product_context(self):
        document = json.loads(self.catalog.read_text(encoding="utf-8"))
        document["products"] = [{"id": 2001, "category_id": 2, "name": "TEST CONTESTO", "price_eur": 34.9}]
        self.catalog.write_text(json.dumps(document), encoding="utf-8")
        self.identify()
        self.ask("Prodotto 2001")
        self.interpreter.result["category_ids"] = [1]
        data = self.ask("Quanto costa quello?").json()
        self.assertIn("TEST CONTESTO", data["reply"])
        self.assertIn("34,90", data["reply"])
        self.assertEqual(data["links"][0]["url"], "/catalogo?categoria=2&prodotto=2001")
        self.assertEqual(data["preferences"]["category_counts"], {"2": 2})

    def test_category_inference_requires_evidence_and_product_intent(self):
        self.identify()
        self.interpreter.result.update(intent="other", category_ids=[4], category_evidence={"4": "ciao"})
        data = self.ask("ciao").json()
        self.assertEqual(data["preferences"]["category_counts"], {})
        self.interpreter.result.update(intent="product", category_ids=[2], category_evidence={"2": "vestiti per dormire"})
        data = self.ask("Cerco vestiti per dormire").json()
        self.assertTrue(any("Pigiameria" in link["label"] for link in data["links"]))
        self.assertEqual(data["preferences"]["category_counts"], {"2": 1})

    def test_model_can_route_semantic_human_and_preference_requests(self):
        self.identify()
        self.interpreter.result["intent"] = "human"
        self.assertEqual(self.ask("Vorrei parlarne con chi lavora qui").json()["status"], "human_required")
        self.interpreter.result["intent"] = "preferences"
        self.assertIn("Non ci sono ancora interessi", self.ask("Di cosa parliamo di solito?").json()["reply"])

    def test_human_request_gives_explicit_manual_handoff(self):
        self.identify()
        data = self.ask("Voglio un operatore").json()
        self.assertEqual(data["status"], "human_required")
        self.assertIn("Nessun operatore è stato contattato", data["reply"])
        self.assertTrue(data["saved"])

    def test_catalog_and_preference_write_errors_require_human(self):
        self.identify()
        self.catalog.write_text("{", encoding="utf-8")
        data = self.ask("Cerco intimo").json()
        self.assertEqual(data["status"], "human_required")
        self.assertIn("catalogo non è leggibile", data["reply"])
        self.assertEqual(data["preferences"]["category_counts"], {})
        with patch.object(self.store, "record_question", side_effect=CustomerStoreError("test")):
            data = self.ask("Un'altra domanda").json()
        self.assertFalse(data["saved"])
        self.assertIn("Non è stato possibile salvare", data["reply"])

    def test_show_preferences_does_not_increment_them(self):
        self.identify()
        self.ask("Vorrei intimo")
        data = self.ask("Quali sono le mie preferenze?").json()
        self.assertIn("Intimo (1)", data["reply"])
        self.assertEqual(data["preferences"]["category_counts"], {"1": 1})

    def test_delete_erases_only_current_customer_and_cookie(self):
        self.identify()
        self.ask("Vorrei intimo")
        self.assertEqual(self.client.delete("/api/session").json(), {"ok": True})
        self.assertIsNone(self.client.get("/api/session").json()["customer"])
        self.assertEqual(self.ask("Ciao").status_code, 401)
        document = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(document["customers"], {})

    def test_input_validation_and_cross_origin_protection(self):
        self.assertEqual(self.client.post("/api/session", json={"name": "x", "email": "invalid", "remember": True}).status_code, 422)
        self.assertEqual(self.client.post("/api/session", headers={"origin": "https://example.invalid"},
            json={"name": "x", "email": "x@example.invalid", "remember": True}).status_code, 403)
        self.identify()
        self.assertEqual(self.ask(" ").status_code, 422)
        self.assertEqual(self.ask("x" * 2001).status_code, 422)
        self.assertEqual(self.ask("Ciao", "bad-id").status_code, 422)


if __name__ == "__main__":
    unittest.main()
