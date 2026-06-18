# Gate Sardinia — go-live chatbot (Fase 1: KB + prompt per-venue)

**Data:** 2026-06-18 · **Contesto:** apertura stagione Gate Sardinia il 3 luglio 2026, DM già in arrivo, bot già live sui canali Sardegna.

## Problema
Il bot rispondeva alle richieste Gate Sardinia con dati Milano. La KB `gate_sardinia.md` era generica/errata, ma soprattutto il **system prompt statico era condiviso e pieno di dati Milano hardcoded** (regole a priorità più alta della KB): orari 23–05, Antonio per oggetti smarriti, contatti Milano per accrediti/privati, biglietteria Xceed, Main/Club Room, ruoli off-site. Sistemare la sola KB non bastava: la regola batteva la KB.

## Ground truth confermato da George
- **Biglietteria:** ticketsms.it (non Xceed/Dice).
- **Orari:** tutte le sere 22:00–04:00 (non 23–05). ⚠️ il sito gatesardinia.it dice ancora 23–05 → da allineare lato sito.
- **Struttura:** Via Marco Polo 1, Budoni · 2.500 mq · 1.500 persone · outdoor · 4 bar (confermati).
- **Tavoli VIP — strutturati:** 2 zone (Terrace rialzata/migliore, VIP), ognuna con fila avanti e dietro.
  - T1–T10 / V1–V10: €600, max 10, extra €60/persona
  - T11–T20 / V11–V20: €300, max 6, extra €50/persona
  - Due prodotti: Tavoli (bottle service, min spesa, ingresso incluso) e Ticket VIP su ticketsms (accesso aree, no consumazioni).
  - Prenotazione tavoli: **mappa online in costruzione** → per ora via vip@gatesardinia.it / WhatsApp +39 391 487 6443.
  - Pagamento 100% anticipato; cancellazioni = policy Milano (no rimborso, credito spostabile entro fine stagione); 18+, arrivo max 02:30.
  - Drinklist Sardegna dedicata, **inviata come PDF, mai come link**.
- **Default policy confermati:** età 18+ (16+ se l'evento lo dichiara), patente non accettata; accessibilità → standard + info@ in anticipo, nessuna infrastruttura dichiarata (outdoor); pagamenti carte+contanti; accrediti → info@; biglietto non ricevuto → info@; come arrivare invariato, nessuna navetta.

## Modifiche
1. **`rag/knowledge/gate_sardinia.md`** — riscrittura completa con i dati sopra (ticketsms, 22–04, VIP strutturato, rimborsi, età/documento, accessibilità, pagamenti, contatti, social, timetable policy senza la motivazione "local DJ" di Milano).
2. **`ai/claude_client.py`** — split del system prompt statico: `SYSTEM_STATIC_MILANO` (invariato) + nuovo `SYSTEM_STATIC_SARDINIA` (orari 22–04, ticketsms, contatti Sardegna, VIP via email/WhatsApp, accessibilità outdoor, niente Main/Club Room né ruoli off-site). `build_system_blocks` sceglie per venue. **Milano resta byte-identico** (verificato con diff su snapshot pre-modifica).
3. **`whatsapp/webhook.py`** — invio drinklist reso venue-aware (`_DRINKLISTS` per venue). Prima mandava sempre il PDF Milano anche ai clienti Sardegna.
4. **`static/drinklist_sardegna.pdf`** — aggiunto (servito da `/static`).
5. **`tests/test_sardinia_venue.py`** — regressioni: prompt Sardegna senza dati Milano, prompt Milano preservato, drinklist venue-aware, PDF presente.

## Limiti noti / follow-up
- **Instagram**: il client non supporta l'invio documenti → su IG la drinklist non si allega (solo WhatsApp). Da decidere come gestire.
- **VIP dinamici**: il lookup live disponibilità tavoli passa da Xceed; Sardegna usa ticketsms → resta inerte finché la mappa di prenotazione non è pronta (Fase 2 — dati live).
- **Tech-debt**: le regole generiche sono duplicate tra i due template Milano/Sardegna; unificarle in un preambolo condiviso in un secondo momento.
- **Da verificare con George**: fine stagione 30 ago (allineato al sito; la KB diceva 29); allineare gli orari 22–04 anche sul sito gatesardinia.it.
