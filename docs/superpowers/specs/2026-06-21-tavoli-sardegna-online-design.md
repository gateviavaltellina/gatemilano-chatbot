# Tavoli VIP Sardegna prenotabili online nel bot

Data: 2026-06-21
Branch: `feat/sardinia-tavoli-online`

## Contesto

La vendita dei tavoli VIP di Gate Sardinia è stata ufficialmente rilasciata. Il
sito `gatesardinia.it` ha un proprio sistema di prenotazione/pagamento (Revolut +
Sanity), distinto da quello di Milano (Xceed):

- Pagina di prenotazione per evento: `GET /tavoli?event=<sanityEventId>` — mappa 3D
  dei 40 tavoli (T1–T20 Terrace, V1–V20 VIP).
- Il cliente sceglie un tavolo libero, inserisce nome/telefono/email
  (`POST /api/vip/hold`): il tavolo va in `held` per 5 minuti e viene creato un
  ordine Revolut, restituendo il `checkoutUrl`.
- Pagamento 100% anticipato con carta; il webhook Revolut conferma il tavolo
  (`sold`). Se non paga entro 5 minuti l'hold scade e il tavolo torna `free`.
- Disponibilità live pubblica: `GET /api/vip/availability?event=<sanityEventId>` →
  `{ event, count, tables: [{ code, zona, coperti, price, stato }] }`.
  `stato` ∈ `libero` | `opzionato` (hold attivo) | `venduto`.

Stato attuale del bot:

- `rag/knowledge/gate_sardinia.md` afferma che i tavoli si prenotano SOLO via
  `vip@gatesardinia.it` / WhatsApp e che "non c'è un link di prenotazione online
  da girare".
- `rag/context_builder.py` (≈ righe 57-61) per la Sardegna fa `continue`: nessun
  lookup tavoli live.
- Il sync salva già `sanity_id` per ogni evento (`sync/sanity_sync.py:344`),
  quindi il bot ha l'ID necessario per costruire `/tavoli?event=<id>`.
- `ai/claude_client.py` è puro RAG: nessun tool-calling.

## Obiettivo

Il bot Sardegna deve:

1. Comunicare che i tavoli VIP sono in vendita e prenotabili/pagabili online.
2. Mostrare la disponibilità live (tavolo, zona, coperti, prezzo minimo) quando un
   evento è in contesto.
3. Girare il link di prenotazione/pagamento `gatesardinia.it/tavoli` (per-evento
   quando disponibile, generico altrimenti), che porta al checkout Revolut sul sito.

Mantenere `vip@gatesardinia.it` e WhatsApp +39 391 487 6443 come canali di
**assistenza**, non più come unico canale.

## Approccio scelto

Riuso del pattern Milano (`get_vip_tables_via_site`): pura iniezione di contesto
RAG, nessuna azione lato bot, nessun tool-use, nessuna raccolta di PII in chat.

Il link `/tavoli?event=<id>` È il link di pagamento: selezione tavolo, dati cliente
e checkout Revolut avvengono interamente sul sito. Questo evita la finestra di hold
di 5 minuti e i rischi di "pagato senza tavolo" che avrebbe un flusso di
generazione link Revolut in chat.

## Componenti

### 1. `config.py`
Nuovo campo:
```python
sardinia_site_base_url: str = "https://www.gatesardinia.it"
```
Milano resta su `site_base_url = "https://gatemilano.it"`.

### 2. `rag/event_store.py` — `get_vip_candidates`
La tupla candidato passa da `(event_name, date_iso, ticket_url)` a
`(event_name, date_iso, ticket_url, sanity_id)`. `sanity_id` è già nel metadata
(`m.get("sanity_id", "")`). Aggiornare entrambi i rami (con `date_str` e senza).

### 3. `rag/vip_tables.py` — nuova `get_vip_tables_sardinia(sanity_id)`
```
async def get_vip_tables_sardinia(sanity_id: str) -> str
```
- Se `sanity_id` vuoto → `""`.
- `GET {settings.sardinia_site_base_url}/api/vip/availability?event=<sanity_id>`.
- Non-200 / errore / nessun tavolo → `""` (fallback su knowledge statica).
- Disponibile = `stato == "libero"`. `opzionato` e `venduto` = non disponibile.
- Cache dedicata (chiave = `sanity_id`, TTL ~60s). `invalidate_vip_cache()` deve
  pulire anche questa cache.
- Output (blocco RAG):
  ```
  TAVOLI VIP DISPONIBILI (prenotazione e pagamento online):
  - Terrace T3 — max 10 persone: minimo €600 → libero
  - VIP V12 — max 6 persone: minimo €300 — NON DISPONIBILE
  PRENOTA E PAGA ONLINE: https://www.gatesardinia.it/tavoli?event=<sanity_id>
  ```
  Riga zona: usare `zona` + `code`; `coperti` → "max N persone"; `price` → "minimo €N".
- Se nessun tavolo libero: blocco "TAVOLI VIP: tutti esauriti per questo evento."
  comunque seguito dal link `/tavoli?event=<id>` (il cliente può comunque vedere la
  mappa / liste d'attesa). Un solo link per evento.

### 4. `rag/context_builder.py`
Sostituire il ramo Sardegna:
```python
elif venue == "gate_sardinia":
    result = await get_vip_tables_sardinia(sanity_id)
```
dove `sanity_id` è il quarto elemento della tupla candidato. Aggiornare l'unpacking
del `for ... in candidates`.

### 5. `rag/knowledge/gate_sardinia.md`
Sezione "Due prodotti VIP distinti":
- Tavoli VIP (bottle service): minimo di spesa come da tabella, ingresso incluso,
  **prenotabili e pagabili online su `gatesardinia.it/tavoli`** (pagamento 100%
  anticipato con carta, conferma immediata). Per assistenza: `vip@gatesardinia.it`
  o WhatsApp +39 391 487 6443.
- Rimuovere "Al momento la prenotazione è solo tramite ... (nessun link di
  prenotazione online da girare)".
- Citare la pagina generica `gatesardinia.it/tavoli` così il bot la può fornire
  anche senza un evento specifico in contesto.
- Aggiornare coerentemente la tabella "Contatti per Tipo di Richiesta" (canale
  tavoli: online su gatesardinia.it/tavoli + email/WhatsApp per assistenza).

### 6. `eval/cases/sardinia_vip.yaml`
Ribaltare le assunzioni del vecchio comportamento:
- `must`: indirizza alla prenotazione/pagamento online su `gatesardinia.it/tavoli`
  (email/WhatsApp come supporto, non unico canale).
- `must_not "Inventa un link di prenotazione del tavolo"` → riformulare in
  "inventa un link **falso/diverso** da gatesardinia.it/tavoli".
- `sard-vip-tables-3` ("mappa/prenotazione online NON attiva", "non fornisce link")
  → ribaltare: deve fornire il link online `gatesardinia.it/tavoli`.
- Invariati i fatti: minimo di SPESA (non persone), €600/€300, extra €60/€50,
  età 18+, drinklist PDF, nessun rimborso (credito spostabile entro fine stagione),
  arrivo max 02:30, distinzione Tavolo vs Ticket VIP, forbidden_substrings
  (gatemilano, Xceed, orari di Milano).

### 7. Test
Nuovo `tests/test_vip_tables_sardinia.py` (pattern `monkeypatch` del repo):
- Risposta con mix `libero`/`venduto`/`opzionato` → il blocco elenca i liberi come
  disponibili, gli altri come NON DISPONIBILE, e include
  `PRENOTA E PAGA ONLINE: .../tavoli?event=<id>`.
- Risposta vuota (`tables: []`) → `""`.
- Non-200 / eccezione → `""`.
- `sanity_id` vuoto → `""` senza chiamate di rete.

## Error handling
Ogni errore di rete o risposta non valida → `get_vip_tables_sardinia` ritorna `""`,
il bot ricade sulle info statiche della knowledge (link generico). Mai sollevare,
mai bloccare la risposta. Stesso contratto di `get_vip_tables_via_site`.

## Fuori scope
- Flusso #2: generazione in chat del link Revolut diretto via `POST /api/vip/hold`
  (richiederebbe tool-use in `claude_client.py`, raccolta PII, gestione della race
  dei 5 minuti). Possibile fase futura.
- Modifiche al sito gatesardinia.it.
