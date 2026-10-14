"""Catalogo demo e fixture isolate: nessun dato commerciale reale viene usato."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.product_agent import CATALOG_PATH, DETAIL_FIELDS, ProductAgent


class ProductAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "catalog.json"
        self.document = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        # Le fixture restano isolate anche quando verranno aggiunti articoli reali.
        self.document["products"] = []
        for category in self.document["categories"]:
            category.pop("products_file", None)
        self.document.pop("demo_mode", None)
        self.document.pop("demo_notice", None)
        self.agent = ProductAgent(self.path)
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.document), encoding="utf-8")

    def use_category_files(self):
        """Fixture indipendenti dal catalogo demo e compatibili con il manifest."""
        inline = self.document["products"]
        self.document["products"] = []
        for category in self.document["categories"]:
            category["products_file"] = f"categories/category-{category['id']}.json"
            source = self.path.parent / category["products_file"]
            source.parent.mkdir(exist_ok=True)
            source.write_text(json.dumps({
                "schema_version": 1, "category_id": category["id"],
                "products": [p for p in inline if p["category_id"] == category["id"]],
            }), encoding="utf-8")
        self.save()

    def save_category(self, category_id, document):
        category = next(c for c in self.document["categories"] if c["id"] == category_id)
        source = self.path.parent / category["products_file"]
        source.write_text(json.dumps(document), encoding="utf-8")
        return source

    def test_real_catalog_has_expected_categories_and_reserved_ranges(self):
        result = ProductAgent().list_categories()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            [(c["id"], c["name"], c["product_id_min"], c["product_id_max"])
             for c in result["data"]],
            [(1, "Intimo", 1001, 1999), (2, "Pigiameria", 2001, 2999),
             (3, "Biancheria", 3001, 3999), (4, "Bambino", 4001, 4999)],
        )

    def test_reserved_id_does_not_create_a_product(self):
        result = self.agent.get_product(1001)
        self.assertEqual(result["status"], "not_found")
        self.assertIsNone(result["data"])

    def test_all_empty_categories_are_explicit(self):
        for category_id in (1, 2, 3, 4):
            with self.subTest(category_id=category_id):
                result = self.agent.list_products(category_id)
                self.assertEqual(result["status"], "empty")
                self.assertEqual(result["data"], [])

    def test_unknown_category_is_distinct_from_empty_category(self):
        self.assertEqual(self.agent.list_products(99)["status"], "not_found")

    def test_invalid_ids_are_rejected_without_type_coercion(self):
        for value in (True, False, None, "1001", "", 1.5, -1, 0):
            with self.subTest(value=value):
                self.assertEqual(self.agent.get_product(value)["status"], "invalid_input")
                self.assertEqual(self.agent.list_products(value)["status"], "invalid_input")

    def test_missing_fields_stay_missing(self):
        self.document["products"] = [{"id": 1001, "category_id": 1}]
        self.save()
        result = self.agent.get_product(1001)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"], {"id": 1001, "category_id": 1})
        self.assertEqual(result["missing_fields"], list(DETAIL_FIELDS))
        self.assertNotIn("price_eur", result["data"])

    def test_stored_details_are_returned_unchanged_and_filtered_by_category(self):
        fixture = {"id": 2001, "category_id": 2, "name": "SOLO TEST",
                   "price_eur": 0, "availability": None, "colors": ["SOLO TEST"]}
        self.document["products"] = [fixture]
        self.save()
        result = self.agent.get_product(2001)
        self.assertEqual(result["data"], fixture)
        self.assertNotIn("price_eur", result["missing_fields"])
        self.assertIn("availability", result["missing_fields"])
        self.assertEqual(self.agent.list_products(2)["data"], [fixture])
        self.assertEqual(self.agent.list_products(1)["status"], "empty")

    def test_range_boundaries_are_inclusive(self):
        for category in self.document["categories"]:
            for product_id in (category["product_id_min"], category["product_id_max"]):
                self.document["products"] = [{"id": product_id, "category_id": category["id"]}]
                self.save()
                self.assertEqual(self.agent.get_product(product_id)["status"], "ok")

    def test_invalid_product_records_fail_closed(self):
        for products in (
            [{"id": 2001, "category_id": 1}],
            [{"id": 1001, "category_id": 99}],
            [{"id": 1001, "category_id": 1}] * 2,
            [{"id": True, "category_id": 1}],
            [{"id": 1001, "category_id": True}],
            [{"id": 1001, "category_id": 1, "price_eur": -1}],
            [{"id": 1001, "category_id": 1, "price_eur": True}],
            [{"id": 1001, "category_id": 1, "sizes": "SOLO TEST"}],
        ):
            with self.subTest(products=products):
                self.document["products"] = products
                self.save()
                self.assertEqual(self.agent.list_categories()["status"], "tool_error")

    def test_overlapping_category_ranges_are_rejected(self):
        self.document["categories"][1]["product_id_min"] = 1999
        self.save()
        self.assertEqual(self.agent.list_categories()["status"], "tool_error")

    def test_invalid_schema_is_rejected(self):
        for document in ([], {}, {"schema_version": True},
                         {"schema_version": 1, "categories": [], "products": []}):
            with self.subTest(document=document):
                self.path.write_text(json.dumps(document), encoding="utf-8")
                self.assertEqual(self.agent.list_categories()["status"], "tool_error")

    def test_missing_or_malformed_file_is_a_tool_error_not_product_absence(self):
        self.path.unlink()
        self.assertEqual(self.agent.get_product(1001)["status"], "tool_error")
        for content in ('{"products":', '{"schema_version": NaN}'):
            self.path.write_text(content, encoding="utf-8")
            self.assertEqual(self.agent.list_products(1)["status"], "tool_error")

    def test_file_permission_error_is_reported_without_leaking_path(self):
        with patch.object(Path, "read_text", side_effect=PermissionError("private-path")):
            result = self.agent.list_categories()
        self.assertEqual(result["status"], "tool_error")
        self.assertNotIn("private-path", result["message"])

    def test_reads_never_modify_the_catalog(self):
        before = self.path.read_bytes()
        self.agent.list_categories()
        self.agent.get_product(1001)
        self.agent.list_products(1)
        self.agent.search_products("aurora")
        self.assertEqual(self.path.read_bytes(), before)

    def test_demo_catalog_has_three_explicitly_fictional_products_per_category(self):
        document = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.assertIs(document["demo_mode"], True)
        self.assertIn("Dati fittizi", document["demo_notice"])
        self.assertEqual(document["products"], [])
        self.assertEqual(sum(len(ProductAgent().list_products(c)["data"])
                             for c in (1, 2, 3, 4)), 12)
        for category_id in (1, 2, 3, 4):
            category = document["categories"][category_id - 1]
            source = json.loads((CATALOG_PATH.parent / category["products_file"])
                                .read_text(encoding="utf-8"))
            self.assertEqual(source["schema_version"], 1)
            self.assertEqual(source["category_id"], category_id)
            self.assertEqual(len(source["products"]), 3)
            result = ProductAgent().list_products(category_id)
            self.assertEqual(result["status"], "ok")
            self.assertEqual([product["id"] for product in result["data"]],
                             [category_id * 1000 + number for number in (1, 2, 3)])
            for product in result["data"]:
                self.assertIs(product["is_demo"], True)
                self.assertEqual(product["demo_notice"], document["demo_notice"])
                self.assertEqual(product["brand"], "Linea Demo")

    def test_demo_prices_are_read_unchanged(self):
        expected = {
            1001: 24.90, 1002: 9.90, 1003: 16.90,
            2001: 34.90, 2002: 29.90, 2003: 39.90,
            3001: 49.90, 3002: 24.90, 3003: 59.90,
            4001: 12.90, 4002: 19.90, 4003: 6.90,
        }
        for product_id, price in expected.items():
            with self.subTest(product_id=product_id):
                result = ProductAgent().get_product(product_id)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["data"]["price_eur"], price)
                self.assertEqual(result["missing_fields"],
                                 ["materials"] if product_id == 3003 else [])

    def test_demo_incomplete_material_and_unavailable_product_remain_explicit(self):
        incomplete = ProductAgent().get_product(3003)
        self.assertIsNone(incomplete["data"]["materials"])
        self.assertEqual(incomplete["missing_fields"], ["materials"])
        unavailable = ProductAgent().get_product(2003)
        self.assertEqual(unavailable["status"], "ok")
        self.assertEqual(unavailable["data"]["availability"], "non disponibile (simulazione)")
        for product_id in (1001, 2001, 3001, 4001):
            self.assertIn("combinazioni taglia/colore da verificare",
                          ProductAgent().get_product(product_id)["data"]["availability"])

    def test_search_names_and_distinctive_keywords(self):
        for query, expected in (
            ("Reggiseno Aurora", [1001]),
            ("VESTAGLIA", [2003]),
            ("Quanto costa il Reggiseno Aurora?", [1001]),
            ("Parlami del copripiumino Sogno", [3003]),
            ("Pigiama", [2001, 2002, 4002]),
            ("Pigiami", [2001, 2002, 4002]),
            ("Quanto costa il pigiama Luna?", [2001]),
            ("Vorrei sapere se avete il Reggiseno Aurora in nero", [1001]),
            ("Potete mostrarmi Aurora in nero?", [1001]),
            ("Che materiale ha il copripiumino Sogno?", [3003]),
            ("Cerco una vestaglia grigio", [2003]),
            ("Vorrei Reggiseno Aurora e Pigiama Luna", [1001, 2001]),
            ("Bimbo Orsetto", [4002]),
            ("Pigiama 4 anni", []),
        ):
            with self.subTest(query=query):
                result = ProductAgent().search_products(query)
                self.assertEqual(result["status"], "ok" if expected else "not_found")
                self.assertEqual([product["id"] for product in result["data"]], expected)

    def test_search_normalizes_accents_apostrophes_and_punctuation(self):
        self.document["products"] = [
            {"id": 1001, "category_id": 1, "name": "Canotta Été d'Estate"},
            {"id": 1002, "category_id": 1, "name": "Canotta Inverno"},
        ]
        self.save()
        for query in ("CANOTTA, ETE!", "canotta été d’estate", "l'été d'estate"):
            with self.subTest(query=query):
                result = self.agent.search_products(query)
                self.assertEqual(result["status"], "ok")
                self.assertEqual([product["id"] for product in result["data"]], [1001])

    def test_search_requires_all_significant_words(self):
        self.assertEqual(ProductAgent().search_products("Reggiseno Luna")["status"], "not_found")
        self.assertEqual(ProductAgent().search_products("vestaglia sconosciuta")["data"], [])
        self.assertEqual(ProductAgent().search_products("pigiama Ines")["data"], [])

    def test_search_ignores_numeric_tokens_instead_of_treating_them_as_ids(self):
        result = ProductAgent().search_products("Aurora 4001 2026 40")
        self.assertEqual(result["status"], "ok")
        self.assertEqual([product["id"] for product in result["data"]], [1001])
        for query in ("1001", "2026", "40", "mi dici il prezzo 1001?"):
            self.assertEqual(ProductAgent().search_products(query)["status"], "invalid_input")

    def test_search_empty_stopwords_or_wrong_type_are_invalid(self):
        for query in (None, True, 1001, [], "", "   ", "mi puoi dire", "prezzo del prodotto"):
            with self.subTest(query=query):
                result = self.agent.search_products(query)
                self.assertEqual(result["status"], "invalid_input")

    def test_search_reports_tool_error_and_never_makes_up_matches(self):
        self.path.write_text("{", encoding="utf-8")
        result = self.agent.search_products("Aurora")
        self.assertEqual(result["status"], "tool_error")
        self.assertIsNone(result["data"])

    def test_duplicate_json_keys_are_corruption_at_any_level(self):
        content = json.dumps(self.document)
        cases = (
            content.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
            content.replace('"name": "Intimo"', '"name": "Intimo", "name": "Altra"'),
            content.replace('"products": []', '"products": [{"id": 1001, "id": 1002, "category_id": 1}]'),
        )
        for value in cases:
            with self.subTest(value=value):
                self.path.write_text(value, encoding="utf-8")
                self.assertEqual(self.agent.list_categories()["status"], "tool_error")

    def test_top_status_cannot_collide_with_tool_result(self):
        self.document["status"] = "tool_error"
        self.save()
        result = self.agent.list_categories()
        self.assertEqual(result["status"], "tool_error")
        self.assertIn("Catalogo non disponibile", result["message"])

    def test_demo_mode_requires_boolean_notice_and_demo_products(self):
        for flag in ("true", 1, None, "false"):
            with self.subTest(flag=flag):
                self.document["demo_mode"] = flag
                self.save()
                self.assertEqual(self.agent.list_categories()["status"], "tool_error")
        self.document["demo_mode"] = True
        for notice in (None, "", "  ", True):
            with self.subTest(notice=notice):
                self.document["demo_notice"] = notice
                self.save()
                self.assertEqual(self.agent.list_categories()["status"], "tool_error")
        self.document["demo_notice"] = "SOLO TEST"
        self.document["products"] = [{"id": 1001, "category_id": 1}]
        self.save()
        self.assertEqual(self.agent.list_categories()["status"], "tool_error")

    def test_demo_product_requires_boolean_and_its_own_notice(self):
        for flag in ("true", 1, None, "false"):
            with self.subTest(flag=flag):
                self.document["products"] = [{"id": 1001, "category_id": 1, "is_demo": flag}]
                self.save()
                self.assertEqual(self.agent.list_categories()["status"], "tool_error")
        for notice in (None, "", "  ", True):
            with self.subTest(notice=notice):
                self.document["products"] = [{"id": 1001, "category_id": 1,
                                             "is_demo": True, "demo_notice": notice}]
                self.save()
                self.assertEqual(self.agent.list_categories()["status"], "tool_error")
        self.document["products"][0]["demo_notice"] = "SOLO TEST"
        self.save()
        self.assertEqual(self.agent.get_product(1001)["status"], "ok")

    def test_legacy_inline_catalog_remains_supported(self):
        self.document["products"] = [{"id": 1001, "category_id": 1, "name": "Scheda legacy"}]
        self.save()
        self.assertEqual(self.agent.get_product(1001)["data"], self.document["products"][0])

    def test_configured_category_sources_merge_without_exposing_paths(self):
        self.document["products"] = [
            {"id": 1001, "category_id": 1, "name": "Prima scheda"},
            {"id": 2001, "category_id": 2, "name": "Seconda scheda"},
        ]
        self.use_category_files()
        self.assertEqual(self.agent.get_product(1001)["data"]["name"], "Prima scheda")
        self.assertEqual(self.agent.get_product(2001)["data"]["name"], "Seconda scheda")
        for category in self.agent.list_categories()["data"]:
            self.assertNotIn("products_file", category)
            self.assertEqual(set(category), {"id", "name", "product_id_min", "product_id_max"})
        # I file non dichiarati nel manifest non vengono cercati o interpretati.
        (self.path.parent / "categories" / "unconfigured.json").write_text("{", encoding="utf-8")
        self.assertEqual(self.agent.list_categories()["status"], "ok")

    def test_category_file_updates_are_visible_without_restart_or_cache(self):
        self.use_category_files()
        self.assertEqual(self.agent.list_products(1)["status"], "empty")
        self.save_category(1, {"schema_version": 1, "category_id": 1, "products": [
            {"id": 1001, "category_id": 1, "name": "Scheda aggiunta"},
        ]})
        self.assertEqual(self.agent.get_product(1001)["data"]["name"], "Scheda aggiunta")

    def test_missing_category_file_fails_entire_catalog_without_partial_results(self):
        self.document["products"] = [{"id": 1001, "category_id": 1}]
        self.use_category_files()
        category = self.document["categories"][3]
        (self.path.parent / category["products_file"]).unlink()
        for result in (self.agent.list_categories(), self.agent.get_product(1001),
                       self.agent.list_products(1), self.agent.search_products("Aurora")):
            self.assertEqual(result["status"], "tool_error")
            self.assertIsNone(result["data"])

    def test_corrupt_category_source_is_a_tool_error(self):
        self.use_category_files()
        source = self.path.parent / self.document["categories"][0]["products_file"]
        for content in ("{", '{"schema_version": NaN}',
                        '{"schema_version": 1, "schema_version": 1}',
                        '{"schema_version": 1, "category_id": 1, "products": '
                        '[{"id": 1001, "id": 1002, "category_id": 1}]}'):
            with self.subTest(content=content):
                source.write_text(content, encoding="utf-8")
                self.assertEqual(self.agent.list_categories()["status"], "tool_error")

    def test_category_source_requires_correct_schema_and_category(self):
        self.use_category_files()
        for content in (
            [], {},
            {"schema_version": True, "category_id": 1, "products": []},
            {"schema_version": 2, "category_id": 1, "products": []},
            {"schema_version": 1, "category_id": True, "products": []},
            {"schema_version": 1, "category_id": 2, "products": []},
            {"schema_version": 1, "category_id": 1, "products": {}},
            {"schema_version": 1, "category_id": 1, "products": [], "status": "ok"},
            {"schema_version": 1, "category_id": 1,
             "products": [{"id": 2001, "category_id": 2}]},
            {"schema_version": 1, "category_id": 1,
             "products": [{"id": 1001, "category_id": True}]},
        ):
            with self.subTest(content=content):
                self.save_category(1, content)
                self.assertEqual(self.agent.list_categories()["status"], "tool_error")

    def test_category_sources_apply_global_product_validation(self):
        self.use_category_files()
        for products in (
            [{"id": 1001, "category_id": 1}] * 2,
            [{"id": 2001, "category_id": 1}],
            [{"id": 1001, "category_id": 1, "price_eur": -1}],
            [{"id": 1001, "category_id": 1, "colors": "Nero"}],
        ):
            with self.subTest(products=products):
                self.save_category(1, {"schema_version": 1, "category_id": 1, "products": products})
                self.assertEqual(self.agent.list_categories()["status"], "tool_error")

    def test_inline_products_and_category_files_cannot_be_mixed(self):
        self.use_category_files()
        self.document["products"] = [{"id": 1001, "category_id": 1}]
        self.save()
        self.assertEqual(self.agent.list_categories()["status"], "tool_error")

    def test_category_source_paths_must_stay_below_catalog_directory(self):
        self.use_category_files()
        for value in ("../outside.json", "categories/../outside.json", "C:/outside.json",
                      "C:\\outside.json", "/outside.json", "\\outside.json",
                      "\\\\server\\share\\outside.json", "", "  ", None, 7):
            with self.subTest(value=value):
                self.document["categories"][0]["products_file"] = value
                self.save()
                result = self.agent.list_categories()
                self.assertEqual(result["status"], "tool_error")
                self.assertNotIn("outside", result["message"])

    def test_resolved_symlink_or_junction_cannot_escape_data_directory(self):
        self.use_category_files()
        source = self.path.parent.resolve() / self.document["categories"][0]["products_file"]
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            # Anche dove Windows non concede la creazione di symlink, verifichiamo
            # la decisione sul percorso finale restituito dal filesystem.
            if path == source:
                return self.path.parent.parent / "outside-catalog" / "products.json"
            return original_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve):
            self.assertEqual(self.agent.list_categories()["status"], "tool_error")


if __name__ == "__main__":
    unittest.main()
