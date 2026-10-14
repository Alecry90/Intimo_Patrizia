"""Identità autodichiarate, domande e interessi osservati nel catalogo.

Nome ed email non sono credenziali e non consentono di recuperare altri profili.
Il token di sessione è segreto: nel JSON viene salvato soltanto il suo hash.
Le sessioni senza consenso restano nella memoria del processo.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from uuid import UUID, uuid4


CUSTOMERS_PATH = Path(__file__).resolve().parent.parent / "data" / "runtime" / "customers.json"
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.RLock()


class CustomerStoreError(Exception):
    """Archivio non disponibile o incoerente: serve un controllo umano."""


class CustomerSessionError(ValueError):
    """La sessione non esiste più: il browser deve chiedere nuovamente l'identità."""


class QuestionConflictError(ValueError):
    """Lo stesso identificativo è stato usato per due domande diverse."""


def validate_identity(name: str, email: str) -> tuple[str, str]:
    """Normalizza nome/email; nessun account viene autenticato o cercato via email."""
    if not isinstance(name, str) or not isinstance(email, str):
        raise ValueError("Nome ed email devono essere stringhe.")
    name = " ".join(name.split())
    email = email.strip().lower()
    if not 1 <= len(name) <= 80 or any(ord(char) < 32 for char in name):
        raise ValueError("Inserisci un nome tra 1 e 80 caratteri.")
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        raise ValueError("Inserisci un indirizzo email valido (massimo 254 caratteri).")
    return name, email


def _empty_preferences() -> dict:
    return {"category_counts": {}, "product_counts": {}}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.utcoffset() == timedelta(0)
    except ValueError:
        return False


def _question_input(request_id: str, question: str) -> tuple[str, str]:
    if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 128:
        raise ValueError("L'ID della domanda deve contenere da 1 a 128 caratteri.")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000:
        raise ValueError("La domanda deve contenere da 1 a 4000 caratteri.")
    return request_id.strip(), question.strip()


def _ids(values: list[int]) -> list[int]:
    if not isinstance(values, list) or any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("Gli ID devono essere liste di interi positivi.")
    return sorted(set(values))


def _increment(preferences: dict, category_ids: list[int], product_ids: list[int]) -> None:
    for field, identifiers in (("category_counts", category_ids), ("product_counts", product_ids)):
        for identifier in identifiers:
            key = str(identifier)
            preferences[field][key] = preferences[field].get(key, 0) + 1


def _public(customer: dict) -> dict:
    return {key: customer[key] for key in ("id", "name", "email", "remember")}


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chiave JSON duplicata.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Costante JSON non valida.")


def _validate_document(document: object) -> None:
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document["schema_version"] != 1 or not isinstance(document.get("customers"), dict)
            or not isinstance(document.get("sessions"), dict)):
        raise ValueError("Struttura dell'archivio non valida.")
    customers = document["customers"]
    for customer_id, customer in customers.items():
        if str(UUID(customer_id)) != customer_id:
            raise ValueError("L'ID cliente deve essere un UUID canonico.")
        if (not isinstance(customer, dict) or customer.get("id") != customer_id
                or customer.get("remember") is not True
                or validate_identity(customer.get("name"), customer.get("email"))
                != (customer.get("name"), customer.get("email"))
                or not _valid_timestamp(customer.get("created_at"))
                or not isinstance(customer.get("questions"), list)):
            raise ValueError("Profilo cliente non valido.")
        expected = _empty_preferences()
        request_ids: set[str] = set()
        for event in customer["questions"]:
            if not isinstance(event, dict):
                raise ValueError("Domanda non valida.")
            request_id, question = _question_input(event.get("request_id"), event.get("question"))
            category_ids, product_ids = _ids(event.get("category_ids")), _ids(event.get("product_ids"))
            if (request_id in request_ids or request_id != event["request_id"]
                    or question != event["question"] or not _valid_timestamp(event.get("timestamp"))
                    or category_ids != event["category_ids"] or product_ids != event["product_ids"]):
                raise ValueError("Domanda duplicata o incoerente.")
            request_ids.add(request_id)
            _increment(expected, category_ids, product_ids)
        preferences = customer.get("preferences")
        if preferences != expected:
            raise ValueError("Conteggi delle preferenze incoerenti con le domande.")
        # Python confronta True == 1: i contatori accettano solo int autentici.
        if any(type(value) is not int for counts in preferences.values() for value in counts.values()):
            raise ValueError("Contatore preferenze non valido.")
    sessions = document["sessions"]
    if any(not _HASH.fullmatch(key) or not isinstance(value, str) or value not in customers
           for key, value in sessions.items()):
        raise ValueError("Sessione non valida.")
    if set(sessions.values()) != set(customers):
        raise ValueError("Profilo senza sessione associata.")


class CustomerStore:
    """Archivio JSON per un backend locale, con lock condiviso per percorso.

    Le scritture atomiche evitano file parziali; l'RLock protegge i thread del
    processo. Avviare un solo processo backend, non più worker indipendenti.
    """

    def __init__(self, path: str | Path = CUSTOMERS_PATH, *, validate_on_start: bool = True):
        self.path = Path(path)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(os.path.normcase(str(self.path.resolve())), threading.RLock())
        self._volatile: dict[str, dict] = {}
        if validate_on_start:
            with self._lock:
                self._load()

    def _load(self) -> dict:
        try:
            content = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"schema_version": 1, "customers": {}, "sessions": {}}
        except (OSError, UnicodeError) as error:
            raise CustomerStoreError("Impossibile leggere l'archivio clienti.") from error
        try:
            document = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            _validate_document(document)
            return document
        except (ValueError, TypeError, KeyError) as error:
            raise CustomerStoreError("Archivio clienti non valido: richiedi un controllo del negozio.") from error

    def _save(self, document: dict) -> None:
        temporary_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except (OSError, ValueError, UnicodeError) as error:
            raise CustomerStoreError("Impossibile salvare l'archivio clienti.") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _hash(token: str) -> str | None:
        if not isinstance(token, str) or not token:
            return None
        try:
            return hashlib.sha256(token.encode("utf-8")).hexdigest()
        except UnicodeError:
            return None

    def create_session(self, name: str, email: str, remember: bool) -> tuple[str, dict]:
        name, email = validate_identity(name, email)
        if type(remember) is not bool:
            raise ValueError("La scelta di memorizzazione deve essere un booleano.")
        token = secrets.token_urlsafe(32)
        token_hash = self._hash(token)
        customer = {"id": str(uuid4()), "name": name, "email": email, "remember": remember}
        with self._lock:
            if not remember:
                self._volatile[token_hash] = {"customer": customer, "request_questions": {}}
            else:
                document = self._load()
                customer.update(created_at=_timestamp(), questions=[], preferences=_empty_preferences())
                document["customers"][customer["id"]] = customer
                document["sessions"][token_hash] = customer["id"]
                self._save(document)
        return token, _public(customer)

    def get_customer(self, token: str) -> dict | None:
        token_hash = self._hash(token)
        if token_hash is None:
            return None
        with self._lock:
            if token_hash in self._volatile:
                return _public(self._volatile[token_hash]["customer"])
            document = self._load()
            customer_id = document["sessions"].get(token_hash)
            return _public(document["customers"][customer_id]) if customer_id else None

    def get_preferences(self, token: str) -> dict:
        token_hash = self._hash(token)
        if token_hash is None:
            return {}
        with self._lock:
            if token_hash in self._volatile:
                return _empty_preferences()
            document = self._load()
            customer_id = document["sessions"].get(token_hash)
            return deepcopy(document["customers"][customer_id]["preferences"]) if customer_id else {}

    def record_question(self, token: str, request_id: str, question: str,
                        category_ids: list[int], product_ids: list[int]) -> dict:
        token_hash = self._hash(token)
        request_id, question = _question_input(request_id, question)
        category_ids, product_ids = _ids(category_ids), _ids(product_ids)
        with self._lock:
            if token_hash in self._volatile:
                request_questions = self._volatile[token_hash]["request_questions"]
                duplicate = request_id in request_questions
                if duplicate and request_questions[request_id] != question:
                    raise QuestionConflictError("ID domanda già usato per un testo diverso.")
                request_questions[request_id] = question
                return {"saved": False, "duplicate": duplicate, "preferences": _empty_preferences()}
            document = self._load()
            customer_id = document["sessions"].get(token_hash)
            if not customer_id:
                raise CustomerSessionError("Sessione cliente non valida o scaduta.")
            customer = document["customers"][customer_id]
            previous = next((event for event in customer["questions"]
                             if event["request_id"] == request_id), None)
            duplicate = previous is not None
            if duplicate and previous["question"] != question:
                raise QuestionConflictError("ID domanda già usato per un testo diverso.")
            if not duplicate:
                customer["questions"].append({
                    "request_id": request_id, "question": question, "timestamp": _timestamp(),
                    "category_ids": category_ids, "product_ids": product_ids,
                })
                _increment(customer["preferences"], category_ids, product_ids)
                self._save(document)
            return {"saved": True, "duplicate": duplicate,
                    "preferences": deepcopy(customer["preferences"])}

    def forget(self, token: str) -> None:
        token_hash = self._hash(token)
        if token_hash is None:
            return
        with self._lock:
            if token_hash in self._volatile:
                del self._volatile[token_hash]
                return
            document = self._load()
            customer_id = document["sessions"].get(token_hash)
            if customer_id is not None:
                del document["customers"][customer_id]
                document["sessions"] = {key: value for key, value in document["sessions"].items()
                                        if value != customer_id}
                self._save(document)
