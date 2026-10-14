# Intimo Patrizia — sito e chatbot del gruppo 4

Il sito vetrina include un catalogo navigabile e una chat utilizzabile dal browser. Un backend Python
con FastAPI esegue un grafo LangGraph con nodi dedicati a identità, domanda,
catalogo, preferenze e risposta. Ollama interpreta le domande in locale;
prezzi, taglie e disponibilità provengono esclusivamente dal catalogo.
Le risposte selezionano i dati richiesti dalla domanda e offrono un link
alla scheda del prodotto; il cliente può usare il nome senza conoscerne l'ID.

Il catalogo contiene **12 prodotti dimostrativi: tre per categoria**.
Nomi, prezzi, taglie e disponibilità sono fittizi e servono solo a provare
la chat: non rappresentano l'assortimento reale del negozio. Il widget e le
risposte sui prodotti segnalano esplicitamente la modalità demo.
L'elenco completo e le prove guidate sono in [Prova del chatbot](docs/prova_chatbot.md).

## Avvio locale

Servono Python 3.11 o successivo e Ollama per l'interpretazione delle domande.
Nel computer di sviluppo è già disponibile il modello `qwen2.5:latest`.

Dal terminale del workspace entra nella cartella del sito:

```powershell
Set-Location '.\SITO NEGOZIO'
python -m pip install -r requirements.txt
python -m backend.server
```

Se il terminale è già in `SITO NEGOZIO`, esegui solo gli ultimi due comandi.
Apri **http://127.0.0.1:8000** e mantieni il server avviato.
Aprire `index.html` direttamente o con Live Server mostra il sito, ma non
fornisce le API Python necessarie alla chat.

Nel workspace esiste anche un ambiente con le dipendenze già installate:

```powershell
& '..\.venv\Scripts\python.exe' -m backend.server
```

Per controllare i modelli disponibili:

```powershell
ollama list
```

Ollama deve essere avviato per usare il modello. Se è spento, va in timeout o
produce un risultato non valido, la chat mostra il limite e continua con la
ricerca deterministica per categorie, ID e nomi presenti nel catalogo.

### Configurazione facoltativa

Imposta le variabili prima di avviare il server. Ad esempio:

```powershell
$env:OLLAMA_MODEL = 'qwen2.5:latest'
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
$env:OLLAMA_TIMEOUT = '30'
python -m backend.server
```

| Variabile | Valore predefinito | Uso |
| --- | --- | --- |
| `OLLAMA_MODEL` | `qwen2.5:latest` | Nome di un modello già presente in Ollama. |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Indirizzo del servizio Ollama. |
| `OLLAMA_TIMEOUT` | `30` | Timeout della chiamata al modello, in secondi. |
| `CUSTOMER_DATA_PATH` | `data/runtime/customers.json` nella cartella del sito | Percorso del JSON privato dei clienti. |
| `COOKIE_SECURE` | `false` | Impostare `true` quando il sito viene servito tramite HTTPS. |
| `PORT` | `8000` | Porta del server locale, in ascolto su `127.0.0.1`. |

Il JSON e il contesto breve sono gestiti da un solo processo: avviare **un
solo worker**. Questo avvio locale non pubblica il sito su Internet.

## Usare la chat e vedere le preferenze

1. Apri il pulsante della chat nel sito.
2. Inserisci nome ed email. Sono dati autodichiarati, senza password.
3. Se vuoi conservare domande e interessi, seleziona la casella di salvataggio,
   inizialmente deselezionata. La chat funziona anche lasciandola vuota.
4. Chiedi, ad esempio, `Quanto costa il Reggiseno Aurora?`.
5. La risposta indica `Reggiseno Aurora costa 24,90 €.` con avviso demo e
   collegamento alla scheda, senza aggiungere taglie, colori o l'intera categoria.
   Puoi continuare con `E in quali colori?`: viene mantenuto il riferimento ad Aurora.
   Con il salvataggio attivo compaiono anche i relativi interessi osservati.
6. Puoi chiedere `Quali sono le mie preferenze?` oppure usare il riepilogo
   presente nella chat. `Elimina i miei dati` rimuove profilo, domande,
   conteggi e sessione associata.

Le preferenze sono **conteggi degli argomenti trattati nelle domande**, non
una dichiarazione certa dei gusti del cliente. Gli ID categoria e prodotto
sono contati una sola volta per domanda. Le domande con una negazione
esplicita non incrementano interessi positivi; i conteggi prodotto vengono
aggiunti solo quando è stata trovata una scheda nel catalogo. Anche gli
articoli demo permettono di simulare questi conteggi; non indicano acquisti.

### Che cosa viene memorizzato

Con salvataggio attivo, `data/runtime/customers.json` contiene:

- Un ID cliente UUID, nome, email, consenso e data di creazione.
- Le domande con `request_id`, testo, data UTC e ID di categorie/prodotti.
- `preferences.category_counts` e `preferences.product_counts`.
- L'associazione fra hash SHA256 del token di sessione e ID cliente.

Il file viene creato al primo consenso ed è escluso da Git. Il token segreto
rimane nel cookie `HttpOnly`, `SameSite=Strict`; non viene scritto in chiaro
nel JSON o nel `localStorage`. Il cookie con consenso dura 30 giorni nel
browser. Senza consenso, identità e sessione restano soltanto in memoria e
le domande non vengono salvate nel file.

Nel medesimo browser, finché il cookie resta disponibile, le domande e gli
interessi autorizzati sono ritrovati anche dopo il riavvio del backend.
La trascrizione visualizzata nella chat e il contesto breve della conversazione
non sono ripristinati: la prima vive nella pagina, il secondo nella RAM del
backend. Inserire nuovamente la stessa email crea un profilo separato e non
consente di leggere un profilo precedente.

Il riepilogo osservato viene aggiornato nella chat, ma non è ancora un motore
di raccomandazione o una dashboard del negoziante.

## Consultare il catalogo dal sito

Le quattro categorie in **Collezioni** aprono il catalogo con le schede e
le informazioni dei prodotti. Non occorrono nome, email o una sessione chat.

- `/catalogo`: catalogo completo.
- `/catalogo?categoria=1`: selezione Intimo.
- `/catalogo?categoria=1&prodotto=1001`: scheda Reggiseno Aurora selezionata.

I link della chat portano alle stesse schede. Una domanda sul prezzo riceve
il prezzo; una domanda su taglie, colori, materiale o disponibilità riceve
i relativi dati. `Quanto costa il pigiama?` richiede invece di scegliere
il nome fra i pigiami trovati, senza selezionarne uno arbitrariamente.
Il catalogo gestisce categorie vuote, riferimenti inesistenti e problemi
di caricamento con messaggi leggibili.

## Catalogo dimostrativo

| ID categoria | Categoria | ID prodotti riservati |
| --- | --- | --- |
| 1 | Intimo | 1001–1999 |
| 2 | Pigiameria | 2001–2999 |
| 3 | Biancheria | 3001–3999 |
| 4 | Bambino | 4001–4999 |

`data/catalog.json` è il manifest: dichiara le categorie, gli intervalli e
il `products_file` relativo a ciascuna categoria. Le schede sono in
`data/categories/intimo.json`, `pigiameria.json`, `biancheria.json` e
`bambino.json`, tre per file, con `is_demo: true`, marca `Linea Demo` e
modalità `demo_mode: true` nel manifest. Il suo array `products` è vuoto:
non contiene copie delle schede.
`2003` simula un articolo non disponibile; `3003` ha il materiale non
compilato per verificare la gestione dei dati mancanti.

Ogni file di categoria ha la struttura
`{"schema_version": 1, "category_id": 1, "products": [...]}`. Il valore
`category_id` deve coincidere con la categoria del manifest e con quello
di tutte le schede del file. La [guida ai JSON per categoria](data/categories/README.md)
contiene il contratto completo e un modello minimo privo di dati commerciali inventati.
Rimane supportato il formato precedente, con tutte le schede in
`catalog.json` e nessun `products_file`; i due formati non vanno mescolati.

Per passare ai dati reali, sostituisci le schede dimostrative solo con
informazioni confermate dal negozio e aggiorna gli avvisi demo. Ogni `id`
deve essere univoco e nell'intervallo della relativa `category_id`.
Un ID riservato senza scheda non dimostra che l'articolo esista.

I campi previsti sono `name`, `description`, `brand`, `price_eur`, `sizes`,
`colors`, `materials`, `availability`. Taglie, colori e materiali sono liste
di stringhe. Lascia assenti o `null` i dati sconosciuti: un prezzo `0` è un
prezzo effettivamente pari a zero. I tool rileggono manifest e file delle
categorie a ogni consultazione: una modifica diventa visibile senza
riavvio. Un file mancante o non valido rende indisponibile l'intero catalogo,
evitando risposte basate su un assortimento parziale.

## File e verifiche

| File | Responsabilità |
| --- | --- |
| `index.html`, `style.css`, `script.js` | Sito vetrina e struttura del widget. |
| `catalogo.html`, `catalogo.js` | Catalogo pubblico, selezione della categoria e schede collegate dalla chat. |
| `chat.js` | Sessione, messaggi, riepilogo interessi, stato di attesa, retry e cancellazione nel browser. |
| `backend/server.py` | FastAPI, cookie, API pubbliche del catalogo, endpoint della chat e file pubblici consentiti. |
| `backend/chat_graph.py` | Grafo LangGraph, interpretazione Ollama, routing e risposte verificate. |
| `backend/question_answering.py` | Individuazione del dato richiesto, risposte mirate e collegamenti costruiti dal codice. |
| `backend/product_agent.py` | Lettura e validazione deterministica del catalogo. |
| `backend/customer_store.py` | Archivio JSON, consenso, deduplicazione e conteggi degli interessi. |
| `data/catalog.json`, `data/categories/*.json` | Manifest delle categorie e schede dei prodotti; nessun dato personale. |
| `data/categories/README.md` | Struttura dei futuri file JSON reali e regole di validazione. |
| `docs/architettura.md` | Diagramma, flusso dei dati e collegamenti con le lezioni. |
| `docs/prova_chatbot.md` | Elenco dei 12 articoli demo, domande da provare ed esiti attesi, anche in caso di errore. |
| `tests/` | Test di catalogo, memoria cliente, grafo e API. |

Le API di sola lettura `GET /api/catalog/categories`,
`GET /api/catalog/products?category_id=1` e
`GET /api/catalog/products/1001` sono pubbliche. I percorsi locali dei
dataset non vengono esposti; i dati personali continuano a richiedere la
sessione del rispettivo cliente.

Dalla cartella `SITO NEGOZIO`:

```powershell
python -m unittest discover -s tests -v
```

Oppure, con l'ambiente del workspace:

```powershell
& '..\.venv\Scripts\python.exe' -m unittest discover -s tests -v
```

I test usano file temporanei, cataloghi di prova e un interprete sostitutivo:
non richiedono Ollama attivo e non inseriscono prodotti nel catalogo del negozio.

La verifica di questa versione ha superato 118 test Python. Le prove con
Ollama reale hanno controllato prezzo per nome, taglie, colori nel turno
successivo, materiale mancante, articolo non disponibile e richiesta ambigua.
Sono state verificate anche le API pubbliche e la navigazione del frontend
con DOM simulato. La disposizione visiva nel browser resta da controllare.

Durante lo sviluppo puoi avviare un server che ricarica le modifiche Python:

```powershell
& '..\.venv\Scripts\python.exe' -m uvicorn backend.server:app --reload --reload-dir backend
```

Usa un solo server sulla porta 8000. I file JSON vengono riletti anche senza
reload; la pagina aperta va ricaricata per aggiornare JavaScript e catalogo.
Per la verifica manuale prova due sessioni in browser separati, il salvataggio
abilitato e disabilitato, una domanda sulle preferenze, un ID non presente,
la richiesta di un operatore e il funzionamento con Ollama fermo.

Per una richiesta umana la chat indica di usare i recapiti del sito. Non
invia messaggi allo staff e non apre automaticamente una conversazione con
un operatore. Il [diagramma di architettura](docs/architettura.md) distingue
questa gestione manuale dal flusso automatico.

## Possibili estensioni

- Filtri deterministici per budget, taglia e tessuto, usando i campi del catalogo.
- Informazioni su orari, contatti e servizi da un dataset confermato dal negozio.
- Richiesta di prenotazione e ritiro con conferma esplicita del gestore.
- Report aggregato degli argomenti più richiesti per capire le domande frequenti.
- Avvisi di riassortimento su adesione del cliente e con canale di invio configurato.

Sono idee per passi successivi: al momento la chat consulta il catalogo,
registra gli interessi autorizzati e indica i contatti per le richieste umane.
Non prenota articoli, non invia notifiche e non applica ancora filtri di acquisto.
