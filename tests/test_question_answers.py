"""Domande reali, risposte mirate e collegamenti al catalogo pubblico.

Il grafo LangGraph e le API sono reali; solo l'interprete LLM è sostituito.
Il catalogo demo viene letto senza modificarlo e i clienti usano file temporanei.
"""

import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.customer_store import CustomerStore
from backend.product_agent import ProductAgent
from backend.server import create_app


class FakeLLM:
    def __init__(self):
        self.fail = False

    def interpret(self, question, categories):
        if self.fail:
            raise ConnectionError("Ollama non raggiungibile nel test")
        return {
            "intent": "product",
            "category_ids": [],
            "product_ids": [],
            "use_previous": False,
            "category_evidence": {},
        }


class QuestionAnswerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = CustomerStore(Path(temporary.name) / "customers.json")
        self.products = ProductAgent()
        self.llm = FakeLLM()
        self.client = TestClient(create_app(self.store, self.llm, self.products))
        self.addCleanup(self.client.close)
        response = self.client.post("/api/session", json={
            "name": "Cliente prova", "email": "cliente@example.invalid", "remember": True,
        })
        self.assertEqual(response.status_code, 200, response.text)

    def ask(self, message, request_id=None):
        response = self.client.post("/api/chat", json={
            "message": message, "request_id": request_id or str(uuid4()),
        })
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertIsInstance(data["reply"], str)
        self.assertIsInstance(data["links"], list)
        # Ogni link esposto deve indirizzare una categoria e, se specificato,
        # un prodotto esistenti: nessuna destinazione ricavata dall'LLM.
        for link in data["links"]:
            self.assertIsInstance(link["label"], str)
            self.assertTrue(link["label"].strip())
            location = urlsplit(link["url"])
            self.assertFalse(location.scheme)
            self.assertFalse(location.netloc)
            self.assertEqual(location.path, "/catalogo")
            query = parse_qs(location.query)
            self.assertEqual(len(query.get("categoria", [])), 1)
            category_id = int(query["categoria"][0])
            self.assertEqual(self.products.list_products(category_id)["status"], "ok")
            if "prodotto" in query:
                self.assertEqual(len(query["prodotto"]), 1)
                product = self.products.get_product(int(query["prodotto"][0]))
                self.assertEqual(product["status"], "ok")
                self.assertEqual(product["data"]["category_id"], category_id)
        return data

    def assert_product_link(self, response, product_id):
        linked = {
            int(query["prodotto"][0])
            for link in response["links"]
            if "prodotto" in (query := parse_qs(urlsplit(link["url"]).query))
        }
        self.assertIn(product_id, linked)

    def assert_no_sheet(self, reply):
        self.assertNotIn("Prodotto ID", reply)
        self.assertNotIn("Descrizione:", reply)
        self.assertNotIn("Marca:", reply)
        self.assertLess(len(reply), 650, "Una domanda specifica non richiede la scheda completa")

    def test_price_by_name_is_concise_linked_and_saved_only_once_on_retry(self):
        request_id = str(uuid4())
        question = "Quanto costa il Reggiseno Aurora?"
        answer = self.ask(question, request_id)
        self.assertIn("Aurora", answer["reply"])
        self.assertIn("24,90", answer["reply"])
        self.assertIn("€", answer["reply"])
        self.assertNotIn("Taglie:", answer["reply"])
        self.assertNotIn("Pigiama", answer["reply"])
        self.assertNotIn("1001", answer["reply"])
        self.assert_no_sheet(answer["reply"])
        self.assert_product_link(answer, 1001)
        self.assertTrue(answer["saved"])
        self.assertEqual(answer["preferences"]["category_counts"], {"1": 1})
        self.assertEqual(answer["preferences"]["product_counts"], {"1001": 1})
        self.assertEqual(self.ask(question, request_id), answer)
        stored = json.loads(self.store.path.read_text(encoding="utf-8"))
        questions = next(iter(stored["customers"].values()))["questions"]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["question"], question)

    def test_sizes_and_followup_colors_refer_to_the_same_named_product(self):
        sizes = self.ask("Che taglie ha Pigiama Luna?")
        self.assertIn("Luna", sizes["reply"])
        for size in ("S", "M", "L", "XL"):
            self.assertRegex(sizes["reply"], rf"\b{size}\b")
        self.assertNotIn("cotone", sizes["reply"].lower())
        self.assertNotIn("34,90", sizes["reply"])
        self.assertNotIn("2001", sizes["reply"])
        self.assert_no_sheet(sizes["reply"])
        self.assert_product_link(sizes, 2001)

        colors = self.ask("E in quali colori?")
        self.assertIn("Blu", colors["reply"])
        self.assertIn("Grigio", colors["reply"])
        self.assertNotIn("Brezza", colors["reply"])
        self.assertNotIn("Taglie:", colors["reply"])
        self.assert_no_sheet(colors["reply"])
        self.assert_product_link(colors, 2001)
        self.assertEqual(colors["preferences"]["category_counts"], {"2": 2})
        self.assertEqual(colors["preferences"]["product_counts"], {"2001": 2})

    def test_missing_material_is_explained_without_inventing_fabric(self):
        answer = self.ask("Di che tessuto è Copripiumino Sogno?")
        text = answer["reply"].lower()
        self.assertIn("sogno", text)
        self.assertRegex(text, r"material|tessut|composizion")
        self.assertRegex(text, r"non.*(?:disponibil|indicat|present|specificat|conosc)|manca|non compilat")
        for fabricated in ("cotone", "poliestere", "lino", "seta"):
            self.assertNotIn(fabricated, text)
        self.assertNotIn("59,90", text)
        self.assert_no_sheet(answer["reply"])
        self.assert_product_link(answer, 3003)

    def test_specific_color_does_not_claim_variant_stock(self):
        for color, expected in (("nero", "Il colore nero è indicato"), ("rosso", "Il colore rosso non è indicato")):
            with self.subTest(color=color):
                answer = self.ask(f"Avete il Reggiseno Aurora in {color}?")
                self.assertIn(expected, answer["reply"])
                self.assertIn("combinazione taglia/colore", answer["reply"])
                self.assertNotIn("Cipria", answer["reply"])
                self.assertNotIn("24,90", answer["reply"])
                self.assert_product_link(answer, 1001)

    def test_short_new_name_preserves_previous_price_question(self):
        self.ask("Quanto costa il Reggiseno Aurora?")
        answer = self.ask("E Luna?")
        self.assertIn("Luna", answer["reply"])
        self.assertIn("34,90", answer["reply"])
        self.assertNotIn("24,90", answer["reply"])
        self.assertNotIn("Taglie", answer["reply"])
        self.assert_product_link(answer, 2001)

    def test_unknown_new_name_does_not_reuse_a_previous_product(self):
        self.ask("Quanto costa il Reggiseno Aurora?")
        answer = self.ask("Quanto costa il Pigiama Inesistente?")
        self.assertEqual(answer["status"], "clarification")
        self.assertNotIn("24,90", answer["reply"])
        self.assertNotIn("34,90", answer["reply"])
        self.assertFalse(any("prodotto=" in item["url"] for item in answer["links"]))

    def test_unique_partial_name_overrides_generic_category_alias(self):
        for name, product_id, price in (("Pigiama Orsetto", 4002, "19,90"), ("Body Stella", 4001, "12,90")):
            with self.subTest(name=name):
                answer = self.ask(f"Quanto costa il {name}?")
                self.assertIn(price, answer["reply"])
                self.assertNotIn("Pigiama Luna", answer["reply"])
                self.assertNotIn("Reggiseno Aurora", answer["reply"])
                self.assert_product_link(answer, product_id)

    def test_implicit_size_is_not_mistaken_for_general_stock(self):
        answer = self.ask("Avete il Pigiama Luna in XXL?")
        self.assertIn("XXL non è indicata", answer["reply"])
        self.assertIn("combinazione taglia/colore", answer["reply"])
        self.assertNotIn("catalogo indica: disponibile", answer["reply"])
        self.assert_product_link(answer, 2001)
        answer = self.ask("Avete il Pigiama Bimbo Orsetto in 4 anni?")
        self.assertIn("4 anni è indicata", answer["reply"])
        self.assert_product_link(answer, 4002)

    def test_unavailable_product_does_not_promise_a_reservation(self):
        answer = self.ask("Avete Vestaglia Nuvola?")
        self.assertIn("Nuvola", answer["reply"])
        self.assertIn("non disponibile", answer["reply"].lower())
        self.assertNotIn("prenotat", answer["reply"].lower())
        self.assertNotIn("39,90", answer["reply"])
        self.assertNotIn("2003", answer["reply"])
        self.assert_no_sheet(answer["reply"])
        self.assert_product_link(answer, 2003)

    def test_broad_search_asks_to_choose_by_name_without_dumping_sheets(self):
        answer = self.ask("Cerco un pigiama")
        self.assertEqual(answer["status"], "clarification")
        self.assertIn("Pigiama Luna", answer["reply"])
        self.assertIn("Pigiama Brezza", answer["reply"])
        self.assertNotIn("Pigiama Bimbo Orsetto", answer["reply"])
        self.assertRegex(answer["reply"].lower(), r"quale|scegli|sceglier|preferisci")
        self.assertNotRegex(answer["reply"], r"\bID\b")
        self.assert_no_sheet(answer["reply"])
        self.assertEqual(answer["preferences"]["category_counts"], {"2": 1})
        self.assertEqual(answer["preferences"]["product_counts"], {})

    def test_price_of_unspecified_pigiama_needs_clarification(self):
        answer = self.ask("Quanto costa pigiama?")
        self.assertEqual(answer["status"], "clarification")
        self.assertRegex(answer["reply"].lower(), r"quale|scegli|sceglier|preferisci")
        self.assertNotRegex(answer["reply"], r"\d+[,.]\d{2}\s*€")
        self.assertNotRegex(answer["reply"], r"\bID\b")
        self.assertEqual(answer["preferences"]["product_counts"], {})

    def test_vague_memory_of_yesterday_does_not_select_an_arbitrary_product(self):
        answer = self.ask("Vorrei un pigiama come quello che ho visto ieri")
        self.assertEqual(answer["status"], "clarification")
        self.assertRegex(answer["reply"].lower(), r"quale|nome|dettagl|descriv|ricord|scegli|preferisci")
        self.assertNotRegex(answer["reply"], r"\bID\b")
        self.assertNotRegex(answer["reply"], r"\d+[,.]\d{2}\s*€")
        self.assertEqual(answer["preferences"]["product_counts"], {})

    def test_unknown_product_name_never_returns_an_existing_products_price(self):
        answer = self.ask("Quanto costa il Pigiama Inesistente?")
        self.assertIn(answer["status"], {"clarification", "not_found"})
        self.assertRegex(answer["reply"].lower(), r"non trov|non (?:è )?present|non risulta|nessun|non riconosc")
        for wrong_price in ("34,90", "29,90", "19,90"):
            self.assertNotIn(wrong_price, answer["reply"])
        self.assertEqual(answer["preferences"]["product_counts"], {})
        self.assertFalse(any("prodotto=" in link["url"] for link in answer["links"]))

    def test_human_and_preferences_routes_keep_recording_rules(self):
        initial = self.ask("Quanto costa il Reggiseno Aurora?")["preferences"]
        preferences = self.ask("Quali sono le mie preferenze?")
        self.assertIn("Intimo", preferences["reply"])
        self.assertEqual(preferences["preferences"], initial)
        self.assertTrue(preferences["saved"])
        human = self.ask("Vorrei un operatore")
        self.assertEqual(human["status"], "human_required")
        self.assertIn("Contatti", human["reply"])
        self.assertEqual(human["preferences"], initial)
        self.assertTrue(human["saved"])

    def test_ollama_offline_still_answers_the_specific_price_and_saves(self):
        self.llm.fail = True
        answer = self.ask("Quanto costa il Reggiseno Aurora?")
        self.assertFalse(answer["llm_available"])
        self.assertEqual(answer["status"], "degraded")
        self.assertIn("Aurora", answer["reply"])
        self.assertIn("24,90", answer["reply"])
        self.assertNotIn("Taglie:", answer["reply"])
        self.assertNotIn("1001", answer["reply"])
        self.assert_no_sheet(answer["reply"])
        self.assert_product_link(answer, 1001)
        self.assertTrue(answer["saved"])
        self.assertEqual(answer["preferences"]["product_counts"], {"1001": 1})


if __name__ == "__main__":
    unittest.main()
