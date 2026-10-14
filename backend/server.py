"""Avvio: python -m backend.server, quindi http://127.0.0.1:8000.

Serve solo asset pubblici espliciti: JSON clienti e sorgenti backend non sono
raggiungibili come file statici. Il prototipo usa un singolo worker Python.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

import uvicorn
from fastapi import Cookie, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .chat_graph import ChatService
from .customer_store import CustomerSessionError, CustomerStore, CustomerStoreError, QuestionConflictError
from .product_agent import ProductAgent
from .question_answering import product_url


ROOT = Path(__file__).resolve().parent.parent
COOKIE_NAME = "patrizia_session"
LOGGER = logging.getLogger(__name__)


class IdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    remember: StrictBool = False


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    request_id: UUID

    @field_validator("message")
    @classmethod
    def trim_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Scrivi una domanda.")
        return value


def create_app(store: CustomerStore | None = None, interpreter=None,
               products: ProductAgent | None = None) -> FastAPI:
    store = store or CustomerStore(
        os.getenv("CUSTOMER_DATA_PATH", str(ROOT / "data" / "runtime" / "customers.json")),
        validate_on_start=False,
    )
    products = products or ProductAgent()
    service = ChatService(store, products, interpreter)
    app = FastAPI(title="Intimo Patrizia — chatbot gruppo 4", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.chat_service = service

    @app.middleware("http")
    async def same_origin_and_private_data(request: Request, call_next):
        if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.url.netloc:
                return JSONResponse({"detail": "Apri la chat dallo stesso indirizzo del sito."}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(CustomerStoreError)
    async def store_error_handler(request: Request, exc: CustomerStoreError):
        return JSONResponse({"detail": "Archivio clienti non disponibile. I dati esistenti non sono stati sovrascritti."}, status_code=503)

    @app.exception_handler(CustomerSessionError)
    async def session_error_handler(request: Request, exc: CustomerSessionError):
        return JSONResponse({"detail": "La sessione è scaduta. Inserisci nuovamente nome ed email."}, status_code=401)

    @app.exception_handler(QuestionConflictError)
    async def conflict_error_handler(request: Request, exc: QuestionConflictError):
        return JSONResponse({"detail": "Questo identificativo è già associato a una domanda diversa."}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        fields = sorted({str(error["loc"][-1]) for error in exc.errors() if error.get("loc")})
        detail = ("Controlla la categoria o il prodotto selezionato." if request.url.path.startswith("/api/catalog/")
                  else "Controlla i dati inviati: nome ed email validi, domanda da 1 a 2000 caratteri e ID richiesta valido.")
        return JSONResponse({"detail": detail,
                             "fields": fields}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        LOGGER.error("Errore API inatteso: %s", type(exc).__name__)
        return JSONResponse({"detail": "Non è stato possibile completare la richiesta. Riprova la stessa domanda; se il problema continua, contatta il negozio."},
                            status_code=500, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    def require_customer(token: str | None) -> dict:
        customer = store.get_customer(token) if token else None
        if customer is None:
            raise HTTPException(401, "Inserisci nome ed email per iniziare una nuova sessione.")
        return customer

    @app.get("/")
    def home():
        return FileResponse(ROOT / "index.html")

    @app.get("/style.css")
    def stylesheet():
        return FileResponse(ROOT / "style.css", media_type="text/css")

    @app.get("/script.js")
    def site_script():
        return FileResponse(ROOT / "script.js", media_type="text/javascript")

    @app.get("/chat.js")
    def chat_script():
        return FileResponse(ROOT / "chat.js", media_type="text/javascript")

    @app.get("/catalogo")
    @app.get("/catalogo.html")
    def catalogue_page():
        return FileResponse(ROOT / "catalogo.html")

    @app.get("/catalogo.js")
    def catalogue_script():
        return FileResponse(ROOT / "catalogo.js", media_type="text/javascript")

    app.mount("/images", StaticFiles(directory=ROOT / "images"), name="images")

    def read_tool(operation, *args) -> dict:
        try:
            result = operation(*args)
        except Exception as error:
            LOGGER.error("Catalogo pubblico: %s", type(error).__name__)
            raise HTTPException(503, "Il catalogo non è disponibile. Riprova tra poco.") from error
        if not isinstance(result, dict) or result.get("status") == "tool_error":
            raise HTTPException(503, "Il catalogo non è disponibile. Riprova tra poco.")
        return result

    def public_product(product: dict, categories: list[dict]) -> dict:
        allowed = {"id", "category_id", "name", "description", "brand", "price_eur", "sizes",
                   "colors", "materials", "availability", "is_demo", "demo_notice"}
        record = {key: value for key, value in product.items() if key in allowed}
        record["category_name"] = next(c["name"] for c in categories if c["id"] == product["category_id"])
        record["product_url"] = product_url(product)
        return record

    def catalog_snapshot() -> tuple[list[dict], list[dict]]:
        result = read_tool(products.list_categories)
        categories = result["data"]
        records = []
        for category in categories:
            result = read_tool(products.list_products, category["id"])
            if result["status"] not in {"ok", "empty"}:
                raise HTTPException(503, "Il catalogo è cambiato durante la lettura. Riprova.")
            records.extend(public_product(record, categories) for record in result["data"])
        return categories, records

    @app.get("/api/catalog/categories")
    def catalog_categories():
        categories, records = catalog_snapshot()
        return {"categories": [{**category,
            "url": f"/catalogo?categoria={category['id']}",
            "product_count": sum(p["category_id"] == category["id"] for p in records)} for category in categories],
            "demo_mode": any(p.get("is_demo") for p in records)}

    @app.get("/api/catalog/products")
    def catalog_products(category_id: Annotated[int | None, Query(gt=0)] = None):
        categories, records = catalog_snapshot()
        if category_id is not None:
            if not any(c["id"] == category_id for c in categories):
                raise HTTPException(404, "Questa categoria non è presente nel catalogo.")
            records = [p for p in records if p["category_id"] == category_id]
        return {"products": records, "demo_mode": any(p.get("is_demo") for p in records)}

    @app.get("/api/catalog/products/{product_id}")
    def catalog_product(product_id: int):
        result = read_tool(products.get_product, product_id)
        if result["status"] == "invalid_input":
            raise HTTPException(422, "Il riferimento al prodotto non è valido.")
        if result["status"] == "not_found":
            raise HTTPException(404, "Questo prodotto non è presente nel catalogo.")
        categories = read_tool(products.list_categories)["data"]
        return {"product": public_product(result["data"], categories)}

    @app.get("/api/session")
    def session(token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
        return {"customer": store.get_customer(token) if token else None}

    @app.post("/api/session")
    def start_session(body: IdentityRequest, response: Response,
                      token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
        # Non sostituiamo una sessione senza prima eliminare esplicitamente i dati.
        if token and store.get_customer(token):
            raise HTTPException(409, "Una sessione è già attiva. Usa Elimina dati per ricominciare.")
        try:
            new_token, customer = store.create_session(body.name, body.email, body.remember)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        response.set_cookie(COOKIE_NAME, new_token, httponly=True, samesite="strict",
                            secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
                            max_age=60 * 60 * 24 * 30 if customer["remember"] else None, path="/")
        return {"customer": customer}

    @app.delete("/api/session")
    def delete_session(response: Response,
                       token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
        if token:
            customer = store.get_customer(token)
            store.forget(token)
            if customer:
                service.forget_context(customer["id"])
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/preferences")
    def preferences(token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
        require_customer(token)
        result = products.list_categories()
        if result["status"] == "tool_error":
            raise HTTPException(503, result["message"])
        return {"preferences": store.get_preferences(token), "categories": result["data"]}

    @app.post("/api/chat")
    def chat(body: ChatRequest, token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
        require_customer(token)
        return service.chat(token, body.message, str(body.request_id))

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
