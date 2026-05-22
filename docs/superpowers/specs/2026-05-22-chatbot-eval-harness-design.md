# Eval Harness per il chatbot Gate Milano — Design

Data: 2026-05-22
Branch: `feat/eval-harness`

## Problema

Il chatbot (`ai/claude_client.py`) è guidato da un system prompt di ~90 righe di
regole più contesto RAG. Ogni modifica per correggere un comportamento rischia di
romperne un altro, e oggi non c'è modo di misurarlo: si modifica a naso. Serve una
rete di sicurezza che, dato un insieme di casi reali, dica se una modifica al prompt
migliora o peggiora il comportamento complessivo.

Questo spec copre **solo l'eval harness** (la fondazione). Le correzioni al prompt e
al RAG sono un lavoro successivo, validato contro questo harness, con spec proprio.

## Dati di partenza

316 scambi reali estratti dal canale Discord `whatsapp-bot` (2–21 maggio 2026),
salvati in `/tmp/gate_convos.json`. Da copiare nel repo come
`eval/data/discord_sample_2026-05.json` (numeri di telefono già mascherati alla
fonte). Da questi sono derivati i casi di test.

### Failure mode osservati (prompt-addressable, da codificare nei casi)

1. **Espone i limiti interni** (alto, ricorrente). Formulazioni non coperte dalla
   regola attuale del prompt:
   - "non ho lo storico delle conversazioni / ogni conversazione riparte da zero"
   - "non ho accesso alle email / non posso vedere le email ricevute"
   - "non riesco a vedere immagini o allegati"
   - "nel mio calendario / sistema / database non ho…"
2. **Prezzi/zone tavoli VIP errati** (alto impatto economico):
   - F5 dato a €300 invece di €600 (è premium, non standard)
   - logica del minimo incoerente ("minimo 8 persone" vs "in 5 va bene €300")
   - upsell verso zona più cara non richiesta (Console €500 a gruppo di 6)
3. **Speculazione su orari/timetable** (medio): inventa orari di apertura/inizio
   non presenti nel contesto ("probabilmente apertura porte alle 17:00").
4. **"Stasera" dopo mezzanotte** (edge case): confusione sabato/domenica nelle ore
   piccole di una serata-evento.

### Fuori scope (traccia separata, NON prompt)

- **Freschezza RAG / sync**: la stessa query ("9 maggio") ha restituito 3 risposte
  diverse in pochi minuti per disallineamento del sync/cache dell'event store.
  È un problema di dati, non di prompt. Va affrontato in un fix dedicato e NON
  deve inquinare l'eval: per questo i casi usano contesto RAG **congelato**.
- Comportamenti già risolti dall'evoluzione del prompt (markdown grassetto, emoji
  eccessive, telefono `+39 391 487 6443`): non servono casi nuovi, ma alcune
  asserzioni di regressione li coprono comunque.

## Architettura

Tre componenti con responsabilità separate, sotto `eval/`.

```
eval/
  data/
    discord_sample_2026-05.json   # campione reale (riferimento, non eseguito)
  cases/
    *.yaml                        # ~25-30 casi di test
  run.py                          # runner: esegue i casi, chiama il bot
  judge.py                        # LLM-as-judge: valuta vs rubrica
  report.py                       # rendering report + diff vs run precedente
  results/
    <timestamp>.json              # output grezzo di ogni run (per il diff)
```

### 1. Test set — `eval/cases/*.yaml`

Ogni file raggruppa casi per failure mode (es. `system_exposure.yaml`,
`vip_tables.yaml`, `hours.yaml`, `date_logic.yaml`, `regression.yaml`).

Schema di un caso:

```yaml
- id: sysexp-storico-conversazioni
  category: system_exposure
  venue: gate_milano
  # opzionale: turni precedenti per casi multi-turno
  history:
    - role: user
      content: "Ciao"
    - role: assistant
      content: "Ciao! Come posso aiutarti?"
  user_message: "Sono il ragazzo che ti ha chiamato Pietro"
  # contesto RAG congelato: stringa catturata, così l'eval non dipende dal sync live
  rag_context: |
    (snapshot del contesto, o vuoto se non rilevante)
  rubric:
    must_not:
      - "Non deve dire che non ha storico/memoria delle conversazioni come fosse un limite di sistema"
      - "Non deve menzionare email/database/calendario/sistema interni"
    must:
      - "Deve rispondere in modo naturale e chiedere come aiutare"
```

`rubric` contiene criteri in **linguaggio naturale** (valutati dal judge), divisi in
`must` e `must_not`. Niente regex fragili nel set principale — quelle vivono solo
nelle asserzioni deterministiche opzionali (vedi sotto).

Asserzioni deterministiche opzionali per caso (controllo a costo zero, pre-judge):

```yaml
  assertions:
    forbidden_substrings: ["database", "+39 391 487 6443"]
    forbidden_markdown: true        # niente *bold* / bullet
```

### 2. Runner — `eval/run.py`

- Carica tutti i casi da `eval/cases/*.yaml`.
- Per ogni caso chiama `ai.claude_client.generate_response(venue, user_message,
  rag_context, history)` — la **stessa funzione di produzione**, così l'eval misura
  il comportamento reale, non una copia.
- Esegue prima le `assertions` deterministiche (fail immediato se violate, senza
  spendere token sul judge).
- Raccoglie `{id, category, user_message, reply, assertion_failures}` e passa al judge.
- Concorrenza limitata (es. `asyncio.Semaphore(5)`) per non saturare l'API.
- Salva il grezzo in `eval/results/<timestamp>.json`.

**Prompt caching nel runner — RINVIATO alla fase 2.** Il runner chiama
`generate_response()` **as-is**, senza modifiche. Motivo: per ottenere cache hit
servirebbe riordinare il system prompt (oggi `datetime` e `rag_context` variabili
stanno in mezzo alle regole statiche → un breakpoint di cache darebbe hit ~zero), e
riordinare cambia il comportamento di produzione. Dato che le modifiche al prompt
sono la fase successiva validata *contro* questo harness, il caching del prompt di
produzione diventa la **prima modifica validata dal diff** dopo il baseline, non
parte della costruzione dell'harness. Senza caching il costo di una run (~60
chiamate) resta comunque trascurabile.

### Verità di riferimento — tavoli VIP Perreo

Fonte unica: `rag/prices.py` (`PERREO_TABLES`). Regola **fissa fino a giugno**.
I casi VIP devono essere scritti contro questi valori:

| Zona          | Tavoli | Minimo | Max persone | Extra/persona |
|---------------|--------|--------|-------------|---------------|
| Face premium  | F1–F5  | €600   | 10          | €50           |
| Face standard | F6–F21 | €300   | 8           | €35           |
| Balcony       | B1–B5  | €300   | 8           | €35           |
| Console       | C1–C3  | €500   | 10          | €50           |

Regole derivate (da codificare nelle rubriche):
- NON esiste un "minimo di persone": il minimo è di **spesa**, non di teste.
- Persone oltre il `max_people` pagano `extra_per_person` **alla porta**, il minimo
  online resta invariato.
- F5 è **premium (€600)**, non standard — questo è esattamente l'errore osservato.
- Ingresso incluso nel minimo; il minimo è di bottiglie, non drink singoli.

Eventi **non-Perreo**: non c'è regola fissa. Il contesto VIP (solo tavoli / anche
backstage / solo backstage / nessuno) arriva a runtime dal lookup Xceed via UUID.
Per l'eval si **congela uno snapshot** del contesto Xceed per caso; le rubriche
verificano solo che il bot non inventi opzioni non presenti nello snapshot.

### 3. Judge — `eval/judge.py`

- Modello del judge: **Sonnet** (`settings.model`), per contenere costi e impatto
  economico. Reso configurabile via parametro/env, ma default Sonnet.
- Per ogni risposta, un secondo Claude riceve: `user_message`, `reply`, e la
  `rubric` del caso.
- Output strutturato (tool use / JSON): `{verdict: pass|fail, violated: [...],
  reasoning: "..."}`.
- Il judge valuta SOLO contro la rubrica del caso, non con criteri propri, per
  evitare giudizi arbitrari.
- Anche il judge usa prompt caching sulla parte di istruzioni fissa.

### 4. Report — `eval/report.py`

- Tabella per categoria: `pass / total`, `%`.
- Elenco dei fail con `id`, motivazione del judge, e la risposta del bot.
- **Diff vs run precedente**: confronta `eval/results/<latest>` con il penultimo;
  evidenzia regressioni (pass→fail) e miglioramenti (fail→pass). Questo è il segnale
  chiave dopo una modifica al prompt.
- Output su stdout (testo) + opzionale `eval/results/<timestamp>.md`.

## Flusso d'uso

```
python -m eval.run            # baseline
# ... modifico ai/claude_client.py ...
python -m eval.run            # nuova run
python -m eval.report --diff  # confronto: regressioni vs miglioramenti
```

## Dipendenze

- `pyyaml` (parsing casi) — da aggiungere a `requirements.txt`.
- Nessun framework di eval esterno: ~3 file Python, mantenibili e leggibili.

Nota operativa: `requirements.txt` risulta cancellata sul working tree (pre-esistente,
non parte di questo lavoro). Va ripristinata (`git checkout requirements.txt`) prima
di aggiungere `pyyaml`.

## Costi

Ogni run = ~30 casi × 2 chiamate (bot + judge) ≈ 60 chiamate. Con prompt caching e
`max_tokens` contenuto, costo per run basso (ordine di pochi centesimi). Le run sono
on-demand, non in CI automatica (almeno in questa fase).

## Criteri di completamento

- `python -m eval.run` esegue tutti i casi senza errori e produce un file in
  `eval/results/`.
- `python -m eval.report --diff` mostra la tabella per categoria e il diff vs run
  precedente.
- Almeno i 4 failure mode prompt-addressable sono coperti da ≥3 casi ciascuno.
- Il prompt caching è attivo nel **judge** (prefisso istruzioni statico,
  verificabile dai `usage.cache_read_input_tokens` nei risultati grezzi). Il caching
  del prompt di produzione è rinviato alla fase 2 (prima modifica validata dal diff).
- Eseguito il baseline iniziale: sappiamo quanti casi il prompt attuale fallisce
  (questo è il punto di partenza per il lavoro successivo sul prompt).
```
