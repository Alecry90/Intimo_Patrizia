# Intimo Patrizia — inventario completo del codice

Questo documento ordina tutti i file del progetto per area. I file sono il codice sorgente originale del progetto; il pacchetto completo è disponibile nell'archivio generato insieme a questo inventario.

## 1. Frontend del sito

| File | Funzione |
|---|---|
| `index.html` | Pagina iniziale, intestazione, sezioni del negozio e widget della chat. |
| `catalogo.html` | Pagina HTML del catalogo prodotti. |
| `catalogo.js` | Caricamento e filtraggio del catalogo lato browser. |
| `script.js` | Comportamenti generali delle pagine e navigazione. |
| `chat.js` | Collegamento del widget chat alle API del backend. |
| `style.css` | Stili, layout responsive, colori, tipografia e componenti del sito. |

## 2. Backend Python

| File | Funzione |
|---|---|
| `backend/__init__.py` | Rende il backend un package Python. |
| `backend/server.py` | Server FastAPI e endpoint pubblici della chat/catalogo. |
| `backend/chat_graph.py` | Flusso LangGraph per identità, domanda, catalogo, preferenze e risposta. |
| `backend/question_answering.py` | Composizione delle risposte a partire dai dati verificati. |
| `backend/product_agent.py` | Ricerca prodotti e applicazione delle regole del catalogo. |
| `backend/customer_store.py` | Gestione locale dei dati cliente e delle preferenze. |
| `backend/ollama_interpreter.py` | Integrazione opzionale con Ollama per interpretare le richieste. |

## 3. Dati del catalogo

| File | Funzione |
|---|---|
| `data/catalog.json` | Indice generale del catalogo dimostrativo. |
| `data/categories/bambino.json` | Prodotti dimostrativi della categoria Bambino. |
| `data/categories/biancheria.json` | Prodotti dimostrativi della categoria Biancheria. |
| `data/categories/intimo.json` | Prodotti dimostrativi della categoria Intimo. |
| `data/categories/pigiameria.json` | Prodotti dimostrativi della categoria Pigiameria. |
| `data/categories/README.md` | Regole e struttura dei dati delle categorie. |

## 4. Test automatici

| File | Area verificata |
|---|---|
| `tests/test_catalog_api.py` | Endpoint e dati del catalogo. |
| `tests/test_chat_integration.py` | Integrazione tra chat e backend. |
| `tests/test_assistant_resilience.py` | Gestione di errori e risposte non disponibili. |
| `tests/test_assistant_scoping.py` | Limiti delle risposte ai dati autorizzati. |
| `tests/test_customer_store.py` | Memorizzazione e recupero delle preferenze cliente. |
| `tests/test_demo_and_errors.py` | Modalità demo e messaggi di errore. |
| `tests/test_ollama_interpreter.py` | Interpretazione locale con Ollama. |
| `tests/test_product_agent.py` | Ricerca e selezione dei prodotti. |
| `tests/test_question_answers.py` | Risposte alle domande sul catalogo. |

## 5. Documentazione e configurazione

| File | Funzione |
|---|---|
| `README.md` | Istruzioni per installazione, avvio e configurazione. |
| `docs/architettura.md` | Spiegazione dell'architettura e dei flussi. |
| `docs/prova_chatbot.md` | Prove guidate della chat e del catalogo. |
| `requirements.txt` | Dipendenze Python del backend. |
| `.gitignore` | File e cartelle esclusi dal controllo versione. |

## Nota importante

Il catalogo presente nel progetto contiene prodotti dimostrativi. Non sono l'assortimento reale del negozio. Prima della pubblicazione commerciale vanno sostituiti con dati, immagini, prezzi, disponibilità e informazioni reali verificati.
