"""Casi di memoria cliente isolati: tutti i dati restano in directory temporanee."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.customer_store import CustomerStore, CustomerStoreError, validate_identity


class CustomerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "runtime" / "customers.json"
        self.store = CustomerStore(self.path)

    def create(self, remember=True):
        return self.store.create_session("Cliente Test", "cliente@example.test", remember)

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_identity_normalization_and_validation(self):
        self.assertEqual(validate_identity("  Ada   Test  ", " ADA@EXAMPLE.TEST "),
                         ("Ada Test", "ada@example.test"))
        for name, email in (("", "a@example.test"), (" " * 5, "a@example.test"),
                            ("a" * 81, "a@example.test"), (None, "a@example.test"),
                            ("Ada", "invalid"), ("Ada", "a@b"),
                            ("Ada", "a b@example.test"), ("Ada", None),
                            ("Ada", "a" * 250 + "@example.test")):
            with self.subTest(name=name, email=email), self.assertRaises(ValueError):
                validate_identity(name, email)
        for value in (1, 0, "true", None):
            with self.subTest(remember=value), self.assertRaises(ValueError):
                self.store.create_session("Ada", "a@example.test", value)

    def test_consent_persists_questions_and_preferences_across_restart(self):
        token, customer = self.create()
        result = self.store.record_question(token, "req-1", "Vorrei informazioni su intimo", [1], [])
        self.assertTrue(result["saved"])
        self.assertFalse(result["duplicate"])
        restarted = CustomerStore(self.path)
        self.assertEqual(restarted.get_customer(token), customer)
        self.assertEqual(restarted.get_preferences(token),
                         {"category_counts": {"1": 1}, "product_counts": {}})
        stored = self.read()["customers"][customer["id"]]
        self.assertEqual(stored["questions"][0]["question"], "Vorrei informazioni su intimo")
        self.assertEqual(stored["questions"][0]["request_id"], "req-1")
        self.assertTrue(stored["questions"][0]["timestamp"].endswith("Z"))

    def test_raw_token_is_never_written(self):
        token, customer = self.create()
        content = self.path.read_text(encoding="utf-8")
        self.assertNotIn(token, content)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.assertEqual(self.read()["sessions"], {token_hash: customer["id"]})

    def test_same_email_does_not_restore_or_merge_profiles(self):
        token_a, customer_a = self.create()
        token_b, customer_b = self.create()
        self.assertNotEqual(token_a, token_b)
        self.assertNotEqual(customer_a["id"], customer_b["id"])
        self.store.record_question(token_a, "req-1", "Intimo", [1], [])
        self.assertEqual(self.store.get_preferences(token_b),
                         {"category_counts": {}, "product_counts": {}})
        self.assertEqual(len(self.read()["customers"]), 2)

    def test_optout_never_creates_a_file_and_is_lost_on_restart(self):
        token, customer = self.create(remember=False)
        self.assertEqual(self.store.get_customer(token), customer)
        self.assertFalse(self.path.exists())
        result = self.store.record_question(token, "req-1", "Intimo", [1], [1001])
        self.assertEqual(result, {"saved": False, "duplicate": False,
                                 "preferences": {"category_counts": {}, "product_counts": {}}})
        self.assertFalse(self.path.parent.exists())
        restarted = CustomerStore(self.path)
        self.assertIsNone(restarted.get_customer(token))
        self.assertEqual(restarted.get_preferences(token), {})

    def test_optout_does_not_modify_existing_persistent_profiles(self):
        self.create()
        before = self.path.read_bytes()
        token, _ = self.create(remember=False)
        self.store.record_question(token, "req-1", "Intimo", [1], [])
        self.store.get_preferences(token)
        self.store.forget(token)
        self.assertEqual(self.path.read_bytes(), before)

    def test_question_retry_is_idempotent_per_customer(self):
        token, customer = self.create()
        first = self.store.record_question(token, "req-1", "Intimo", [1, 1], [1001, 1001])
        before = self.path.read_bytes()
        second = self.store.record_question(token, "req-1", "Intimo", [2], [2001])
        self.assertEqual(first["preferences"], second["preferences"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.read()["customers"][customer["id"]]["questions"]), 1)
        # Lo stesso request_id di un cliente diverso rimane indipendente.
        other_token, _ = self.create()
        self.assertFalse(self.store.record_question(other_token, "req-1", "Intimo", [1], [])["duplicate"])

    def test_request_id_reuse_for_different_question_is_rejected_without_changes(self):
        for remember in (False, True):
            token, _ = self.create(remember=remember)
            self.store.record_question(token, "req-1", "Intimo", [1], [])
            before = self.path.read_bytes() if self.path.exists() else None
            with self.subTest(remember=remember), self.assertRaises(ValueError):
                self.store.record_question(token, "req-1", "Pigiameria", [2], [])
            self.assertEqual(self.path.read_bytes() if self.path.exists() else None, before)

    def test_optout_retries_are_deduplicated_without_persisting_questions(self):
        token, _ = self.create(remember=False)
        self.store.record_question(token, "req-1", "Intimo", [1], [])
        self.assertTrue(self.store.record_question(token, "req-1", "Intimo", [1], [])["duplicate"])
        self.assertFalse(self.path.exists())

    def test_preferences_count_each_unique_id_once_per_question(self):
        token, _ = self.create()
        self.store.record_question(token, "req-1", "Prima domanda", [2, 1, 2], [1001, 1001])
        result = self.store.record_question(token, "req-2", "Seconda domanda", [2], [1001, 2001])
        self.assertEqual(result["preferences"], {
            "category_counts": {"1": 1, "2": 2}, "product_counts": {"1001": 2, "2001": 1},
        })

    def test_question_with_no_explicit_interests_is_stored_without_inferred_counts(self):
        token, customer = self.create()
        result = self.store.record_question(token, "req-1", "Non mi interessa l'intimo", [], [])
        self.assertEqual(result["preferences"], {"category_counts": {}, "product_counts": {}})
        self.assertEqual(len(self.read()["customers"][customer["id"]]["questions"]), 1)

    def test_bad_ids_questions_and_unknown_sessions_do_not_modify_file(self):
        token, _ = self.create()
        before = self.path.read_bytes()
        for category_ids, product_ids in (([True], []), ([0], []), ([-1], []),
                                          (["1"], []), ([1.2], []), (None, []),
                                          ([], [False]), ([], "1001")):
            with self.subTest(categories=category_ids, products=product_ids), self.assertRaises(ValueError):
                self.store.record_question(token, "req-1", "Domanda", category_ids, product_ids)
        for request_id, question in (("", "Domanda"), ("a" * 129, "Domanda"),
                                     ("req-1", " "), ("req-1", "a" * 4001)):
            with self.subTest(request=request_id), self.assertRaises(ValueError):
                self.store.record_question(token, request_id, question, [], [])
        with self.assertRaises(ValueError):
            self.store.record_question("unknown-token", "req-1", "Domanda", [], [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNone(self.store.get_customer("unknown-token"))
        self.assertEqual(self.store.get_preferences("unknown-token"), {})

    def test_public_results_are_copies_and_do_not_expose_questions_or_token_hash(self):
        for remember in (False, True):
            token, customer = self.create(remember=remember)
            self.assertEqual(set(customer), {"id", "name", "email", "remember"})
            customer["name"] = "MODIFICATO"
            self.assertEqual(self.store.get_customer(token)["name"], "Cliente Test")
            preferences = self.store.get_preferences(token)
            preferences["category_counts"]["1"] = 999
            self.assertEqual(self.store.get_preferences(token)["category_counts"], {})

    def test_forget_removes_only_the_associated_customer_and_session(self):
        token_a, customer_a = self.create()
        token_b, customer_b = self.create()
        self.store.record_question(token_a, "req-1", "Intimo", [1], [])
        self.store.forget(token_a)
        self.assertIsNone(self.store.get_customer(token_a))
        self.assertEqual(self.store.get_customer(token_b), customer_b)
        self.assertNotIn(customer_a["id"], self.read()["customers"])
        self.assertEqual(len(self.read()["sessions"]), 1)
        self.assertIsNone(CustomerStore(self.path).get_customer(token_a))
        self.store.forget("unknown-token")

    def test_forget_volatile_session_never_creates_a_file(self):
        token, _ = self.create(remember=False)
        self.store.forget(token)
        self.assertIsNone(self.store.get_customer(token))
        self.assertFalse(self.path.exists())

    def test_malformed_or_inconsistent_json_is_not_overwritten(self):
        for invalid in ('{"customers":', '{"schema_version": NaN}', '{}',
                        '{"schema_version":1,"schema_version":1,"customers":{},"sessions":{}}'):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(invalid, encoding="utf-8")
            before = self.path.read_bytes()
            with self.subTest(content=invalid), self.assertRaises(CustomerStoreError):
                self.create()
            self.assertEqual(self.path.read_bytes(), before)
            with self.assertRaises(CustomerStoreError):
                CustomerStore(self.path)

    def test_inconsistent_preference_counts_fail_closed(self):
        token, customer = self.create()
        document = self.read()
        document["customers"][customer["id"]]["preferences"]["category_counts"] = {"1": 9}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(CustomerStoreError):
            self.store.record_question(token, "req-1", "Intimo", [1], [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_atomic_replace_preserves_previous_file_and_cleans_temporary(self):
        token, _ = self.create()
        before = self.path.read_bytes()
        with patch("backend.customer_store.os.replace", side_effect=PermissionError("private-path")):
            with self.assertRaises(CustomerStoreError) as caught:
                self.store.record_question(token, "req-1", "Intimo", [1], [])
        self.assertNotIn("private-path", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.assertEqual(self.store.get_preferences(token), {"category_counts": {}, "product_counts": {}})

    def test_read_failure_is_explicit_and_does_not_become_an_empty_archive(self):
        token, _ = self.create()
        with patch.object(Path, "read_text", side_effect=PermissionError("private-path")):
            with self.assertRaises(CustomerStoreError) as caught:
                self.store.get_customer(token)
        self.assertNotIn("private-path", str(caught.exception))

    def test_threads_and_store_instances_do_not_lose_questions(self):
        token, _ = self.create()
        other_store = CustomerStore(self.path)

        def record(index):
            store = self.store if index % 2 == 0 else other_store
            return store.record_question(token, f"req-{index}", "Intimo", [1], [])

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(record, range(12)))
        self.assertTrue(all(result["saved"] for result in results))
        self.assertEqual(self.store.get_preferences(token)["category_counts"], {"1": 12})


if __name__ == "__main__":
    unittest.main()
