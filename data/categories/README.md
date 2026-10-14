# File JSON del catalogo per categoria

Questa cartella contiene le schede lette sia dal catalogo pubblico sia dal
chatbot. I quattro file attuali contengono **prodotti demo fittizi**. Nome,
prezzo, taglie, materiali e disponibilità devono essere sostituiti soltanto
con informazioni confermate dal negozio.

## Manifest e percorsi

Il manifest è `../catalog.json`. Dichiara categorie e intervalli; ogni
`products_file` è relativo alla cartella `data`, non a questa cartella.

| Categoria | ID categoria | File dichiarato | Intervallo ID prodotti |
| --- | ---: | --- | --- |
| Intimo | 1 | `categories/intimo.json` | 1001–1999 |
| Pigiameria | 2 | `categories/pigiameria.json` | 2001–2999 |
| Biancheria | 3 | `categories/biancheria.json` | 3001–3999 |
| Bambino | 4 | `categories/bambino.json` | 4001–4999 |

Questo è un manifest completo per il futuro catalogo reale, senza schede
commerciali inventate. Il catalogo attuale mantiene invece `demo_mode: true`
e il proprio `demo_notice` finché contiene soltanto articoli dimostrativi.

```json
{
  "schema_version": 1,
  "demo_mode": false,
  "categories": [
    {
      "id": 1,
      "name": "Intimo",
      "product_id_min": 1001,
      "product_id_max": 1999,
      "products_file": "categories/intimo.json"
    },
    {
      "id": 2,
      "name": "Pigiameria",
      "product_id_min": 2001,
      "product_id_max": 2999,
      "products_file": "categories/pigiameria.json"
    },
    {
      "id": 3,
      "name": "Biancheria",
      "product_id_min": 3001,
      "product_id_max": 3999,
      "products_file": "categories/biancheria.json"
    },
    {
      "id": 4,
      "name": "Bambino",
      "product_id_min": 4001,
      "product_id_max": 4999,
      "products_file": "categories/bambino.json"
    }
  ],
  "products": []
}
```

`products` nel manifest resta un array vuoto: le schede non vanno duplicate
qui. I percorsi devono restare dentro `data`; percorsi assoluti, `..` e
symlink o junction che portano all'esterno vengono rifiutati. Il loader
legge soltanto i file dichiarati nel manifest.

## Struttura di un file di categoria

Ogni file è un oggetto JSON con questi tre campi:

| Campo | Tipo e regola |
| --- | --- |
| `schema_version` | Intero `1`. |
| `category_id` | Intero positivo uguale all'ID della categoria che dichiara il file. |
| `products` | Array di schede; `[]` indica una categoria senza prodotti inseriti. |

Esempio valido per Intimo, senza dichiarare prodotti ancora sconosciuti:

```json
{
  "schema_version": 1,
  "category_id": 1,
  "products": []
}
```

Una futura scheda reale può partire dalla struttura minima seguente, **solo
dopo aver verificato che esista un articolo da associare a quell'ID**:

```json
{
  "schema_version": 1,
  "category_id": 1,
  "products": [
    {
      "id": 1004,
      "category_id": 1
    }
  ]
}
```

Questo esempio riserva soltanto il riferimento: non inventa nome, marca,
prezzo o disponibilità. I dati commerciali verranno aggiunti dalla scheda
reale che fornirai; finché mancano, il chatbot li dichiara non indicati.
Se non c'è ancora un articolo verificato, mantieni `products: []`.

## Campi della scheda prodotto

| Campo | Tipo e regola |
| --- | --- |
| `id` | Obbligatorio: intero positivo, unico nell'intero catalogo, nell'intervallo della categoria. |
| `category_id` | Obbligatorio: intero positivo, uguale a quello del file. |
| `name` | Nome commerciale confermato: stringa oppure `null`/assente. |
| `description` | Descrizione confermata: stringa oppure `null`/assente. |
| `brand` | Marca confermata: stringa oppure `null`/assente. |
| `price_eur` | Numero finito maggiore o uguale a zero, oppure `null`/assente. Non usare stringhe come `"24,90 €"`. |
| `sizes` | Array di stringhe, oppure `null`/assente. |
| `colors` | Array di stringhe, oppure `null`/assente. |
| `materials` | Array di stringhe, oppure `null`/assente. |
| `availability` | Testo confermato della disponibilità, oppure `null`/assente. |
| `is_demo` | Se presente, deve essere un booleano. `true` identifica una scheda fittizia. |
| `demo_notice` | Stringa non vuota obbligatoria quando `is_demo` è `true`. |

Non usare `0` per un prezzo sconosciuto: significa davvero zero euro.
Campi assenti, `null`, stringhe vuote e liste vuote sono trattati come dati
mancanti. Non inserire password, dati dei clienti o percorsi locali nelle
schede; le domande autorizzate appartengono all'archivio clienti separato.

Con `demo_mode: true` nel manifest ogni scheda deve avere `is_demo: true`
e il proprio `demo_notice`; anche il manifest deve contenere un avviso.
Quando sostituisci tutti gli articoli con dati reali, imposta
`demo_mode: false` ed elimina i contrassegni demo soltanto dalle schede
effettivamente sostituite. I contrassegni rimasti identificano ancora dati
fittizi e continuano a produrre avvisi.

## Validazione e aggiornamenti

`ProductAgent` rilegge tutti i file configurati a ogni consultazione. Una
modifica valida è visibile al prossimo caricamento o messaggio, senza
riavviare il server. La pagina già aperta va ricaricata per aggiornare le schede.

File mancanti, JSON malformati, chiavi duplicate, `NaN`/`Infinity`, versioni
non supportate, categorie incoerenti e ID duplicati rendono non valido
l'intero catalogo. Non viene mostrato un elenco parziale: la chat segnala
il problema e le API pubbliche rispondono con HTTP 503. Non usare il campo
`status` alla radice del manifest o dei file di categoria: è riservato
agli esiti dei tool.

È supportato anche il formato precedente: nessuna categoria dichiara
`products_file` e tutte le schede sono nell'array `products` del manifest.
Non mescolare schede inline non vuote e file di categoria. I nuovi dataset
possono adottare direttamente il formato per categoria descritto qui.

La pagina `/catalogo`, le API `GET /api/catalog/categories`,
`GET /api/catalog/products` e `GET /api/catalog/products/{id}` usano questi
dati senza richiedere una sessione cliente. Gli URL delle schede vengono
costruiti dal backend; non occorre inserirli nei JSON. Il manifest e i file
di questa cartella non sono serviti come file statici pubblici.
