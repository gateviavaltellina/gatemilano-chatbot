# Audit chatbot — 19 novembre 2026

Ambito: risoluzione di eventi/date, continuità della conversazione, selezione della sede per i tavoli, messaggi consecutivi, avvio del servizio. Non è una certificazione di assenza di altri bug.

## Problemi riprodotti e corretti

| Problema | Riproduzione | Correzione |
| --- | --- | --- |
| La cronologia ripristinava la prima data/serata | Perreo 21 novembre → «anzi il 28» → «siamo in 5»; oppure cambio artista e successivo messaggio breve | Prevale l'ultima selezione del cliente. Una nuova data relativa/numerica prevale sulla cronologia; per una vecchia data relativa si usa la data concreta della risposta, senza ricalcolarla come se fosse stata scritta oggi. |
| Tavoli della sede errata | Richiesta per Guè a Sardinia, con un evento Milano nella stessa data; Perreo esistente in entrambe le sedi | La sede esplicita o quella dell'artista guida la ricerca. Non si passa all'altra sede solo perché mancano tavoli o dati del catalogo scelto. |
| La festa prevaleva sulla data precisa | «Halloween del 31/10/2027», «Natale il 24 dicembre 2026» | Il nome della festa è un fallback; una data scritta prevale. |
| Servizio pronto prima del calendario | Durante il caricamento iniziale /health rispondeva 200 | L'avvio attende il primo sync. /health risponde 503 prima della readiness, così Railway non promuove anticipatamente il nuovo container. |
| Richiesta del cliente cancellata prima del modello | «preferisco BLK» senza risposta, seguito da «siamo in cinque» | La pulizia della cronologia conserva il turno user finale e invia entrambi i messaggi al modello. |

L'API ammette messaggi user consecutivi: non occorre eliminarne uno per alternare i ruoli. Fonte: [Claude Messages API](https://platform.claude.com/docs/en/api/messages/create).

## Verifica

- Ogni difetto riprodotto con test fallente prima della modifica; suite completa: 811 test passati, 2 warning di deprecazione preesistenti.
- Test di regressione su cambio data, cambio artista, cambio sede, stessa data con due sedi, date festive, avvio e conservazione dei messaggi.
- Prove con il modello reale e dati pubblicati, senza inviare messaggi a clienti: Perreo 21 → 28 novembre, Capodanno → BLK, domanda «e domani invece?».
- Diagnostica iniziale produzione: 23 tracce, 8 richieste e 8 risposte con invio riuscito. È un campione breve e non una garanzia generale di consegna.

## Limiti osservati

- Nel caso «e domani invece?» il modello può ancora citare inutilmente la serata precedente, pur ricevendo la data corretta e fornendo il link corretto per domani. La qualità del testo va monitorata; la risoluzione dei dati è coperta dai test.
- Readiness attende la conclusione del primo tentativo di sync; non garantisce che i provider esterni rispondano sempre con dati completi.
- Rimangono i 9 eventi senza listino tavoli verificabile dal sito, documentati nella [verifica tavoli](2026-11-19-gate-milano-tables.md). Non sono stati inventati prezzi o creato inventario.
