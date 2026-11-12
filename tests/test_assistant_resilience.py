"""Regressioni per risposte utili anche quando l'interprete locale non risponde.

Il grafo, il catalogo demo e le API sono reali. L'interprete è sostituito e
la sessione usa il consenso disattivato con uno store temporaneo: nessuna
domanda di prova finisce nell'archivio dei clienti.
"""

import json
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.customer_store import CustomerStore
from backend.product_agent import ProductAgent
from backend.server import create_app


class FakeInterpreter:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.result = {
            "intent": "product", "category_ids": [], "product_ids": [],
            "use_previous": False, "category_evidence": {},
            "product_query": "", "requested_fields": [], "field_evidence": {},
        }

    def interpret(self, question, categories):
        self.calls.append((question, categories))
        if self.failure is not None:
            raise self.failure
        return self.result


class AssistantResilienceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = CustomerStore(Path(temporary.name) / "customers.json")
        self.products = ProductAgent()
        self.interpreter = FakeInterpreter()
        self.client = TestClient(create_app(self.store, self.interpreter, self.products))
        self.addCleanup(self.client.close)
        response = self.client.post("/api/session", json={
            "name": "Cliente regressione", "email": "regressione@example.invalid",
            "remember": False,
        })
        self.assertEqual(response.status_code, 200, response.text)

    def ask(self, question, request_id=None):
        response = self.client.post("/api/chat", json={
            "message": question, "request_id": request_id or str(uuid4()),
        })
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()
        self.assertIsInstance(answer["reply"], str)
        self.assertTrue(answer["reply"].strip())
        self.assertFalse(answer["saved"])
        self.assertFalse(self.store.path.exists())
        self.assertNotRegex(answer["reply"].casefold(), r"ollama|\bllm\b|timeout|traceback")
        return answer

    def linked_products(self, answer):
        ids = set()
        for link in answer["links"]:
            target = urlsplit(link["url"])
            self.assertFalse(target.scheme)
            self.assertFalse(target.netloc)
            self.assertEqual(target.path, "/catalogo")
            query = parse_qs(target.query)
            if "prodotto" not in query:
                continue
            product_id = int(query["prodotto"][0])
            product = self.products.get_product(product_id)
            self.assertEqual(product["status"], "ok")
            self.assertEqual(product["data"]["category_id"], int(query["categoria"][0]))
            ids.add(product_id)
        return ids

    def assert_precise_aurora_price(self, answer):
        self.assertEqual(answer["status"], "ok")
        self.assertIn("Aurora", answer["reply"])
        self.assertIn("24,90", answer["reply"])
        self.assertIn("€", answer["reply"])
        self.assertNotIn("34,90", answer["reply"])
        self.assertNotRegex(answer["reply"].casefold(), r"taglie|composizione")
        self.assertEqual(self.linked_products(answer), {1001})

    def test_greeting_with_clear_price_still_consults_ollama(self):
        question = "Ciao, quanto costa il reggiseno Aurora?"
        answer = self.ask(question)
        self.assert_precise_aurora_price(answer)
        self.assertTrue(answer["llm_available"])
        self.assertEqual([call[0] for call in self.interpreter.calls], [question])

    def test_unreachable_timeout_and_invalid_json_keep_precise_price(self):
        for failure in (
            ConnectionError("private Ollama address"),
            TimeoutError("private timeout details"),
            json.JSONDecodeError("private invalid output", "{", 1),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.interpreter.failure = failure
                answer = self.ask("Ciao, quanto costa il reggiseno Aurora?")
                self.assert_precise_aurora_price(answer)
                self.assertFalse(answer["llm_available"])
                self.assertNotIn("private", answer["reply"])
        self.assertEqual(len(self.interpreter.calls), 3)

    def test_malformed_interpretation_keeps_customer_answer_semantic(self):
        for result in ("{", [], {"intent": "product", "category_ids": [True]},
                       {"intent": "invented", "category_ids": [], "product_ids": []}):
            with self.subTest(result=result):
                self.interpreter.result = result
                answer = self.ask("Quanto costa Aurora?")
                self.assert_precise_aurora_price(answer)
                self.assertFalse(answer["llm_available"])

    def test_failure_with_unspecified_product_still_asks_clarification(self):
        self.interpreter.failure = ConnectionError("offline")
        answer = self.ask("Quanto costa il pigiama?")
        self.assertEqual(answer["status"], "clarification")
        self.assertFalse(answer["llm_available"])
        self.assertRegex(answer["reply"].casefold(), r"quale|scegli|preferisci")
        self.assertNotRegex(answer["reply"], r"\d+[,.]\d{2}\s*€")

    def test_simple_greeting_is_welcomed_without_unknown_product_error(self):
        answer = self.ask("Ciao!")
        self.assertRegex(answer["reply"].casefold(), r"ciao|benvenut|buongiorno|salve")
        self.assertNotRegex(answer["reply"].casefold(), r"non trovo|non riconosc|inesistent")
        self.assertEqual(self.linked_products(answer), set())

    def test_thanks_after_product_question_does_not_repeat_product_answer(self):
        self.ask("Quanto costa Aurora?")
        answer = self.ask("Grazie mille!")
        self.assertRegex(answer["reply"].casefold(), r"prego|volentieri|piacere|figurati|grazie")
        self.assertNotRegex(answer["reply"].casefold(), r"non trovo|non riconosc|inesistent")
        self.assertNotIn("24,90", answer["reply"])
        self.assertEqual(self.linked_products(answer), set())

    def test_multiple_full_names_answer_their_own_questions(self):
        answer = self.ask("Quanto costa il Reggiseno Aurora e che taglie ha il Pigiama Luna?")
        self.assert_scoped_price_and_sizes(answer)

    def test_multiple_short_names_answer_their_own_questions(self):
        answer = self.ask("Quanto costa Aurora e che taglie ha Luna?")
        self.assert_scoped_price_and_sizes(answer)

    def test_multiple_questions_are_scoped_even_during_ollama_failure(self):
        self.interpreter.failure = TimeoutError("offline")
        answer = self.ask("Quanto costa Aurora e che taglie ha Luna?")
        self.assert_scoped_price_and_sizes(answer)
        self.assertFalse(answer["llm_available"])

    def test_shared_price_question_answers_both_products(self):
        answer = self.ask("Quanto costano Aurora e Luna?")
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(self.linked_products(answer), {1001, 2001})
        self.assertIn("24,90", answer["reply"])
        self.assertIn("34,90", answer["reply"])
        self.assertNotRegex(answer["reply"].casefold(), r"taglie|composizione")

    def test_model_category_cannot_override_verified_categories_of_named_products(self):
        # Solo questo caso attiva il salvataggio nello store temporaneo per
        # controllare che una categoria inventata non diventi una preferenza.
        self.assertEqual(self.client.delete("/api/session").status_code, 200)
        response = self.client.post("/api/session", json={
            "name": "Preferenze prova", "email": "preferenze@example.invalid", "remember": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.interpreter.result.update(category_ids=[3], category_evidence={"3": "Aurora"})
        response = self.client.post("/api/chat", json={
            "message": "Quanto costa Aurora e che taglie ha Luna?", "request_id": str(uuid4()),
        })
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()
        self.assert_scoped_price_and_sizes(answer)
        self.assertTrue(answer["llm_available"])
        self.assertTrue(answer["saved"])
        self.assertEqual(answer["preferences"]["category_counts"], {"1": 1, "2": 1})
        self.assertEqual(answer["preferences"]["product_counts"], {"1001": 1, "2001": 1})
        for link in answer["links"]:
            query = parse_qs(urlsplit(link["url"]).query)
            self.assertNotIn("3", query.get("categoria", []))

    def assert_scoped_price_and_sizes(self, answer):
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(self.linked_products(answer), {1001, 2001})
        text = answer["reply"]
        self.assertIn("Reggiseno Aurora", text)
        self.assertIn("Pigiama Luna", text)
        first, second = text.index("Reggiseno Aurora"), text.index("Pigiama Luna")
        self.assertLess(first, second)
        aurora, luna = text[first:second], text[second:]
        self.assertIn("24,90", aurora)
        self.assertNotRegex(aurora, r"\b(?:S|M|L|XL)\b")
        self.assertNotIn("34,90", text)
        for size in ("S", "M", "L", "XL"):
            self.assertRegex(luna, rf"\b{re.escape(size)}\b")

    def test_price_colors_and_material_for_same_product_are_all_answered(self):
        answer = self.ask("Quanto costa Aurora, in quali colori c'è e di che materiale è?")
        self.assertEqual(answer["status"], "ok")
        self.assertIn("24,90", answer["reply"])
        for fact in ("Bianco", "Nero", "Cipria", "95% cotone", "5% elastan"):
            self.assertIn(fact, answer["reply"])
        self.assertNotRegex(answer["reply"].casefold(), r"taglie")
        self.assertEqual(self.linked_products(answer), {1001})

    def test_followup_uses_product_context_for_new_field(self):
        self.ask("Quanto costa Aurora?")
        answer = self.ask("E in quali colori?")
        self.assertEqual(answer["status"], "ok")
        self.assertIn("Aurora", answer["reply"])
        self.assertIn("Cipria", answer["reply"])
        self.assertNotIn("24,90", answer["reply"])
        self.assertEqual(self.linked_products(answer), {1001})

    def test_short_new_product_keeps_previous_price_question(self):
        self.ask("Quanto costa Aurora?")
        answer = self.ask("E Luna?")
        self.assertEqual(answer["status"], "ok")
        self.assertIn("Luna", answer["reply"])
        self.assertIn("34,90", answer["reply"])
        self.assertNotIn("24,90", answer["reply"])
        self.assertEqual(self.linked_products(answer), {2001})

    def test_unknown_new_product_does_not_inherit_old_price(self):
        self.ask("Quanto costa Aurora?")
        answer = self.ask("Quanto costa il Pigiama Inesistente?")
        self.assertEqual(answer["status"], "clarification")
        self.assertNotRegex(answer["reply"], r"\d+[,.]\d{2}\s*€")
        self.assertEqual(self.linked_products(answer), set())

    def test_followup_after_unknown_product_does_not_resurrect_old_price(self):
        self.ask("Quanto costa Aurora?")
        self.ask("Quanto costa il Pigiama Inesistente?")
        answer = self.ask("E quanto costa?")
        self.assertEqual(answer["status"], "clarification")
        self.assertNotRegex(answer["reply"], r"\d+[,.]\d{2}\s*€")
        self.assertNotIn(1001, self.linked_products(answer))

    def test_retry_is_idempotent_and_does_not_repeat_model_call(self):
        request_id = str(uuid4())
        first = self.ask("Quanto costa Aurora?", request_id)
        second = self.ask("Quanto costa Aurora?", request_id)
        self.assertEqual(first, second)
        self.assertEqual(len(self.interpreter.calls), 1)


if __name__ == "__main__":
    unittest.main()
