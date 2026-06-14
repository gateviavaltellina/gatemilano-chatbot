# Consolidamento Correzioni → KB (Fase 2B) — Design

**Data:** 2026-06-14
**Stato:** approvato (design), pronto per il piano di implementazione
**Autore:** George + Claude

## Contesto e problema

Le correzioni staff (Fase 2A) vivono nello store live e vengono iniettate
globalmente nel contesto a ogni messaggio. Crescono nel tempo (soft cap 30) e
restano "non permanenti". Vogliamo **fondere** le correzioni stabili nella
knowledge base canonica (`rag/knowledge/{venue}.md`) — così diventano permanenti
e ben integrate — e poi rimuoverle dallo store live per tenerlo piccolo.

La fusione tocca la KB canonica (alto impatto sui clienti), quindi è
**locale + assistita da LLM + revisionata da umano**, e si appoggia agli eval
case approvati di 2A come rete di sicurezza.

## Decisioni (prese in brainstorming)

1. **Modalità**: tool locale, LLM-assistito, con review umana del diff. Nessun
   edit automatico non supervisionato della KB.
2. **Scope**: si consolidano SOLO le correzioni con un eval case **approvato**.
   Il caso valida il fold (l'eval, girando senza correzioni live, prova che la
   KB da sola mantiene il comportamento).
3. **Rimozione**: dopo il deploy della KB, le correzioni consolidate si tolgono
   con `!rimuovi <id>` su Discord (il tool stampa gli id). Nessun nuovo endpoint
   di scrittura.

## Goals

- Fondere le regole approvate nella KB in modo **solo additivo** (mai riscrivere
  testo esistente) e **idempotente** (salta le regole già presenti).
- Review umana via `git diff` + validazione eval prima del commit.
- Nessuna nuova dipendenza di produzione; un solo nuovo endpoint **read-only**.

## Non-goals (fuori da questo spec)

- Rewrite integrale della KB da parte dell'LLM (solo edit additivi).
- PR automatica dal bot.
- Rimozione automatica dallo store (resta `!rimuovi`).
- Scheduling (il consolidamento lo lancia George quando vuole).
- Matching per pertinenza/embeddings (Fase 2C).

## Architettura

### `rag/corrections.py` — estensione (1 funzione)
- `get_approved_corrections() -> list[dict]` — ritorna `[{"id", "venue", "rule"}]`
  per le correzioni con `case_status == "approved"`. (Sibling di
  `get_approved_cases`; serve le REGOLE, non i case.)

### `main.py` — endpoint read-only
- `GET /eval/corrections?key=<token>`: stesso `EVAL_EXPORT_TOKEN` dell'export,
  fail-closed (404 se token non settato, 403 chiave errata). Ritorna
  `{"corrections": corrections.get_approved_corrections()}`.

### `eval/consolidate_corrections.py` — tool locale (dev-only)
Scopo: orchestrare il consolidamento. Dipende da `httpx` (prod-dep), `anthropic`
(prod-dep), e legge/scrive i file KB. Gira in locale con la API key del `.env`.
- `_fetch(base_url, token) -> list[dict]` — GET `/eval/corrections`, ritorna la
  lista correzioni approvate.
- `async def propose_placement(kb_text, rule, *, client, model) -> dict` — una
  chiamata LLM (tool-use, pattern `eval/judge.py`) che propone, per UNA regola,
  `{"section": <heading KB di destinazione o "">, "line": <riga di guida da
  aggiungere>}`.
- `_apply_edit(kb_text, section, line) -> str` (puro) — inserimento **additivo**:
  - se la `line` (o il bullet `- line`) è già nel testo → ritorna invariato (dedup);
  - se `section` è un heading presente nella KB → inserisce `- line` subito dopo
    la riga di quel heading;
  - altrimenti → fallback: appende sotto una sezione dedicata
    `## Regole consolidate (da correzioni staff)` (creata se assente).
  Non riscrive MAI testo esistente.
- `main(argv)` — per ogni venue: legge `rag/knowledge/{venue}.md`; per ogni regola
  approvata di quel venue (saltando quelle già presenti nella KB): chiama
  `propose_placement`, applica `_apply_edit`; scrive il file KB; raccoglie gli id
  consolidati. Stampa il riepilogo + **la lista degli id** da rimuovere. Non
  committa, non pusha, non rimuove nulla dallo store (lo fa George).

## Flusso
`python -m eval.consolidate_corrections <base_url> --token <TOKEN>` → edit additivi
applicati ai file KB + stampa id consolidati. Poi George: `git diff` (review) →
`python -m eval.run` (valida i casi 2A contro la sola KB) → se verde → commit +
push (deploy KB) → su Discord `!rimuovi <id>` per ogni id consolidato.

## Validazione con l'eval (rete di 2A)
In locale `PERSIST_DIR` è vuoto → nessuna iniezione di correzioni live → i casi
`corrections` (importati in 2A in `eval/cases/corrections.yaml`) sono validati
**contro la sola KB**. Se passano, la KB porta il comportamento da sola: il fold
è corretto e le correzioni live possono essere rimosse.

## Error handling
- `propose_placement` fallisce (LLM error / output vuoto) → il tool salta quella
  regola con un warning, senza toccare la KB per quella regola (nessun edit parziale
  corrotto). Se nessuna regola va a buon fine, la KB resta invariata.
- Nessuna correzione approvata → "niente da consolidare", esce.
- Regola già presente nella KB → saltata (dedup).
- Endpoint: 404 senza token, 403 chiave errata.
- Heading proposto inesistente → fallback alla sezione consolidata (mai perdere la regola).

## Sicurezza
Endpoint read-only protetto da `EVAL_EXPORT_TOKEN`. Nessuna scrittura sullo store
di produzione dal tool (la rimozione resta via `!rimuovi` su Discord). Gli edit
alla KB sono additivi e revisionati da umano prima del commit.

## Testing
- `rag/corrections.py`: `get_approved_corrections` (solo approvate, shape id/venue/rule).
- endpoint `/eval/corrections`: 404 senza token, 403 chiave errata, 200 con le correzioni.
- `propose_placement` con `FakeClient`: ritorna `{section, line}` dal tool output.
- `_apply_edit` (puro): inserimento sotto heading esistente; fallback sezione
  dedicata quando l'heading manca; dedup (nessun cambiamento se la riga è già presente).
- `main`: integrazione con `_fetch` e client monkeypatchati su KB temporanea
  (applica gli edit, stampa gli id, idempotente alla seconda esecuzione).

## Deploy
Nessuna nuova env var (riusa `EVAL_EXPORT_TOKEN`). Il tool è locale: non cambia il
runtime di produzione se non per il nuovo endpoint read-only.
