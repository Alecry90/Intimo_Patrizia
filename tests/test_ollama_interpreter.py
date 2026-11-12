"""Trasporto Ollama simulato: JSON, errori e budget dei retry senza rete."""

import json
import os
import unittest
from unittest.mock import patch

import httpx

from backend.ollama_interpreter import OllamaInterpreter, REQUEST_SCHEMA
from backend.product_agent import ProductAgent
from backend.question_answering import answer_products


class OllamaInterpreterTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "OLLAMA_MODEL": "catalogue-test:7b",
            "OLLAMA_BASE_URL": "http://ollama.test",
            "OLLAMA_TIMEOUT": "30",
            "OLLAMA_KEEP_ALIVE": "30m",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.interpreter = OllamaInterpreter()
        # Chiude il pool originale prima di sostituirlo: nessun socket reale
        # viene usato e il test mantiene lo stesso ciclo di vita del client.
        self.interpreter.client.close()
        self.interpreter.client = httpx.Client(
            base_url="http://ollama.test", transport=httpx.MockTransport(self.dispatch),
        )
        self.addCleanup(self.interpreter.close)
        self.requests = []
        self.handler = lambda request: self.valid_response()
        self.document = {
            "intent": "product", "category_ids": [1], "product_ids": [],
            "use_previous": False, "category_evidence": {"1": "reggiseno"},
            "product_query": "reggiseno Aurora", "requested_fields": ["price_eur"],
            "field_evidence": {"price_eur": "quanto costa"}, "requests": [],
        }
        self.question = "Ciao, quanto costa il reggiseno Aurora?"
        self.categories = [{"id": 1, "name": "Intimo"}]

    def dispatch(self, request):
        self.requests.append(request)
        return self.handler(request)

    def valid_response(self):
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": json.dumps(self.document)},
            "done": True, "done_reason": "stop",
        })

    def interpret(self):
        return self.interpreter.interpret(self.question, self.categories)

    def test_valid_json_preserves_configured_model_and_uses_constrained_request(self):
        self.assertEqual(self.interpret(), self.document)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/api/chat")
        payload = json.loads(request.content)
        self.assertEqual(payload["model"], "catalogue-test:7b")
        self.assertEqual(self.interpreter.model, "catalogue-test:7b")
        self.assertEqual(payload["keep_alive"], "30m")
        self.assertEqual(payload["format"], REQUEST_SCHEMA)
        self.assertIs(payload["think"], False)
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["messages"][-1], {"role": "user", "content": self.question})

    def test_compact_single_request_expands_into_verified_interpretation(self):
        self.document = {
            "intent": "product", "use_previous": False, "requests": [{
                "text": "quanto costa il reggiseno Aurora?", "query": "reggiseno Aurora",
                "fields": ["price_eur"], "category": 1,
            }],
        }
        answer = self.interpret()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(answer["intent"], "product")
        self.assertEqual(answer["product_query"], "reggiseno Aurora")
        self.assertEqual(answer["product_ids"], [])
        self.assertEqual(answer["category_ids"], [1])
        self.assertEqual(answer["requested_fields"], ["price_eur"])
        self.assertIn(answer["category_evidence"]["1"], self.question)
        self.assertIn(answer["field_evidence"]["price_eur"], self.question)
        self.assertEqual(answer["requests"][0]["text"], self.document["requests"][0]["text"])

    def test_compact_double_request_keeps_fields_with_their_product_question(self):
        self.question = "Quanto costa Aurora e che taglie ha Luna?"
        self.categories = [{"id": 1, "name": "Intimo"}, {"id": 2, "name": "Pigiameria"}]
        self.document = {
            "intent": "product", "use_previous": False, "requests": [
                {"text": "Quanto costa Aurora", "query": "Aurora", "fields": ["price_eur"], "category": 1},
                {"text": "che taglie ha Luna?", "query": "Luna", "fields": ["sizes"], "category": 2},
            ],
        }
        answer = self.interpret()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(answer["category_ids"], [1, 2])
        self.assertEqual(answer["product_ids"], [])
        self.assertEqual(set(answer["requested_fields"]), {"price_eur", "sizes"})
        scoped = {item["text"]: item["requested_fields"] for item in answer["requests"]}
        self.assertEqual(scoped, {"Quanto costa Aurora": ["price_eur"], "che taglie ha Luna?": ["sizes"]})
        for item in answer["requests"]:
            self.assertIn(item["text"], self.question)
            for evidence in item["field_evidence"].values():
                self.assertIn(evidence, item["text"])

    def test_nonliteral_query_is_discarded_without_losing_valid_question(self):
        text = "quanto costa il reggiseno Aurora?"
        self.document = {
            "intent": "product", "use_previous": False, "requests": [{
                "text": text, "query": "Reggiseno Aurora senza ferretto",
                "fields": ["price_eur"], "category": 0,
            }],
        }
        answer = self.interpret()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(answer["product_query"], "")
        self.assertEqual(answer["requests"][0]["text"], text)
        self.assertEqual(answer["requests"][0]["requested_fields"], ["price_eur"])
        self.assertEqual(answer["category_ids"], [])

    def test_invented_request_text_is_rejected_after_at_most_two_attempts(self):
        self.document = {
            "intent": "product", "use_previous": False, "requests": [{
                "text": "Quanto costa il Pigiama Luna?", "query": "Pigiama Luna",
                "fields": ["price_eur"], "category": 0,
            }],
        }
        with self.assertRaises(ValueError):
            self.interpret()
        self.assertEqual(len(self.requests), 2)

    def test_http_200_array_payload_is_retried_then_valid_compact_json_is_used(self):
        self.document = {"intent": "product", "use_previous": False, "requests": [{
            "text": "quanto costa il reggiseno Aurora?", "query": "reggiseno Aurora",
            "fields": ["price_eur"], "category": 1,
        }]}

        def respond(request):
            return httpx.Response(200, json=[]) if len(self.requests) == 1 else self.valid_response()

        self.handler = respond
        answer = self.interpret()
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(answer["product_query"], "reggiseno Aurora")
        self.assertEqual(answer["requested_fields"], ["price_eur"])

    def test_invalid_json_is_retried_once_and_valid_answer_is_returned(self):
        def respond(request):
            if len(self.requests) == 1:
                return httpx.Response(200, json={"message": {"content": "{"}})
            return self.valid_response()

        self.handler = respond
        self.assertEqual(self.interpret(), self.document)
        self.assertEqual(len(self.requests), 2)
        payloads = [json.loads(request.content) for request in self.requests]
        self.assertTrue(all(payload["model"] == "catalogue-test:7b" for payload in payloads))
        self.assertTrue(all(payload["messages"][-1]["content"] == self.question for payload in payloads))

    def test_truncated_json_response_is_retried_before_use(self):
        def respond(request):
            if len(self.requests) == 1:
                return httpx.Response(200, json={
                    "message": {"content": json.dumps(self.document)}, "done_reason": "length",
                })
            return self.valid_response()

        self.handler = respond
        self.assertEqual(self.interpret(), self.document)
        self.assertEqual(len(self.requests), 2)

    def test_missing_model_http_404_is_not_retried(self):
        self.handler = lambda request: httpx.Response(404, json={"error": "model not found"})
        with self.assertRaises(httpx.HTTPStatusError) as raised:
            self.interpret()
        self.assertEqual(raised.exception.response.status_code, 404)
        self.assertEqual(len(self.requests), 1)

    def test_two_connection_errors_end_after_two_attempts(self):
        def fail(request):
            raise httpx.ConnectError("Simulated local connection failure", request=request)

        self.handler = fail
        with self.assertRaises(httpx.ConnectError):
            self.interpret()
        self.assertEqual(len(self.requests), 2)

    def test_retry_receives_remaining_budget_instead_of_second_full_timeout(self):
        def respond(request):
            if len(self.requests) == 1:
                raise httpx.ReadTimeout("Simulated elapsed time", request=request)
            return self.valid_response()

        self.handler = respond
        # Prima chiamata a t=100, errore a t=112: il secondo tentativo ha
        # ancora 18 secondi, non un altro timeout completo da 30 secondi.
        with patch("backend.ollama_interpreter.time.monotonic", side_effect=[100, 100, 112, 112]):
            self.assertEqual(self.interpret(), self.document)
        self.assertEqual(len(self.requests), 2)
        first_timeout, retry_timeout = [request.extensions["timeout"] for request in self.requests]
        self.assertEqual(first_timeout["read"], 30)
        self.assertEqual(retry_timeout["read"], 18)
        self.assertEqual(retry_timeout["write"], 18)
        self.assertLessEqual(retry_timeout["connect"], 2)

    def test_insufficient_remaining_budget_does_not_start_another_attempt(self):
        def fail(request):
            raise httpx.ReadTimeout("Simulated nearly exhausted budget", request=request)

        self.handler = fail
        with patch("backend.ollama_interpreter.time.monotonic", side_effect=[100, 100, 127]):
            with self.assertRaises(httpx.ReadTimeout):
                self.interpret()
        self.assertEqual(len(self.requests), 1)

    def test_warmup_connection_and_http_errors_do_not_propagate(self):
        def fail(request):
            raise httpx.ConnectError("Simulated warmup failure", request=request)

        for handler in (fail, lambda request: httpx.Response(500, json={"error": "busy"})):
            with self.subTest(handler=handler):
                self.requests.clear()
                self.handler = handler
                self.assertIsNone(self.interpreter.warmup())
                self.assertEqual(len(self.requests), 1)
                request = self.requests[0]
                self.assertEqual(request.url.path, "/api/generate")
                payload = json.loads(request.content)
                self.assertEqual(payload["model"], "catalogue-test:7b")
                self.assertEqual(payload["prompt"], "")
                self.assertEqual(payload["keep_alive"], "30m")


class NaturalSizeQuestionTests(unittest.TestCase):
    def test_che_taglia_ha_does_not_treat_verb_ha_as_requested_size(self):
        product = ProductAgent().get_product(1001)
        self.assertEqual(product["status"], "ok")
        answer = answer_products("Che taglia ha Aurora?", [product["data"]], ["sizes"])
        self.assertEqual(answer["status"], "ok")
        self.assertIn("Aurora", answer["reply"])
        for size in ("2", "3", "4", "5"):
            self.assertRegex(answer["reply"], rf"\b{size}\b")
        self.assertNotRegex(answer["reply"], r"\bHA\b")
        self.assertNotIn("non è indicata", answer["reply"])


if __name__ == "__main__":
    unittest.main()
