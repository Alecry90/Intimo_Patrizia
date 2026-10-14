# Provare il chatbot dal sito

Questa è una **simulazione**: tutti i prodotti elencati, i prezzi e le
disponibilità sono fittizi. La marca comune è `Linea Demo`.
Le risposte commerciali leggono il dataset locale e mostrano un avviso;
l'LLM non può cambiare prezzo o disponibilità.
La chat seleziona il dato chiesto e collega la scheda pertinente; non serve
conoscere l'ID del prodotto.

## I 12 prodotti dimostrativi

| Categoria | ID | Prodotto | Prezzo demo |
| --- | ---: | --- | ---: |
| Intimo | 1001 | Reggiseno Aurora | 24,90 € |
| Intimo | 1002 | Slip Comfort | 9,90 € |
| Intimo | 1003 | Canotta Essenziale | 16,90 € |
| Pigiameria | 2001 | Pigiama Luna | 34,90 € |
| Pigiameria | 2002 | Pigiama Brezza | 29,90 € |
| Pigiameria | 2003 | Vestaglia Nuvola | 39,90 € |
| Biancheria | 3001 | Completo Lenzuola Armonia | 49,90 € |
| Biancheria | 3002 | Set Asciugamani Morbidezza | 24,90 € |
| Biancheria | 3003 | Copripiumino Sogno | 59,90 € |
| Bambino | 4001 | Body Bimbo Stella | 12,90 € |
| Bambino | 4002 | Pigiama Bimbo Orsetto | 19,90 € |
| Bambino | 4003 | Calzini Bimbo Arcobaleno | 6,90 € |

`2003` è intenzionalmente **non disponibile**. Per `3003` il materiale è
intenzionalmente **non compilato**. Servono a controllare che il chatbot
dichiari il limite senza inventare informazioni.

## Avvio e percorso principale

1. Avvia il server dalla cartella `SITO NEGOZIO`:

   ```powershell
   & '..\.venv\Scripts\python.exe' -m backend.server
   ```

2. Apri `http://127.0.0.1:8000`. In **Collezioni**, clicca Intimo:
   il catalogo mostra le tre schede demo con le informazioni disponibili.
   Questa consultazione non richiede nome/email. Puoi anche aprire
   `http://127.0.0.1:8000/catalogo` per tutte le categorie.
3. Torna alla home e premi **Chiedi a Patrizia**.
4. Usa un nome di prova e un'email come `demo@example.com`.
5. Seleziona **Salva le mie domande e i miei interessi** per provare la memoria.
6. Invia `Quanto costa il Reggiseno Aurora?`. Controlla il prezzo 24,90 €,
   l'avviso demo e il link **Vedi Reggiseno Aurora**. Il link apre
   `/catalogo?categoria=1&prodotto=1001`, con la scheda pertinente.
7. Chiedi `E in quali colori?`: deve rispondere sui colori di Aurora,
   senza tornare alla lista della categoria.
8. Apri **Argomenti più richiesti**: devono apparire Intimo e il prodotto
   richiesto. Invia nuovamente la domanda come nuovo messaggio: i conteggi
   aumentano di una unità. Il pulsante **Riprova la stessa domanda**,
   quando compare dopo un problema di connessione, riusa l'identificativo
   iniziale e non deve duplicare il salvataggio.
9. Chiedi `Quali sono le mie preferenze?`: il riepilogo mostra gli argomenti
   osservati, senza considerarli acquisti o gusti certi.

La conferma di salvataggio compare solo quando il backend restituisce
`saved: true`. Il JSON privato è `data/runtime/customers.json`; non è
scaricabile dal browser. I conteggi sono nelle chiavi `category_counts`
e `product_counts` del profilo. Nome ed email sono autodichiarati e non
costituiscono un account verificato.

## Domande da copiare e risultato atteso

| Domanda | Che cosa controllare |
| --- | --- |
| `Quanto costa il Reggiseno Aurora?` | `Reggiseno Aurora costa 24,90 €.` con avviso demo e link alla scheda. Nessun elenco di Intimo o dettaglio non chiesto. |
| `Che taglie ha il Pigiama Luna?` | Taglie S, M, L e XL, avviso demo e link a Luna. |
| `E in quali colori?` subito dopo Luna | Blu e Grigio per Luna: il riferimento al prodotto resta nella conversazione. |
| `Vorrei informazioni sulla categoria Pigiameria` | Nomi Pigiama Luna, Pigiama Brezza e Vestaglia Nuvola, con link alle rispettive schede. |
| `Vorrei informazioni su Reggiseno Aurora` | Breve descrizione e prezzo disponibili, con link alla scheda; nessun ID obbligatorio. |
| `Quanto costa il pigiama?` | Chiarimento: scegliere il nome del pigiama fra quelli trovati. Non attribuisce un prezzo arbitrario. |
| `Di che materiale è il Copripiumino Sogno?` | Il materiale non è indicato; nessun tessuto inventato. Link alla scheda incompleta. |
| `È disponibile la Vestaglia Nuvola?` | Non disponibile nel catalogo demo; nessuna promessa di ordine o prenotazione. |
| `Vorrei il prodotto 9999` | ID non trovato; nessuna scheda o prezzo inventato, richiesta di un riferimento valido. |
| `Quali sono le mie preferenze?` | Conteggi osservati con consenso; l'interrogazione del profilo non aggiunge un interesse prodotto. |
| `Non mi interessa il prodotto 1001` | Nessun incremento positivo degli interessi per la domanda; può essere conservata se è attivo il salvataggio. |
| `Voglio parlare con un operatore` | Indicazione di contattare il negozio dai recapiti del sito, senza affermare che lo staff sia stato avvisato. |

Le risposte dipendono dalla domanda e dai dati selezionati dai tool. Il
modello interpreta la richiesta; Python valida le citazioni, risolve il
prodotto e compone la risposta e i link. Le schede complete sono nel catalogo.
I filtri combinati per budget, taglia o composizione e le prenotazioni restano
estensioni future.

## Provare i collegamenti del catalogo

| Indirizzo | Che cosa verificare |
| --- | --- |
| `/catalogo` | Tutti i prodotti demo e la selezione delle categorie. |
| `/catalogo?categoria=1` | Soltanto Intimo: Aurora, Comfort ed Essenziale. |
| `/catalogo?categoria=1&prodotto=1001` | Scheda Aurora selezionata, raggiungibile anche dal link nella chat. |
| `/catalogo?categoria=99` | Errore leggibile per categoria inesistente. |
| `/catalogo?categoria=1&prodotto=9999` | Errore leggibile per prodotto inesistente; nessuna scheda sostitutiva. |

Il browser legge `GET /api/catalog/categories`,
`GET /api/catalog/products?category_id=1` e
`GET /api/catalog/products/1001`. Puoi provarli senza aver aperto una sessione
chat. Il JSON privato dei clienti e i file sorgente dei dati restano separati.

Le schede demo sono già divise in un file JSON per categoria, dichiarato
da `products_file` in `data/catalog.json`. Per preparare i futuri file reali,
segui la [guida ai JSON di categoria](../data/categories/README.md).

## Verificare la memoria senza salvare

Premi **Elimina i miei dati** per rimuovere il profilo di prova. Avvia una
nuova sessione lasciando la checkbox deselezionata e ripeti la domanda su
Reggiseno Aurora: la risposta commerciale continua a funzionare, ma il backend non
registra domande o conteggi nel JSON e il widget indica salvataggio disattivato.
La creazione di una sessione con la stessa email non recupera un profilo
precedente: la continuità dipende dal cookie del medesimo browser.

## Errori da simulare

Le prove che modificano file vanno eseguite su copie temporanee nei test;
non è necessario danneggiare il catalogo o l'archivio dei clienti.

| Caso | Risultato atteso |
| --- | --- |
| Ollama non attivo o risposta non valida | Avviso del limite; restano utilizzabili le regole per categorie, ID e nomi del catalogo. |
| ID inesistente | Messaggio leggibile, senza eccezione mostrata al cliente o sostituzione arbitraria del prodotto. |
| Nome ambiguo o domanda senza oggetto | Domanda di chiarimento per scegliere la categoria o il nome, con link ai candidati quando disponibili. |
| Campo mancante (`3003`) | Indicazione dell'informazione mancante; l'LLM non completa il dato. |
| Manifest o file di categoria mancante, illeggibile o non valido | In chat richiesta di verifica umana; il catalogo pubblico mostra un errore di caricamento. Nessuna informazione inventata o elenco parziale. |
| Archivio clienti non scrivibile | Nessuna conferma falsa di salvataggio; messaggio per riprovare o contattare il negozio. |
| Sessione scaduta | Richiesta di inserire di nuovo nome ed email. |
| Backend fermo o rete interrotta | Messaggio di connessione e possibilità di riprovare la stessa domanda senza duplicarla. |

La suite automatica si avvia con:

```powershell
& '..\.venv\Scripts\python.exe' -m unittest discover -s tests -v
```

## Funzioni utili da aggiungere in seguito

- **Ricerca con filtri:** budget, taglia, colore e tessuto verificati nel catalogo.
- **Informazioni sul negozio:** orari, recapiti, servizi e politiche da dati confermati dal gestore.
- **Prenotazione e ritiro:** richiesta del cliente seguita da conferma di disponibilità del gestore.
- **Report aggregato delle domande:** categorie richieste e dati mancanti frequenti, per migliorare il servizio.
- **Avvisi di riassortimento:** adesione esplicita e canale configurato, senza invii automatici prima dell'attivazione.

Queste estensioni sono proposte per i prossimi passi e non sono attive nella demo.

Per progettare i filtri, un riferimento funzionale è la
[documentazione sui filtri di catalogo](https://help.shopify.com/manual/online-store/search-and-discovery/filters):
prezzo, disponibilità e opzioni come taglia/colore vanno incrociati usando i dati del prodotto.
Questa è un'idea per il nostro codice Python, senza richiedere l'adozione di Shopify.

## Copertura delle verifiche

La suite comprende cataloghi inline e per categoria, validazione dei file,
risposte mirate, nomi ambigui, contesto della conversazione, link alle schede,
API pubbliche, memoria autorizzata e recupero dagli errori. Usa directory
temporanee e un interprete sostitutivo: non richiede Ollama attivo e non
scrive nel profilo di un cliente reale.

Il percorso browser descritto sopra permette di controllare navigazione,
schede e risposte con Ollama locale, insieme al salvataggio attivo e disattivo.
Il numero di test e l'esito della singola esecuzione vanno letti dal comando
di verifica, senza considerarli una proprietà fissa della demo.
