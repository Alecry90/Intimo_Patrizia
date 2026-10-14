"""Risposte brevi alla domanda effettiva, ricavate solo dai campi del catalogo.

La selezione del prodotto e del dato richiesto è separata dalla sua resa in
italiano. Gli URL vengono costruiti dal codice, mai accettati dall'LLM.
"""

from __future__ import annotations

import re


FIELD_PATTERNS = {
    "price_eur": r"\b(prezz\w*|cost\w*|cifra|quanto\s+(?:viene|pago|pagherei|si\s+paga))\b",
    "sizes": r"\b(tagli\w*|misur\w*|dimension\w*)\b|\bin\s+(?:x{0,4}[sl]|m)\b|\bin\s+\d+(?:[-/]\d+)?\s+(?:anni|mesi)\b",
    "colors": r"\b(color\w*|nero|nera|neri|nere|bianco|bianca|bianchi|bianche|rosso|rossa|rossi|rosse|blu|rosa|verde|verdi|beige|grigio|grigia|grigi|grigie|azzurro|azzurra|cipria|panna|salvia|tortora)\b",
    "materials": r"\b(material\w*|tessut\w*|composizion\w*|cotone|seta|lana|poliestere|elastan|poliammide)\b|\b(?:di|in)\s+che\s+(?:cosa\s+)?[èe]\s+fatt\w*",
    "availability": r"\b(disponib\w*|avete|hai|esaurit\w*|trovo)\b|\bc[’']\s*[èe]\b|\bce\s+l[’']",
    "brand": r"\b(marc[ah]\w*|marchi\w*|brand)\b",
    "description": r"\b(descrizion\w*|caratteristic\w*)\b|\bcom[’']\s*[èe]\b|\bcome\s+[èe]\b",
}
FIELD_LABELS = {"price_eur": "prezzo", "sizes": "taglie", "colors": "colori",
                "materials": "materiale", "availability": "disponibilità", "brand": "marca",
                "description": "descrizione"}
COLOR_FORMS = {
    "Nero": r"ner[oaie]", "Bianco": r"bianc[oaie]|bianch[ie]", "Rosso": r"ross[oaie]",
    "Blu": "blu", "Rosa": "rosa", "Verde": r"verd[ei]", "Beige": "beige",
    "Grigio": r"grigi[oaie]", "Azzurro": r"azzurr[oaie]", "Cipria": "cipria",
    "Panna": "panna", "Salvia": "salvia", "Tortora": "tortora",
}


def requested_fields(question: str, inferred: dict | None = None) -> list[str]:
    text = question.casefold()
    fields = [field for field, pattern in FIELD_PATTERNS.items() if re.search(pattern, text)]
    if fields or not isinstance(inferred, dict):
        return fields
    evidence = inferred.get("field_evidence", {})
    suggestions = inferred.get("requested_fields", [])
    if not isinstance(evidence, dict) or not isinstance(suggestions, list):
        return []
    return list(dict.fromkeys(field for field in suggestions if isinstance(field, str)
        and field in FIELD_LABELS and isinstance(evidence.get(field), str)
        and evidence[field].strip() and evidence[field].casefold() in text))


def is_catalog_request(question: str, categories: list[dict]) -> bool:
    text = question.strip().casefold().rstrip(".?!")
    if any(text == category["name"].casefold() for category in categories):
        return True
    return bool(re.search(r"\b(catalogo|categor(?:ia|ie)|collezion\w*|elenc\w*)\b|"
                         r"\b(?:mostra\w*|vedere|quali)\b.*\b(?:prodotti|articoli)\b", text))


def is_context_question(question: str) -> bool:
    text = question.casefold()
    if re.search(r"\b(quell[oaie]|quest[oaie]|stess[oaie])\b", text):
        return True
    if not requested_fields(question):
        return False
    for pattern in FIELD_PATTERNS.values():
        text = re.sub(pattern, " ", text)
    remaining = set(re.findall(r"[a-zàèéìòù]+", text))
    filler = set("e ed in di che quale quali quanto quanta quanti quante come viene costa costano "
                 "sono è ha hanno avete hai lo la il li le ne un una uno sai dimmi mi puoi anche "
                 "per favore a al alla del della sul sulla si trova va bene vorrei sapere".split())
    return not (remaining - filler)


def product_url(product: dict) -> str:
    return f"/catalogo?categoria={product['category_id']}&prodotto={product['id']}"


def product_link(product: dict) -> dict:
    return {"label": "Vedi " + (product.get("name") or "il prodotto"), "url": product_url(product)}


def category_links(categories: list[dict], category_ids: list[int] | None = None) -> list[dict]:
    selected = [c for c in categories if not category_ids or c["id"] in category_ids]
    return [{"label": "Apri " + c["name"], "url": f"/catalogo?categoria={c['id']}"} for c in selected]


def _join(values: list[str]) -> str:
    if len(values) < 2:
        return values[0] if values else ""
    return ", ".join(values[:-1]) + " e " + values[-1]


def _money(value: int | float) -> str:
    return f"{value:.2f}".replace(".", ",") + " €"


def _requested_size(question: str) -> str | None:
    explicit = re.search(r"\btaglia\s+([a-z0-9/-]+(?:\s+(?:anni|mesi|cm))?)\b", question, re.IGNORECASE)
    implicit = re.search(r"\bin\s+((?:x{0,4}[sl]|m)(?:/(?:x{0,4}[sl]|m))?|\d+(?:[-/]\d+)?\s+(?:anni|mesi))\b", question, re.IGNORECASE)
    match = explicit or implicit
    return match.group(1).strip() if match else None


def _single_answer(product: dict, question: str, fields: list[str]) -> str:
    name = product.get("name") or "Il prodotto"
    if not fields:
        description = product.get("description")
        text = f"{name}: {description}" if description else name + "."
        if not description and product.get("price_eur") is None:
            return f"La scheda di {name} non è ancora completa. Puoi chiedere al negozio le informazioni mancanti."
        if product.get("price_eur") is not None:
            text += " Prezzo: " + _money(product["price_eur"]) + "."
        return text
    parts = []
    variant_question = ("colors" in fields and any(re.search(rf"\b(?:{pattern})\b", question, re.IGNORECASE)
                        for pattern in COLOR_FORMS.values())) or _requested_size(question) is not None
    for field in fields:
        value = product.get(field)
        if value in (None, "", []):
            parts.append(f"Per {name}, il dato richiesto ({FIELD_LABELS[field]}) non è indicato nel catalogo.")
        elif field == "price_eur":
            parts.append(f"{name} costa {_money(value)}.")
        elif field == "sizes":
            requested = _requested_size(question)
            if requested:
                size = requested.upper() if not re.search(r"\s", requested) else requested
                found = any(size.casefold() == item.casefold() for item in value)
                parts.append(f"La taglia {size} {'è' if found else 'non è'} indicata per {name}. "
                             f"Le taglie a catalogo sono {_join(value)}.")
            else:
                parts.append(f"Per {name} sono indicate le taglie {_join(value)}.")
        elif field == "colors":
            mentioned = [color for color, pattern in COLOR_FORMS.items()
                         if re.search(rf"\b(?:{pattern})\b", question, re.IGNORECASE)]
            if mentioned:
                for color in mentioned:
                    present = any(item.casefold() == color.casefold() for item in value)
                    parts.append(f"Il colore {color.lower()} {'è' if present else 'non è'} indicato per {name}.")
            else:
                parts.append(f"{name} è proposto nei colori {_join(value)}.")
        elif field == "materials":
            parts.append(f"La composizione di {name} è {_join(value)}.")
        elif field == "availability":
            if variant_question and not re.search(r"\b(non disponibile|esaurit\w*)\b", value, re.IGNORECASE):
                parts.append("La disponibilità della specifica combinazione taglia/colore va verificata in negozio.")
            else:
                parts.append(f"Per {name} il catalogo indica: {value}.")
        elif field == "brand":
            parts.append(f"La marca di {name} è {value}.")
        elif field == "description":
            parts.append(f"{name}: {value}")
    if ("sizes" in fields or "colors" in fields) and "availability" not in fields:
        # La presenza fra le opzioni non è la disponibilità di una variante.
        if variant_question or re.search(r"\b(avete|disponibil\w*)\b", question, re.IGNORECASE):
            parts.append("La disponibilità della combinazione taglia/colore va verificata in negozio.")
    return " ".join(parts)


def answer_products(question: str, products: list[dict], fields: list[str], *,
                    catalog_request: bool = False, explicit_selection: bool = False) -> dict:
    """Nessuna scheda completa automatica: soltanto i fatti richiesti e i link."""
    links = [product_link(product) for product in products[:10]]
    if len(products) == 1:
        reply = _single_answer(products[0], question, fields)
        if products[0].get("is_demo"):
            reply += " (Dati demo fittizi.)"
        return {"reply": reply, "status": "ok", "links": links}
    names = [product.get("name") or "Prodotto senza nome" for product in products[:10]]
    if catalog_request and not fields:
        reply = "In questa selezione trovi " + _join(names) + ". Apri una scheda per i dettagli."
        if any(product.get("is_demo") for product in products):
            reply += " (Catalogo demo.)"
        return {"reply": reply, "status": "ok", "links": links}
    if explicit_selection and fields:
        return {"reply": " ".join(_single_answer(product, question, fields) for product in products[:10])
                + (" (Dati demo fittizi.)" if any(p.get("is_demo") for p in products) else ""),
                "status": "ok", "links": links}
    return {"reply": "Quale prodotto intendi: " + _join(names) + "? Puoi rispondere con il nome.",
            "status": "clarification", "links": links}
