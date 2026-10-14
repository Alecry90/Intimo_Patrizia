"""Catalogo pubblico, file di categoria e collegamenti provenienti dalla chat.

Le letture normali usano il catalogo demo; tutte le modifiche e le sessioni
cliente restano in cartelle temporanee, senza toccare i dati del negozio.
"""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.customer_store import CustomerStore
from backend.product_agent import CATALOG_PATH, ProductAgent
from backend.server import create_app


class FakeLLM:
    def interpret(self, question, categories):
        return {
            "intent": "product",
            "category_ids": [],
            "product_ids": [],
            "use_previous": False,
            "category_evidence": {},
        }


class CatalogAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = CustomerStore(self.root / "customers.json")
        self.products = ProductAgent()
        self.client = self.make_client(self.products)

    def make_client(self, products):
        client = TestClient(create_app(self.store, FakeLLM(), products))
        self.addCleanup(client.close)
        return client

    def get_json(self, path, status=200, client=None):
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, status, response.text)
        self.assertIn("application/json", response.headers["content-type"])
        data = response.json()
        if status != 200:
            self.assertIsInstance(data["detail"], str)
            self.assertTrue(data["detail"].strip())
        return data

    def copy_sources(self, directory):
        directory.mkdir()
        manifest = directory / "catalog.json"
        manifest.write_bytes(CATALOG_PATH.read_bytes())
        shutil.copytree(CATALOG_PATH.parent / "categories", directory / "categories")
        return manifest

    def test_catalogue_page_and_its_assets_are_accessible_without_a_session(self):
        for path in ("/catalogo", "/catalogo.html"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertIn("Catalogo", response.text)
                self.assertIn("/catalogo.js", response.text)
                self.assertIn("/style.css", response.text)
        for path in ("/catalogo.js", "/style.css", "/chat.js"):
            with self.subTest(asset=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.content)
        self.assertFalse(self.store.path.exists())

    def test_categories_have_counts_and_public_links_without_file_paths(self):
        data = self.get_json("/api/catalog/categories")
        self.assertTrue(data["demo_mode"])
        categories = data["categories"]
        self.assertEqual({item["name"] for item in categories}, {
            "Intimo", "Pigiameria", "Biancheria", "Bambino",
        })
        for category in categories:
            self.assertEqual(category["product_count"], 3)
            self.assertNotIn("products_file", category)
            link = urlsplit(category["url"])
            self.assertEqual(link.path, "/catalogo")
            self.assertEqual(parse_qs(link.query)["categoria"], [str(category["id"])])
        serialized = json.dumps(data)
        self.assertNotIn("categories/", serialized)
        self.assertNotIn(".json", serialized)
        self.assertFalse(self.store.path.exists())

    def test_product_list_can_show_all_twelve_or_only_the_selected_category(self):
        all_products = self.get_json("/api/catalog/products")["products"]
        self.assertEqual(len(all_products), 12)
        self.assertEqual(len({product["id"] for product in all_products}), 12)
        category_products = self.get_json("/api/catalog/products?category_id=2")["products"]
        self.assertEqual(len(category_products), 3)
        self.assertEqual({product["category_id"] for product in category_products}, {2})
        self.assertEqual({product["name"] for product in category_products}, {
            "Pigiama Luna", "Pigiama Brezza", "Vestaglia Nuvola",
        })
        self.assertTrue({product["id"] for product in category_products}
                        <= {product["id"] for product in all_products})

    def test_product_details_keep_catalogue_values_and_missing_material_explicit(self):
        product = self.get_json("/api/catalog/products/1001")["product"]
        self.assertEqual(product["name"], "Reggiseno Aurora")
        self.assertEqual(product["price_eur"], 24.9)
        self.assertEqual(product["category_name"], "Intimo")
        self.assertTrue(product["is_demo"])
        self.assertTrue(product["demo_notice"].strip())
        location = urlsplit(product["product_url"])
        self.assertEqual(location.path, "/catalogo")
        self.assertEqual(parse_qs(location.query), {"categoria": ["1"], "prodotto": ["1001"]})
        incomplete = self.get_json("/api/catalog/products/3003")["product"]
        self.assertEqual(incomplete["name"], "Copripiumino Sogno")
        self.assertIn("materials", incomplete)
        self.assertIsNone(incomplete["materials"])

    def test_internal_product_metadata_is_not_exposed_in_public_api(self):
        document = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        for category in document["categories"]:
            category.pop("products_file", None)
        product = dict(self.products.get_product(1001)["data"])
        product.update(internal_notes="NOTA RISERVATA FORNITORE", supplier_cost=8.76)
        document["products"] = [product]
        catalog_path = self.root / "inline.json"
        catalog_path.write_text(json.dumps(document), encoding="utf-8")
        client = self.make_client(ProductAgent(catalog_path))
        for path in ("/api/catalog/products", "/api/catalog/products?category_id=1",
                     "/api/catalog/products/1001"):
            with self.subTest(path=path):
                response = self.get_json(path, client=client)
                text = json.dumps(response)
                self.assertIn("Reggiseno Aurora", text)
                self.assertNotIn("internal_notes", text)
                self.assertNotIn("supplier_cost", text)
                self.assertNotIn("NOTA RISERVATA FORNITORE", text)

    def test_unknown_and_invalid_product_or_category_have_readable_json_errors(self):
        for path, status in (
            ("/api/catalog/products/9999", 404),
            ("/api/catalog/products?category_id=99", 404),
            ("/api/catalog/products/-1", 422),
            ("/api/catalog/products/non-un-id", 422),
            ("/api/catalog/products?category_id=-1", 422),
            ("/api/catalog/products?category_id=non-un-id", 422),
        ):
            with self.subTest(path=path):
                error = self.get_json(path, status)
                self.assertNotIn("Traceback", json.dumps(error))
                self.assertNotIn("customers.json", json.dumps(error))

    def test_broken_category_source_makes_all_catalogue_reads_fail_without_partial_data(self):
        for failure in ("missing", "corrupt"):
            with self.subTest(failure=failure):
                manifest = self.copy_sources(self.root / failure)
                damaged = manifest.parent / "categories" / "biancheria.json"
                if failure == "missing":
                    damaged.unlink()
                else:
                    damaged.write_text("{JSON incompleto", encoding="utf-8")
                client = self.make_client(ProductAgent(manifest))
                # Anche la categoria Intimo integra deve fallire: pubblicare un
                # catalogo parziale darebbe un'impressione falsa della selezione.
                for path in ("/api/catalog/categories", "/api/catalog/products",
                             "/api/catalog/products?category_id=1", "/api/catalog/products/1001"):
                    error = self.get_json(path, 503, client)
                    self.assertNotIn("products", error)
                    self.assertNotIn("product", error)
                    self.assertNotIn("categories", error)
                    self.assertNotIn("Reggiseno Aurora", json.dumps(error))
                    self.assertNotIn(str(manifest.parent), json.dumps(error))

    def test_chat_product_link_opens_the_same_product_in_page_and_public_api(self):
        session = self.client.post("/api/session", json={
            "name": "Cliente prova", "email": "cliente@example.invalid", "remember": False,
        })
        self.assertEqual(session.status_code, 200, session.text)
        response = self.client.post("/api/chat", json={
            "message": "Quanto costa il Reggiseno Aurora?", "request_id": str(uuid4()),
        })
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()
        matching = [link for link in answer["links"]
                    if parse_qs(urlsplit(link["url"]).query).get("prodotto") == ["1001"]]
        self.assertEqual(len(matching), 1)
        link = matching[0]
        self.assertEqual(self.client.get(link["url"]).status_code, 200)
        product = self.get_json("/api/catalog/products/1001")["product"]
        self.assertEqual(link["url"], product["product_url"])
        self.assertIn(product["name"], answer["reply"])
        self.assertEqual(parse_qs(urlsplit(link["url"]).query)["categoria"], [str(product["category_id"])])
        self.assertFalse(self.store.path.exists())


if __name__ == "__main__":
    unittest.main()
