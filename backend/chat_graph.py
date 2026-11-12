"""Grafo conversazionale: identità, interpretazione Ollama, catalogo e memoria.

Il modello interpreta il testo; tutte le affermazioni commerciali sono costruite
in Python dai risultati effettivi dei tool. Nessun nome/email viene inviato al modello.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .customer_store import CustomerSessionError, CustomerStore, CustomerStoreError, QuestionConflictError
from .product_agent import ProductAgent
from .ollama_interpreter import OllamaInterpreter, REQUEST_SCHEMA
from .question_answering import (answer_products, category_links, is_catalog_request,
                                is_context_question, requested_fields as detect_requested_fields,
                                question_segments, conversational_opening, pure_social_reply)


EMPTY_PREFERENCES = {"category_counts": {}, "product_counts": {}}
LOGGER = logging.getLogger(__name__)
ALIASES = {
    1: r"\b(intim[oi]|reggisen\w*|slip|mutand\w*|body)\b",
    2: r"\b(pigiam\w*|homewear)\b",
    3: r"\b(biancheria|lenzuol\w*|asciugaman\w*|corred\w*)\b",
    4: r"\b(bambin\w*|bimb\w*|neonat\w*)\b",
}
HUMAN_PATTERN = r"\b(operatore|operatrice|gestore|persona reale|assistenza umana|reclamo)\b"
PREFERENCE_PATTERN = r"\b(preferenze|miei interessi|mie domande|ho chiesto|ho cercato)\b"
NEGATIVE_PATTERN = r"\b(non|nessun\w*|odio|detesto|evit\w*)\b"


class ChatState(TypedDict, total=False):
    token: str
    request_id: str
    question: str
    customer: dict
    categories: list[dict]
    category_ids: list[int]
    product_ids: list[int]
    intent: str
    results: list[dict]
    context: dict
    reply: str
    status: str
    error: str
    saved: bool
    preferences: dict
    llm_available: bool
    preferences_available: bool
    name_matches: list[dict]
    requested_fields: list[str]
    catalog_request: bool
    unmatched_name: bool
    explicit_selection: bool
    links: list[dict]
    product_questions: dict[int, str]
    product_fields: dict[int, list[str]]
    unanswered_questions: list[str]


def _ids(value: Any) -> list[int]:
    if not isinstance(value, list) or any(type(item) is not int or item <= 0 for item in value):
        raise ValueError("Elenco ID non valido.")
    return list(dict.fromkeys(value))


def _explicit_product_ids(text: str) -> list[int]:
    """Anni, prezzi e taglie non diventano ID per il solo fatto di essere numeri."""
    matches = re.findall(r"\b(?:id|prodotto|articolo)\s*[:#n°.-]*\s*(\d{1,9})\b", text)
    if re.fullmatch(r"\s*\d{1,9}\s*[?!.]?\s*", text):
        matches.append(re.search(r"\d+", text).group())
    return list(dict.fromkeys(int(value) for value in matches if int(value) > 0))[:10]


def _safe_tool(operation, *args) -> dict:
    """Gli errori al confine dei tool diventano un esito esplicito del grafo."""
    try:
        result = operation(*args)
        if (not isinstance(result, dict) or result.get("status") not in {
                "ok", "not_found", "empty", "invalid_input", "tool_error"}
                or not {"data", "message", "missing_fields"} <= result.keys()):
            raise ValueError("Risultato del tool non valido.")
        return result
    except Exception as error:
        # Non registrare domande, dati cliente o messaggi di eccezione con percorsi privati.
        LOGGER.error("Tool %s: %s", getattr(operation, "__name__", "catalogo"), type(error).__name__)
        return {"status": "tool_error", "data": None,
                "message": "Il servizio prodotti non è disponibile. Serve un controllo del negozio.",
                "missing_fields": []}


class ChatService:
    """Un grafo per turno HTTP; il contesto breve resta separato per cliente.

    Le domande/preferenze autorizzate vengono invece salvate nel JSON dallo store.
    I turni della stessa sessione sono sequenziali; clienti diversi possono
    attendere Ollama senza bloccare le altre conversazioni.
    """

    def __init__(self, store: CustomerStore, products: ProductAgent | None = None,
                 interpreter: Any = None):
        self.store = store
        self.products = products or ProductAgent()
        self.interpreter = interpreter if interpreter is not None else OllamaInterpreter()
        self.contexts: dict[str, dict] = {}
        self.responses: dict[tuple[str, str], tuple[str, dict]] = {}
        self.lock = threading.RLock()
        self.customer_locks: dict[str, threading.RLock] = {}
        builder = StateGraph(ChatState)
        builder.add_node("customer_agent", self.customer_agent)
        builder.add_node("request_agent", self.request_agent)
        builder.add_node("product_agent", self.product_agent)
        builder.add_node("preference_agent", self.preference_agent)
        builder.add_node("response_agent", self.response_agent)
        builder.add_node("human_handoff", self.human_handoff)
        builder.add_edge(START, "customer_agent")
        builder.add_edge("customer_agent", "request_agent")
        builder.add_edge("request_agent", "product_agent")
        builder.add_edge("product_agent", "preference_agent")
        builder.add_conditional_edges("preference_agent", self.route,
                                      {"human": "human_handoff", "reply": "response_agent"})
        builder.add_edge("response_agent", END)
        builder.add_edge("human_handoff", END)
        self.graph = builder.compile()

    def customer_agent(self, state: ChatState) -> dict:
        customer = self.store.get_customer(state["token"])
        if customer is None:
            raise CustomerSessionError("Sessione non disponibile. Inserisci nuovamente nome ed email.")
        return {"customer": customer, "context": self.contexts.get(customer["id"], {})}

    def resolve_product_questions(self, question: str, categories: list[dict],
                                  inferred: dict) -> tuple[dict, dict, list]:
        """Ogni fatto rimane associato al prodotto e alla richiesta che lo riguarda."""
        segments = question_segments(question)
        # Anche una domanda con prezzo condiviso può nominare due articoli.
        if len(segments) == 1 and re.search(r"\s+e(?:d)?\s+", question, re.IGNORECASE):
            candidates = re.split(r"\s+e(?:d)?\s+", question, flags=re.IGNORECASE)
            found = [_safe_tool(self.products.search_products, part) for part in candidates[:8]]
            recognized = sum(result["status"] == "ok" and len(result["data"]) == 1 for result in found)
            unknown_product = any(result["status"] == "not_found" and any(
                re.search(ALIASES.get(c["id"], re.escape(c["name"].casefold())), part.casefold())
                for c in categories) for part, result in zip(candidates, found))
            if recognized >= 2 or (recognized and unknown_product):
                segments = candidates[:8]
        model_requests = inferred.get("requests", [])
        if not isinstance(model_requests, list):
            model_requests = []
        validated = []
        seen = set()
        for item in model_requests[:8]:
            if (not isinstance(item, dict) or not isinstance(item.get("text"), str)
                    or len(item["text"].strip()) < 3
                    or item["text"].casefold() not in question.casefold()):
                continue
            key = item["text"].strip().casefold()
            if key not in seen:
                seen.add(key)
                validated.append(item)
        # Il modello può affinare una suddivisione, mai cancellare una domanda
        # già riconosciuta: anche le porzioni non coperte vengono conservate.
        refined = []
        for segment in segments:
            spans = sorted((segment.casefold().find(item["text"].casefold()), item["text"])
                           for item in validated if item["text"].casefold() in segment.casefold())
            nonoverlapping = []
            end = -1
            for start, text in spans:
                if start >= end:
                    nonoverlapping.append((start, text))
                    end = start + len(text)
            if len(nonoverlapping) < 2:
                refined.append(segment)
                continue
            cursor = 0
            for start, text in nonoverlapping:
                gap = segment[cursor:start].strip(" ,.!\t")
                if gap and not re.fullmatch(r"(?:e|ed|poi|inoltre|ma|mentre)", gap, re.IGNORECASE):
                    refined.append(gap)
                refined.append(text)
                cursor = start + len(text)
            gap = segment[cursor:].strip(" ,.!\t")
            if gap and not re.fullmatch(r"(?:e|ed|poi|inoltre|ma|mentre)", gap, re.IGNORECASE):
                refined.append(gap)
        segments = refined
        scoped, fields, unmatched = {}, {}, []
        previous_ids = []
        for segment in segments[:8]:
            interpretation = next((item for item in validated if item["text"].casefold() == segment.casefold()), None)
            selected_fields = detect_requested_fields(segment) or detect_requested_fields(segment, interpretation)
            search = _safe_tool(self.products.search_products, segment)
            matches = search["data"] if search["status"] == "ok" else []
            local_categories = [c["id"] for c in categories if re.search(
                ALIASES.get(c["id"], re.escape(c["name"].casefold())), segment.casefold())]
            if len(matches) > 1 and local_categories:
                matches = [p for p in matches if p["category_id"] in local_categories]
            if len(matches) > 1:
                # Un elenco generico di pigiami richiede una scelta; due nomi espliciti no.
                matches = [p for p in matches if p.get("name", "").casefold() in segment.casefold()]
            ids = [p["id"] for p in matches]
            if not ids:
                ids = _explicit_product_ids(segment.casefold())
            if not ids and previous_ids and is_context_question(segment):
                ids = previous_ids
            for product_id in ids:
                scoped[product_id] = (scoped.get(product_id, "") + " " + segment).strip()
                product = next((p for p in matches if p["id"] == product_id), None)
                without_name = re.sub(re.escape(product["name"]), "", segment, flags=re.IGNORECASE) if product else segment
                local_fields = detect_requested_fields(without_name) or detect_requested_fields(without_name, interpretation)
                if local_fields:
                    fields[product_id] = list(dict.fromkeys(fields.get(product_id, []) + local_fields))
            if ids:
                previous_ids = ids
            elif (len(segments) > 1 and not is_context_question(segment)
                  and (selected_fields or local_categories)):
                unmatched.append(segment)
        return scoped, fields, unmatched

    def request_agent(self, state: ChatState) -> dict:
        catalog = _safe_tool(self.products.list_categories)
        if catalog["status"] == "tool_error":
            return {"categories": [], "category_ids": [], "product_ids": [], "intent": "other",
                    "error": "Il catalogo non è leggibile. Serve il controllo del negozio.",
                    "llm_available": False}
        categories = catalog["data"]
        question = state["question"]
        text = question.casefold()
        fields = detect_requested_fields(question)
        catalog_request = is_catalog_request(question, categories)
        category_ids = [item["id"] for item in categories
                        if re.search(ALIASES.get(item["id"], re.escape(item["name"].casefold())), text)]
        product_ids = _explicit_product_ids(text)
        explicit_selection = bool(product_ids)
        intent = "product" if product_ids or category_ids else "catalog"
        if re.search(PREFERENCE_PATTERN, text):
            intent = "preferences"
        if re.search(HUMAN_PATTERN, text):
            intent = "human"
        name_matches = []
        search_status = "not_attempted"
        if not product_ids and intent not in {"human", "preferences"}:
            search = _safe_tool(self.products.search_products, question)
            if search["status"] == "tool_error":
                return {"categories": categories, "category_ids": [], "product_ids": [],
                        "intent": intent, "name_matches": [], "llm_available": False,
                        "error": "La ricerca per nome non è disponibile. Serve il controllo del negozio."}
            if search["status"] == "ok":
                name_matches = search["data"]
                if len(name_matches) > 1 and category_ids:
                    name_matches = [p for p in name_matches if p["category_id"] in category_ids]
                if len(name_matches) == 1:
                    product_ids = [name_matches[0]["id"]]
                    category_ids = [name_matches[0]["category_id"]]
            search_status = search["status"]
        available = True
        inferred = {}
        use_previous = is_context_question(question)
        # Risolvi prima i riferimenti alla conversazione; un'inferenza del modello
        # non deve sostituire una scheda già identificata nel turno precedente.
        if not category_ids and not product_ids and not name_matches and use_previous and not catalog_request:
            category_ids = state["context"].get("category_ids", [])
            product_ids = state["context"].get("product_ids", [])
        try:
            if isinstance(self.interpreter, OllamaInterpreter):
                inferred = self.interpreter.interpret(question, categories, state["context"])
            else:
                inferred = self.interpreter.interpret(question, categories)
            if not isinstance(inferred, dict) or inferred.get("intent") not in {
                "catalog", "product", "preferences", "human", "other"
            }:
                raise ValueError("Interpretazione non valida.")
            valid_categories = {item["id"] for item in categories}
            inferred_categories = _ids(inferred.get("category_ids"))
            _ids(inferred.get("product_ids"))
            if type(inferred.get("use_previous")) is not bool:
                raise ValueError("Indicazione contesto non valida.")
            evidence = inferred.get("category_evidence", {})
            fields = detect_requested_fields(question, inferred)
            query = inferred.get("product_query", "")
            if (not product_ids and not name_matches and isinstance(query, str)
                    and len(query.strip()) >= 3 and query.casefold() in text
                    and intent not in {"human", "preferences"} and not catalog_request):
                search = _safe_tool(self.products.search_products, query)
                if search["status"] == "tool_error":
                    return {"categories": categories, "category_ids": category_ids,
                        "product_ids": [], "name_matches": [], "intent": intent,
                        "llm_available": True, "requested_fields": fields,
                        "error": "La ricerca per nome non è disponibile. Serve il controllo del negozio."}
                if search["status"] == "ok":
                    matches = search["data"]
                    if len(matches) > 1 and category_ids:
                        matches = [p for p in matches if p["category_id"] in category_ids]
                    name_matches = matches
                    search_status = "ok"
                    if len(matches) == 1:
                        product_ids = [matches[0]["id"]]
                        category_ids = [matches[0]["category_id"]]
            if (not category_ids and not product_ids and inferred["intent"] == "product"
                    and isinstance(evidence, dict)):
                category_ids = [item for item in inferred_categories if item in valid_categories
                    and isinstance(evidence.get(str(item)), str)
                    and len(evidence[str(item)].strip()) >= 3
                    and evidence[str(item)].casefold() in text]
            if not re.search(PREFERENCE_PATTERN + "|" + HUMAN_PATTERN, text):
                intent = inferred["intent"]
        except Exception as error:
            # Il modello è un servizio esterno locale: output errato, timeout e
            # indisponibilità hanno lo stesso recupero, senza dettagli tecnici al cliente.
            LOGGER.warning("Interpretazione non disponibile: %s", type(error).__name__)
            available = False
            inferred = {}
        scoped, scoped_fields, unanswered = self.resolve_product_questions(question, categories, inferred)
        if scoped and intent not in {"human", "preferences"}:
            product_ids = list(dict.fromkeys(product_ids + list(scoped)))[:10]
            explicit_selection = explicit_selection or len(scoped) > 1
        if len(name_matches) == 1 and name_matches[0].get("name"):
            # Un colore/tessuto nel nome non è un'informazione richiesta:
            # «Quanto costa Pigiama Cotone?» deve rispondere soltanto al prezzo.
            without_name = re.sub(re.escape(name_matches[0]["name"]), "", question, flags=re.IGNORECASE)
            if without_name != question:
                fields = detect_requested_fields(without_name, inferred if available else None)
        if not fields and product_ids and re.match(r"\s*(e|ed|invece)\b", text):
            fields = state["context"].get("requested_fields", [])
        return {"categories": categories, "category_ids": category_ids,
                "product_ids": product_ids, "intent": intent, "llm_available": available,
                "name_matches": name_matches, "requested_fields": fields,
                "catalog_request": catalog_request, "explicit_selection": explicit_selection,
                "product_questions": scoped, "product_fields": scoped_fields,
                "unanswered_questions": unanswered,
                "unmatched_name": search_status == "not_found" and not catalog_request
                    and not product_ids and not use_previous}

    def product_agent(self, state: ChatState) -> dict:
        if state.get("error") or state["intent"] in {"preferences", "human"}:
            return {"results": []}
        results = []
        if state["product_ids"]:
            for product_id in state["product_ids"][:10]:
                result = _safe_tool(self.products.get_product, product_id)
                results.append({"product_id": product_id, "result": result})
        elif state.get("name_matches") and not state.get("catalog_request"):
            results.append({"search": True, "result": {"status": "ok", "data": state["name_matches"],
                "message": "Schede corrispondenti al nome.", "missing_fields": []}})
        elif state["category_ids"] and not state.get("unmatched_name"):
            for category_id in state["category_ids"]:
                results.append({"category_id": category_id,
                                "result": _safe_tool(self.products.list_products, category_id)})
        elif state.get("name_matches"):
            results.append({"search": True, "result": {"status": "ok", "data": state["name_matches"],
                "message": "Più schede corrispondenti al nome.", "missing_fields": []}})
        if any(item["result"]["status"] == "tool_error" for item in results):
            return {"results": results, "error": "Errore nel recupero dei prodotti. Serve il controllo del negozio."}
        verified_categories = list(dict.fromkeys(item["result"]["data"]["category_id"]
            for item in results if "product_id" in item and item["result"]["status"] == "ok"))
        return {"results": results, **({"category_ids": verified_categories} if state["product_ids"] else {})}

    def preference_agent(self, state: ChatState) -> dict:
        category_ids = list(state["category_ids"])
        product_ids = []
        for item in state["results"]:
            if "product_id" in item and item["result"]["status"] == "ok":
                product = item["result"]["data"]
                product_ids.append(product["id"])
                category_ids.append(product["category_id"])
        # Una domanda salvata non equivale a dichiarare che un articolo piaccia.
        # In presenza di negazioni evitiamo di incrementare interessi positivi.
        if (state.get("error") or state["intent"] in {"preferences", "human"}
                or re.search(NEGATIVE_PATTERN, state["question"].casefold())):
            category_ids, product_ids = [], []
        try:
            result = self.store.record_question(state["token"], state["request_id"],
                state["question"], list(dict.fromkeys(category_ids)), product_ids)
            return {"saved": result["saved"], "preferences": result["preferences"],
                    "preferences_available": True}
        except CustomerStoreError:
            try:
                previous = self.store.get_preferences(state["token"])
                available = True
            except CustomerStoreError:
                previous, available = dict(EMPTY_PREFERENCES), False
            return {"saved": False, "preferences": previous, "preferences_available": available,
                    "error": "Non è stato possibile salvare la domanda e le preferenze. Serve il controllo dell'archivio."}

    @staticmethod
    def route(state: ChatState) -> str:
        return "human" if state.get("error") or state["intent"] == "human" else "reply"

    def human_handoff(self, state: ChatState) -> dict:
        reason = state.get("error", "Per questa richiesta serve parlare con una persona del negozio.")
        return {"status": "human_required", "reply": reason +
                " Puoi usare i recapiti nella sezione Contatti del sito. Nessun operatore è stato contattato automaticamente.",
                "links": []}

    def response_agent(self, state: ChatState) -> dict:
        names = {str(item["id"]): item["name"] for item in state["categories"]}
        parts = []
        status = "ok"
        links = []
        social_reply = pure_social_reply(state["question"]) if not state["results"] else None
        if social_reply and state["intent"] not in {"preferences", "human"}:
            return {"reply": social_reply, "status": "ok", "links": []}
        if state["intent"] == "preferences":
            counts = state["preferences"].get("category_counts", {})
            if not state["customer"]["remember"]:
                parts.append("Il salvataggio delle domande e delle preferenze non è attivo per questa sessione.")
            elif not counts:
                parts.append("Non ci sono ancora interessi registrati. Le domande sulle categorie alimenteranno il tuo profilo.")
            else:
                parts.append("Argomenti più richiesti nella tua sessione: " + ", ".join(
                    f"{names.get(key, key)} ({value})" for key, value in sorted(counts.items(), key=lambda item: -item[1])) +
                    ". Sono interessi ricavati dalle domande, non preferenze dichiarate.")
        elif state["results"]:
            products = []
            absent = False
            for item in state["results"]:
                result = item["result"]
                if result["status"] == "ok":
                    products.extend([result["data"]] if "product_id" in item else result["data"])
                elif result["status"] == "empty":
                    parts.append(f"Per la categoria {names.get(str(item.get('category_id')), 'richiesta')} non sono ancora state inserite schede prodotto.")
                else:
                    absent = True
            if products:
                answer = answer_products(state["question"], products, state.get("requested_fields", []),
                    catalog_request=state.get("catalog_request", False),
                    explicit_selection=state.get("explicit_selection", False),
                    product_questions=state.get("product_questions"), product_fields=state.get("product_fields"))
                parts.append(answer["reply"])
                status, links = answer["status"], answer["links"]
            if absent:
                parts.append("Non trovo una scheda per il prodotto richiesto. Puoi controllare il nome o scegliere dal catalogo.")
            if not products:
                links = category_links(state["categories"], state["category_ids"])
        else:
            if state.get("unmatched_name"):
                parts.append("Non trovo un prodotto con questo nome. Puoi scrivere il nome completo o scegliere una scheda dal catalogo?")
            else:
                parts.append("Quale prodotto ti interessa? Puoi indicarmi il nome oppure scegliere dal catalogo.")
            links = category_links(state["categories"], state["category_ids"])
            status = "clarification"
        if state.get("unanswered_questions"):
            parts.append("Per l’altra richiesta non trovo un prodotto preciso: mi scrivi il nome completo o lo scegli dal catalogo?")
            status = "clarification"
        opening = conversational_opening(state["question"])
        reply = "\n\n".join(parts)
        return {"reply": (opening + " " + reply).strip(), "status": status, "links": links}

    def chat(self, token: str, question: str, request_id: str) -> dict:
        with self.lock:
            customer = self.store.get_customer(token)
            if customer is None:
                raise CustomerSessionError("Sessione non disponibile.")
            customer_lock = self.customer_locks.setdefault(customer["id"], threading.RLock())
        # L'attesa del modello non blocca le conversazioni degli altri clienti.
        with customer_lock:
            key = (customer["id"], request_id)
            with self.lock:
                cached = self.responses.get(key)
            if cached:
                previous_question, response = cached
                if previous_question != question:
                    raise QuestionConflictError("ID richiesta già usato per una domanda diversa.")
                return response
            state = self.graph.invoke({"token": token, "question": question, "request_id": request_id})
            if state.get("unmatched_name") and not state["product_ids"] and state.get("requested_fields"):
                self.contexts.pop(customer["id"], None)
            elif state["category_ids"] or state["product_ids"]:
                self.contexts[customer["id"]] = {"category_ids": state["category_ids"],
                    "product_ids": state["product_ids"], "requested_fields": state.get("requested_fields", [])}
            response = {name: state[name] for name in (
                "reply", "status", "saved", "preferences", "llm_available", "preferences_available", "links")}
            response["request_id"] = request_id
            with self.lock:
                if not state.get("error"):
                    self.responses[key] = (question, response)
                # Il contesto breve è limitato; il JSON resta la memoria autorizzata.
                if len(self.responses) > 500:
                    self.responses.pop(next(iter(self.responses)))
            return response

    def forget_context(self, customer_id: str) -> None:
        with self.lock:
            customer_lock = self.customer_locks.setdefault(customer_id, threading.RLock())
        # Un turno già avviato può terminare dopo la cancellazione dello store.
        # Aspettiamo quel turno prima di svuotare anche l'ultima risposta in RAM.
        with customer_lock:
            with self.lock:
                self.contexts.pop(customer_id, None)
                for key in [key for key in self.responses if key[0] == customer_id]:
                    del self.responses[key]
