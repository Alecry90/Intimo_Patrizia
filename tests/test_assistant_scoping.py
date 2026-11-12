"""Scoping delle domande e cancellazione concorrente, senza modelli o dati reali."""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from backend.chat_graph import ChatService
from backend.customer_store import CustomerStore
from backend.question_answering import answer_products


class Interpreter:
    def __init__(self):
        self.requests = []

    def interpret(self, question, categories):
        return {"intent": "product", "category_ids": [], "product_ids": [],
                "use_previous": False, "category_evidence": {}, "product_query": "",
                "requested_fields": [], "field_evidence": {}, "requests": self.requests}


class AssistantScopingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = CustomerStore(Path(temporary.name) / "customers.json")
        self.token, self.customer = self.store.create_session("Prova", "prova@example.invalid", False)
        self.interpreter = Interpreter()
        self.service = ChatService(self.store, interpreter=self.interpreter)

    def ask(self, question):
        answer = self.service.chat(self.token, question, str(uuid4()))
        self.assertFalse(answer["saved"])
        self.assertFalse(self.store.path.exists())
        return answer

    @staticmethod
    def product_ids(answer):
        return {int(query["prodotto"][0]) for link in answer["links"]
                if "prodotto" in (query := parse_qs(urlsplit(link["url"]).query))}

    def test_duplicate_model_requests_do_not_erase_another_question(self):
        clause = {"text": "Quanto costa Aurora", "requested_fields": ["price_eur"],
                  "field_evidence": {"price_eur": "costa"}}
        self.interpreter.requests = [clause, dict(clause)]
        answer = self.ask("Quanto costa Aurora e quali taglie ha Luna?")
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(self.product_ids(answer), {1001, 2001})
        self.assertIn("24,90", answer["reply"])
        self.assertIn("S, M, L e XL", answer["reply"])
        self.assertNotIn("34,90", answer["reply"])

    def test_model_refinement_preserves_an_uncovered_tail_question(self):
        self.interpreter.requests = [
            {"text": "Il prezzo di Aurora", "requested_fields": ["price_eur"],
             "field_evidence": {"price_eur": "prezzo"}},
            {"text": "le taglie di Luna", "requested_fields": ["sizes"],
             "field_evidence": {"sizes": "taglie"}},
        ]
        answer = self.ask("Il prezzo di Aurora, le taglie di Luna e i colori di Aurora?")
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(self.product_ids(answer), {1001, 2001})
        self.assertIn("24,90", answer["reply"])
        self.assertIn("S, M, L e XL", answer["reply"])
        self.assertIn("Cipria", answer["reply"])
        self.assertNotIn("34,90", answer["reply"])

    def test_global_model_fields_do_not_leak_between_deterministic_clauses(self):
        self.interpreter.requests = [
            {"text": "Quanto costa Aurora", "requested_fields": ["price_eur", "sizes"],
             "field_evidence": {"price_eur": "Quanto costa Aurora", "sizes": "Quanto costa Aurora"}},
        ]
        answer = self.ask("Quanto costa Aurora e che taglie ha Luna?")
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(self.product_ids(answer), {1001, 2001})
        aurora, luna = answer["reply"].split("Pigiama Luna", 1)
        self.assertIn("24,90", aurora)
        self.assertNotIn("taglie", aurora.casefold())
        self.assertIn("S, M, L e XL", luna)
        self.assertNotIn("34,90", luna)

    def test_unknown_sibling_keeps_the_verified_price_and_requests_clarification(self):
        for question in ("Quanto costano Aurora e Pigiama Inesistente?",
                         "Quanto costano Pigiama Inesistente e Aurora?"):
            with self.subTest(question=question):
                answer = self.ask(question)
                self.assertEqual(answer["status"], "clarification")
                self.assertEqual(self.product_ids(answer), {1001})
                self.assertIn("24,90", answer["reply"])
                self.assertIn("altra richiesta", answer["reply"])
                self.assertNotIn("34,90", answer["reply"])

    def test_color_list_remains_one_product_request(self):
        answer = self.ask("Avete Aurora in bianco e nero?")
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(self.product_ids(answer), {1001})
        self.assertIn("Il colore bianco è indicato", answer["reply"])
        self.assertIn("Il colore nero è indicato", answer["reply"])
        self.assertIn("combinazione taglia/colore", answer["reply"])

    def test_delete_waits_for_the_inflight_answer_before_clearing_memory(self):
        entered = threading.Event()
        release = threading.Event()
        removed = threading.Event()
        forgotten = threading.Event()
        failures = []

        def delayed_answer(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Il test non ha rilasciato la risposta")
            return answer_products(*args, **kwargs)

        def chat():
            try:
                self.ask("Quanto costa Aurora?")
            except BaseException as error:
                failures.append(error)

        def delete():
            try:
                self.store.forget(self.token)
                removed.set()
                self.service.forget_context(self.customer["id"])
                forgotten.set()
            except BaseException as error:
                failures.append(error)

        with patch("backend.chat_graph.answer_products", delayed_answer):
            chat_thread = threading.Thread(target=chat, daemon=True)
            delete_thread = threading.Thread(target=delete, daemon=True)
            chat_thread.start()
            try:
                self.assertTrue(entered.wait(5))
                delete_thread.start()
                self.assertTrue(removed.wait(5))
                self.assertFalse(forgotten.is_set())
            finally:
                release.set()
                chat_thread.join(5)
                if delete_thread.ident is not None:
                    delete_thread.join(5)
        self.assertFalse(chat_thread.is_alive())
        self.assertFalse(delete_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(forgotten.is_set())
        self.assertIsNone(self.store.get_customer(self.token))
        self.assertNotIn(self.customer["id"], self.service.contexts)
        self.assertFalse(any(key[0] == self.customer["id"] for key in self.service.responses))


if __name__ == "__main__":
    unittest.main()
