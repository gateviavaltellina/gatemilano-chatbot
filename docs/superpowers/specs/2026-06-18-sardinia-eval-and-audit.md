# Gate Sardinia — Fase 4: audit anti-info-errate + suite di eval

**Data:** 2026-06-18 · **Obiettivo (George):** il bot Sardegna dev'essere perfetto e non dare mai info errate, in vista dell'apertura del 3 luglio.

Prodotto con un workflow multi-agente (ultracode, 33 agenti): 7 agenti di audit (lenti diverse) su KB+prompt vs fatti ufficiali, pipeline genera→verifica-avversariale su 12 aree, critico di completezza. Output: 20 difetti + 92 eval case.

## Difetti trovati e azioni

### 🔴 Alti (2) — corretti (erano rischi reali sul layer dati)
1. **Leak Xceed sugli eventi Sardegna** — `sync/xceed_sync.py` girava anche per `gate_sardinia`: un evento dal canale Xceed `gate-sardinia` non deduplicato da Sanity sarebbe finito nel contesto con link Xceed (la Sardegna è SOLO ticketsms). Oggi il canale fa 404 → bug latente, ma rimosso `gate_sardinia` da `XCEED_CHANNELS`.
2. **Tavoli VIP via Xceed per Sardegna** — il lookup VIP poteva produrre link `booking-plugin.xceed.me`. Aggiunto guard esplicito in `rag/context_builder.py`: per `gate_sardinia` nessun lookup tavoli live (i tavoli si prenotano solo via vip@/WhatsApp).

### 🟡 Medi (7) — corretti i reali
- KB: gerarchia link chiarita (link diretto ticketsms se evento in programma; gatesardinia.it/IG solo fallback).
- KB+prompt: orari — apertura 22:00/chiusura 04:00 SEMPRE certe; il caveat "controlla il sito" vale solo per l'orario d'inizio di un concerto live. Rimosso "(variazioni possibili)".
- KB: rimborso post-evento senza il cancello "solo in casi eccezionali" (allineato a fatti ufficiali e prompt).
- KB+prompt: eccezione concerti live vincolata a "orario di inizio presente nel contesto"; aggiunta sezione TIMETABLE al prompt.
- Prompt: aggiunta regola DRINKLIST VIP (inviata come PDF, mai link inventato).
- Prompt: aggiunta regola PREZZI esplicita (solo prezzi del contesto, alla lettera, niente stime).

### ⚪ Bassi (11) — assorbiti dai fix sopra o accettati
Rollover/arrivo aggiunti alla sezione Orari della KB; "mappa in arrivo" → "nessun link online". I rimanenti (es. dress code/security non nel prompt) NON sono gap reali: la KB è iniettata nel system prompt, quindi quell'informazione è già disponibile al bot.

## Suite di eval (92 casi)
`eval/cases/sardinia_*.yaml` — tickets/prices, hours/timetable, vip, access_policy (età/dress/pagamenti/rimborsi/contatti/accessibilità/location), guardrails (cross-venue/persona). Ogni caso ha `venue: gate_sardinia`, rubrica must/must_not (judge LLM) e, dove utile, `assertions.forbidden_substrings` (es. Xceed, Antonio, Main Room, gatemilano). Include domande-trappola ("li fate su xceed?", "numero di Antonio?", "pago il tavolo la sera?", "minimo 10 persone?"). Verificati da un revisore avversariale perché le risposte attese siano corrette.

`load_cases` valida 119 casi totali (27 Milano + 92 Sardegna), 0 id duplicati.

## Come eseguire l'eval (richiede API key Claude reale)
```
python -m eval.run        # gira tutti i casi (Milano + Sardegna), salva eval/results/<ts>.json
```
Il runner chiama l'API Claude (genera la risposta a temperature=0) e un judge LLM. Non è eseguibile in locale con la key dummy: va lanciato dove c'è `ANTHROPIC_API_KEY` reale (Railway/CI) oppure da George.

## Follow-up
- Far girare `python -m eval.run` con la key vera e rivedere i fail.
- Promemoria aperti: sito gatesardinia.it ancora 23–05 (allineare a 22–04); fine stagione 30 ago da confermare; drinklist su Instagram non allegabile (solo WhatsApp).
