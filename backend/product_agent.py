"""Consultazione deterministica del catalogo locale, senza chiamate all'LLM.

Queste funzioni sono richiamate dal nodo Product Agent del grafo LangGraph.
Gli intervalli riservano ID: non dimostrano che esista un prodotto.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path, PureWindowsPath
from typing import Any, Literal, TypedDict


CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog.json"
DETAIL_FIELDS = (
    "name", "description", "brand", "price_eur", "sizes", "colors",
    "materials", "availability",
)

# Parole di contorno: la ricerca confronta soltanto termini significativi dei nomi.
SEARCH_STOPWORDS = frozenset(
    "a ad al alla alle allo ai agli anche che chi ci come con cosa da dal dalla "
    "dalle dallo dei del della delle dello degli di e ed è gli i il in l la le lo "
    "mi nel nella nelle nello nei negli non o per puoi potrei posso qual quale "
    "quali quanto quanta quanti quante se si su sul sulla sulle sullo sui sugli "
    "un una uno d all dell nell sull c è vorrei voglio cerco cerca cercare "
    "mostra mostri mostrami dimmi dici dire dammi sapere vedere informazioni informazione info "
    "dettagli dettaglio scheda prodotto prodotti articolo articoli prezzo prezzi "
    "costa costano costo taglia taglie colore colori materiale materiali grazie "
    "favore parlami avete hai ha hanno sono disponibile disponibili disponibilità disponibilita "
    "potete potrebbe riesci sapete vorremmo mostrarmi descrizione descrivi trova trovare "
    "fatto fatta fatti fatte tessuto tessuti composizione misura misure euro euros".split()
)

# Piccolo vocabolario esplicito: non si usano correzioni creative del modello.
SEARCH_FORMS = {
    "reggiseni": "reggiseno", "pigiami": "pigiama", "canotte": "canotta",
    "vestaglie": "vestaglia", "asciugamani": "asciugamano", "lenzuola": "lenzuolo",
    "calzini": "calzino", "copripiumini": "copripiumino", "completi": "completo",
    "bimbi": "bimbo", "bambino": "bimbo", "bambini": "bimbo",
}


def _search_tokens(value: str) -> set[str]:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )
    return {SEARCH_FORMS.get(token, token) for token in re.findall(r"[a-z0-9]+", normalized)
            if not token.isdecimal() and token not in SEARCH_STOPWORDS}


ToolStatus = Literal["ok", "not_found", "empty", "invalid_input", "tool_error"]


class ToolResult(TypedDict):
    status: ToolStatus
    data: Any
    message: str
    missing_fields: list[str]


def _result(status: ToolStatus, data: Any, message: str,
            missing_fields: list[str] | None = None) -> ToolResult:
    return {"status": status, "data": data, "message": message,
            "missing_fields": missing_fields or []}


def _is_id(value: Any) -> bool:
    # bool è un sottotipo di int in Python, ma True non è un ID valido.
    return type(value) is int and value > 0


def _validate_catalog(document: Any) -> None:
    """Rifiuta un dataset incoerente anziché restituire dati commerciali errati."""
    if not isinstance(document, dict):
        raise ValueError("Il catalogo deve essere un oggetto JSON.")
    # _load usa status soltanto per i risultati di errore dei tool.
    if "status" in document:
        raise ValueError("Il campo status non appartiene al catalogo.")
    if "demo_mode" in document and type(document["demo_mode"]) is not bool:
        raise ValueError("demo_mode deve essere un booleano.")
    if document.get("demo_mode") is True and (
            not isinstance(document.get("demo_notice"), str)
            or not document["demo_notice"].strip()):
        raise ValueError("Il catalogo dimostrativo deve dichiarare i dati fittizi.")
    version = document.get("schema_version")
    if type(version) is not int or version != 1:
        raise ValueError("Versione del catalogo non supportata.")
    categories = document.get("categories")
    products = document.get("products")
    if not isinstance(categories, list) or not categories or not isinstance(products, list):
        raise ValueError("Categorie o prodotti non validi.")

    category_map: dict[int, dict] = {}
    ranges: list[tuple[int, int]] = []
    for category in categories:
        if not isinstance(category, dict):
            raise ValueError("Categoria non valida.")
        category_id = category.get("id")
        name = category.get("name")
        lower, upper = category.get("product_id_min"), category.get("product_id_max")
        if (not _is_id(category_id) or category_id in category_map
                or not isinstance(name, str) or not name.strip()
                or not _is_id(lower) or not _is_id(upper) or lower > upper):
            raise ValueError("ID, nome o intervallo di categoria non valido.")
        if any(lower <= end and start <= upper for start, end in ranges):
            raise ValueError("Intervalli di ID sovrapposti.")
        category_map[category_id] = category
        ranges.append((lower, upper))

    seen: set[int] = set()
    for product in products:
        if not isinstance(product, dict):
            raise ValueError("Prodotto non valido.")
        if "is_demo" in product and type(product["is_demo"]) is not bool:
            raise ValueError("is_demo deve essere un booleano.")
        if document.get("demo_mode") is True and product.get("is_demo") is not True:
            raise ValueError("Un catalogo demo deve contenere soltanto schede demo.")
        if product.get("is_demo") is True and (
                not isinstance(product.get("demo_notice"), str)
                or not product["demo_notice"].strip()):
            raise ValueError("La scheda dimostrativa deve dichiarare i dati fittizi.")
        product_id, category_id = product.get("id"), product.get("category_id")
        if (not _is_id(product_id) or product_id in seen or not _is_id(category_id)
                or category_id not in category_map):
            raise ValueError("ID prodotto duplicato o categoria sconosciuta.")
        category = category_map[category_id]
        if not category["product_id_min"] <= product_id <= category["product_id_max"]:
            raise ValueError("ID prodotto fuori dall'intervallo della categoria.")
        for field in ("name", "description", "brand", "availability"):
            value = product.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"Campo {field} non valido.")
        for field in ("sizes", "colors", "materials"):
            value = product.get(field)
            if value is not None and (not isinstance(value, list)
                    or any(not isinstance(item, str) for item in value)):
                raise ValueError(f"Campo {field} non valido.")
        price = product.get("price_eur")
        if price is not None and (type(price) not in (int, float)
                or (type(price) is float and not math.isfinite(price)) or price < 0):
            raise ValueError("Prezzo non valido.")
        seen.add(product_id)


def _reject_constant(value: str) -> None:
    raise ValueError(f"Costante JSON non valida: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("Chiave JSON duplicata.")
        document[key] = value
    return document


def _read_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )


def _merge_category_files(document: dict[str, Any], catalog_path: Path) -> None:
    """Carica solo le sorgenti dichiarate, interamente dentro la cartella dati.

    Il manifest è validato prima di questa funzione; il catalogo unificato viene
    validato di nuovo dopo il caricamento, anche per gli ID duplicati fra file.
    Non vengono restituiti cataloghi parziali se una sorgente contiene errori.
    """
    categories = document["categories"]
    if not any("products_file" in category for category in categories):
        return  # Formato precedente, con prodotti direttamente nel catalogo.
    if document["products"]:
        raise ValueError("Non mescolare prodotti nel manifest e file di categoria.")
    root = catalog_path.parent.resolve()
    products: list[dict[str, Any]] = []
    for category in categories:
        if "products_file" not in category:
            continue
        configured_path = category["products_file"]
        if not isinstance(configured_path, str) or not configured_path.strip():
            raise ValueError("Percorso della categoria non valido.")
        relative = Path(configured_path)
        windows_path = PureWindowsPath(configured_path)
        if (relative.is_absolute() or relative.drive or windows_path.drive
                or windows_path.root or ".." in relative.parts or ".." in windows_path.parts):
            raise ValueError("La sorgente deve essere relativa alla cartella del catalogo.")
        source = (root / relative).resolve()
        if not source.is_relative_to(root):
            raise ValueError("La sorgente esce dalla cartella del catalogo.")
        contents = _read_json(source)
        if (not isinstance(contents, dict) or "status" in contents
                or type(contents.get("schema_version")) is not int
                or contents["schema_version"] != 1
                or not _is_id(contents.get("category_id"))
                or contents["category_id"] != category["id"]
                or not isinstance(contents.get("products"), list)):
            raise ValueError("File di categoria non valido.")
        for product in contents["products"]:
            if (not isinstance(product, dict) or not _is_id(product.get("category_id"))
                    or product["category_id"] != category["id"]):
                raise ValueError("Un prodotto appartiene alla categoria sbagliata.")
            products.append(product)
    document["products"] = products


class ProductAgent:
    """Strumenti locali in sola lettura; nessun dato viene generato o scritto.

    catalog_path è configurato dal backend e non deve provenire dal cliente.
    Ogni chiamata rilegge manifest e file di categoria: le modifiche ai dati
    diventano subito visibili senza riavviare il backend.
    """

    def __init__(self, catalog_path: str | Path = CATALOG_PATH):
        self.catalog_path = Path(catalog_path)

    def _load(self) -> dict | ToolResult:
        try:
            document = _read_json(self.catalog_path)
            _validate_catalog(document)
            _merge_category_files(document, self.catalog_path)
            _validate_catalog(document)
            return document
        except (OSError, UnicodeError, ValueError, RuntimeError):
            return _result(
                "tool_error", None,
                "Catalogo non disponibile o non valido. Serve un controllo del negozio.",
            )

    def list_categories(self) -> ToolResult:
        catalog = self._load()
        if catalog.get("status") == "tool_error":
            return catalog
        categories = [
            {field: category[field] for field in
             ("id", "name", "product_id_min", "product_id_max")}
            for category in catalog["categories"]
        ]
        return _result("ok", categories, "Categorie e intervalli di ID riservati.")

    def get_product(self, product_id: int) -> ToolResult:
        if not _is_id(product_id):
            return _result("invalid_input", None, "L'ID prodotto deve essere un intero positivo.")
        catalog = self._load()
        if catalog.get("status") == "tool_error":
            return catalog
        product = next((item for item in catalog["products"] if item["id"] == product_id), None)
        if product is None:
            return _result(
                "not_found", None,
                "Prodotto non presente nel catalogo. Un ID riservato non è una scheda prodotto.",
            )
        missing = [field for field in DETAIL_FIELDS if product.get(field) in (None, "", [])]
        return _result("ok", product, "Scheda letta dal catalogo, senza aggiungere dati.", missing)

    def list_products(self, category_id: int) -> ToolResult:
        if not _is_id(category_id):
            return _result("invalid_input", None, "L'ID categoria deve essere un intero positivo.")
        catalog = self._load()
        if catalog.get("status") == "tool_error":
            return catalog
        if not any(item["id"] == category_id for item in catalog["categories"]):
            return _result("not_found", None, "Categoria non presente nel catalogo.")
        products = [item for item in catalog["products"] if item["category_id"] == category_id]
        if not products:
            return _result("empty", [], "Nessun prodotto ancora inserito in questa categoria.")
        return _result("ok", products, "Prodotti letti dal catalogo, senza aggiungere dati.")

    def search_products(self, query: str) -> ToolResult:
        """Cerca nomi nel testo della domanda; numeri isolati non sono ID.

        Un nome completo riconosciuto prevale sulle parole di contorno. Per i
        nomi parziali occorre la corrispondenza di tutte le parole significative:
        un articolo sconosciuto non viene sostituito dal primo della tipologia.
        """
        if not isinstance(query, str) or not query.strip():
            return _result("invalid_input", None, "Scrivi il nome di un prodotto da cercare.")
        keywords = _search_tokens(query)
        if not keywords:
            return _result("invalid_input", None, "Indica almeno una parola del nome del prodotto.")
        catalog = self._load()
        if catalog.get("status") == "tool_error":
            return catalog
        named = [(item, _search_tokens(item["name"])) for item in catalog["products"]
                 if isinstance(item.get("name"), str) and item["name"].strip()]
        products = [item for item, name in named if len(name) > 1 and name <= keywords]
        if not products:
            # Colori e tessuti sono attributi, non frammenti del nome. Si
            # ignorano soltanto valori realmente presenti nelle schede.
            attributes: set[str] = set()
            for item, _ in named:
                for field in ("colors", "materials"):
                    for value in item.get(field) or []:
                        attributes.update(_search_tokens(value))
            name_words = set().union(*(name for _, name in named)) if named else set()
            keywords -= attributes - name_words
            products = [item for item, name in named if keywords and keywords <= name]
        if not products:
            return _result("not_found", [], "Nessun nome corrispondente nel catalogo.")
        return _result("ok", products, "Prodotti trovati per nome nel catalogo.")
