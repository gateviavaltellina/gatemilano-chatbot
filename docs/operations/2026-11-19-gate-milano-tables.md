# Gate Milano — tavoli, verifica del 19 novembre 2026

Richiesta: dare subito i link quando si chiedono tavoli e riconoscere ogni evento pubblicato.

## Comportamento

- Evento e data identificati: link mappa nella prima risposta; eventuale checkout del tavolo scelto. Zona e numero persone non sono prerequisiti.
- Evento/data ambigui: chiedere quale serata, senza scegliere una data arbitraria.
- Più eventi nello stesso giorno: tutti i link restano nel contesto, separati per evento; prezzi e disponibilità non si trasferiscono tra serate.
- Riconoscimento su tutto il calendario futuro, con priorità ai nomi specifici e supporto per nomi corti e accenti. Le date ISO nei link sono lette correttamente anche nei follow-up.
- API indisponibile o lista vuota: link per verificare, nessuna garanzia di vendita, prezzo o disponibilità. Assistenza se la mappa non permette l'acquisto.
- Rimosso il vecchio riferimento personale dalle due knowledge base Gate; nessuna correzione staff persistente equivalente trovata in produzione.

## Verifica

797 test automatici passati dopo l’integrazione degli aggiornamenti concorrenti su persone extra, date festive e follow-up. Verifica aggiuntiva dei 33 eventi pubblicati in Sanity: tutti riconosciuti con nome/data, link corretto nel contesto, route della mappa HTTP 200. HTTP 200 della pagina non garantisce che vi siano tavoli acquistabili.

Prove con il modello di produzione, senza inviare messaggi a clienti: link corretto nella prima risposta per BLK, Ornella, Capodanno e Adrián Mills; domanda generica chiede la serata; follow-up Biscotto fornisce checkout appartenenti alla stessa serata; nessuna diffusione del contatto ritirato.

## Dati tavoli mancanti dal sito

24 eventi restituiscono tavoli dall'API. Per i seguenti 9 il listino non è verificabile; questo non dimostra che siano esauriti o che non siano previsti tavoli. Non sono stati creati inventario o prezzi mancanti.

| Evento | Data | Risposta API |
| --- | --- | --- |
| TIffany Day | 2026-11-23 | 404, evento tavoli non risolto |
| 180BPM | 2026-11-27 | 404, evento tavoli non risolto |
| Regno Spietato - Fasma Club Tour | 2026-11-29 | 404, evento tavoli non risolto |
| THE SATURN MOVEMENT | 2026-12-04 | 404, evento tavoli non risolto |
| PLASMA: Clara Cuvé & William Luck | 2026-12-07 | 404, evento tavoli non risolto |
| AIRA | 2026-12-22 | 200, lista vuota |
| Nicole Moudaber & Anna Tur | 2027-01-05 | 404, evento tavoli non risolto |
| COREVOLUTION: ANDY THE CORE & LANCINHOUSE & THE SICKEST SQUAD | 2027-01-08 | 404, evento tavoli non risolto |
| UGLY KID JOE | 2027-02-08 | 404, evento tavoli non risolto |
