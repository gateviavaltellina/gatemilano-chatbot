# Corrections → Eval Cases (Fase 2A) — Design

**Data:** 2026-06-14
**Stato:** approvato (design), pronto per il piano di implementazione
**Autore:** George + Claude

## Contesto e problema

La feature "Staff Corrections Live" (PR #1, in produzione) permette di correggere il
bot in tempo reale da Discord. Ogni correzione salva `rule` + `example = {user_msg,
wrong_reply}`. Ma le correzioni non sono protette da regressioni future: se in seguito
cambiamo prompt/KB (o consolidiamo le correzioni nella KB, Fase 2B), nulla garantisce
che un comportamento corretto non si rompa di nuovo.

Obiettivo: ogni correzione genera un **eval case di regressione**, così la suite cresce
da sola e blinda le correzioni. È la sinergia "correzione = test".

## Decisioni (prese in brainstorming)

1. **Trigger/approvazione**: al `!regola` viene generata SUBITO una BOZZA di eval case
   (stato `pending`); George la approva su Discord con `!approva <id>` e solo allora è
   "approvata". Controllo umano sulla qualità del test.
2. **Fedeltà contesto**: caso **rule-level** con contesto sintetico/vuoto. Nessuna
   modifica alla cattura già in produzione. L'LLM deriva il caso da `rule` + `example`.
3. **Sync verso il repo**: i casi approvati vivono sul volume Railway; un **endpoint
   HTTP** li espone come JSON; uno script locale li converte in YAML e li committa in
   `eval/cases/corrections.yaml`. L'endpoint è protetto da token (fail-closed).

## Goals

- Ogni `!regola` produce una bozza di eval case rivedibile su Discord.
- Solo dopo `!approva` il caso è esportabile.
- I casi approvati raggiungono il repo in modo idempotente (dedup per id).
- Nessuna nuova dipendenza di produzione; nessuna modifica al path di cattura.
- La generazione fallita non blocca la correzione (la regola resta valida).

## Non-goals (fuori da questo spec)

- Cattura del rag_context reale (resta rule-level).
- PR automatica dal bot (sync via endpoint + commit in sessione).
- Comando `!rifiuta` separato (per scartare basta `!rimuovi` la correzione).
- Consolidamento correzioni→KB (Fase 2B) e matching per pertinenza/embeddings (Fase 2C).

## Architettura

### `rag/correction_cases.py` — il generatore (nuovo)
Scopo: trasformare una correzione in un eval case strutturato via LLM.
- `async def draft_case(correction: dict, *, client, model: str) -> dict` — una chiamata
  LLM con tool-use forzato (stesso pattern di `eval/judge.py`): input `rule`, `user_msg`,
  `wrong_reply`; output del tool `draft_eval_case`: `user_message`, `rag_context`
  (sintetico/minimo, spesso `""`), `must` (list), `must_not` (list), `forbidden_substrings`
  (list, opzionale). La funzione assembla il case dict completo nello schema eval:
  `{id: f"corr-{correction['id']}", category: "corrections", venue, user_message,
  rag_context, rubric: {must, must_not}, assertions: {forbidden_substrings}}`.
- `client` iniettabile → testabile col `FakeClient` di `tests/conftest.py`.
- Dipendenze: `anthropic` (già in produzione). Nessun `pyyaml` (lo schema è un dict).

### `rag/corrections.py` — estensione store
La bozza vive ATTACCATA alla correzione (un solo store, già persistito):
- Nuovi campi nel record: `case` (dict, lo schema eval) e `case_status`
  (`"pending"` | `"approved"`; assente se nessuna bozza).
- `set_case(correction_id, case) -> bool` — attacca la bozza, stato `pending`, salva.
- `approve_case(correction_id) -> bool` — porta `case_status` a `approved`, salva.
- `get_approved_cases() -> list[dict]` — ritorna i `case` con `case_status == "approved"`.

### `notifications/discord_bot.py` — comandi
- `!regola <direttiva>` (esteso): salva la regola (come ora) → genera la bozza via
  `correction_cases.draft_case` (await, client condiviso) → `set_case` → la risposta
  mostra `must`/`must_not` della bozza + "approva con `!approva <id>`". Se la generazione
  LLM fallisce, la correzione resta salvata e la risposta avvisa "regola ok, bozza non
  generata". Il path `!regola` diventa async per poter attendere la generazione.
- `!approva <id>` (nuovo): `approve_case(id)` → ✅ / ❌ se id sconosciuto o senza bozza.
- `!regole` / `!rimuovi <id>`: invariati (operazioni sincrone sullo store).

### `main.py` — endpoint export
- `GET /eval/correction-cases?key=<token>`:
  - se `settings.eval_export_token` è vuoto → `404` (endpoint disabilitato, sicuro di default);
  - se `key` non combacia → `403`;
  - altrimenti → JSON `{"cases": get_approved_cases()}`.

### `config.py` — nuovo setting
- `eval_export_token: str = ""` (env `EVAL_EXPORT_TOKEN`). Vuoto = endpoint disabilitato.

### `eval/import_correction_cases.py` — importer locale (nuovo, dev-only)
Scopo: portare i casi approvati nel repo.
- `python -m eval.import_correction_cases <base_url> --token <TOKEN>`: fa GET
  dell'endpoint, per ogni caso lo converte in voce YAML, **deduplica per `id`** rispetto
  a `eval/cases/corrections.yaml` (salta gli id già presenti), appende i nuovi e riscrive
  il file. Idempotente. Usa `httpx`/`pyyaml` (dev-deps; gira in locale, non in produzione).

## Flusso dati
`!regola <direttiva>` → `add_correction` → `draft_case` (LLM) → `set_case(id, bozza)` →
Discord mostra la bozza → `!approva <id>` → `approve_case`. In sessione:
`python -m eval.import_correction_cases <url> --token ...` → scrive in
`eval/cases/corrections.yaml` (dedup) → `python -m eval.run` per confermare che i nuovi
casi passano → commit.

## Error handling
- `draft_case` fallisce (errore API / output malformato) → la correzione resta salvata,
  nessun `case` attaccato; Discord avvisa. La regola è comunque attiva.
- `!approva <id>` con id sconosciuto o senza bozza → ❌.
- Endpoint: token non configurato → 404; chiave errata → 403; nessun caso → `{"cases": []}`.
- Importer: idempotente, salta gli id già presenti; se l'endpoint è irraggiungibile,
  errore esplicito senza scrivere nulla.

## Sicurezza
L'endpoint espone `user_message` (testo cliente). Protetto da `EVAL_EXPORT_TOKEN`,
fail-closed (404 se il token non è configurato). Le correzioni continuano ad arrivare
solo dal canale Discord access-controlled.

## Testing
- `draft_case` con `FakeClient`: lo schema del case generato (id `corr-...`, category
  `corrections`, venue dalla correzione, rubric da output tool); robustezza su output
  parziale.
- store: `set_case` / `approve_case` / `get_approved_cases` (+ round-trip persistenza).
- `handle` comandi: `!approva` (approva / id sconosciuto); `!regola` con fake client
  genera e attacca la bozza (gli unit test esistenti del `!regola` aggiornati per il
  path async + client iniettato).
- endpoint: `TestClient` — 404 senza token, 403 con chiave errata, 200 con i casi.
- importer: conversione JSON→YAML + dedup per id (su file temporaneo).

## Deploy
Nuova env var su Railway: `EVAL_EXPORT_TOKEN` (un segreto a piacere). Senza, l'endpoint
resta disabilitato (la generazione/approvazione su Discord funziona comunque; solo
l'export è off finché non c'è il token).
