# Gate Sardinia — enrichment prezzi/descrizione da TicketSMS (Fase 2, parziale)

**Data:** 2026-06-18 · **Contesto:** gli eventi Gate Sardinia arrivano già da Sanity (41 eventi, link ticketsms) e sono già live nel bot. Mancavano **prezzi e descrizione** (Milano li prende da Xceed; per la Sardegna non c'era enrichment perché i biglietti sono su TicketSMS).

## Premessa importante
Il "token Sanity" fornito **non serviva**: il dataset Sanity Sardegna (`1999xgdy`) è pubblico e già letto senza auth. Quel token è per-progetto (401 su Milano) e, peggio, aggiunto come env var **crasherebbe l'app** (pydantic `extra_forbidden`, manca il campo in config). Non è stato wired. → ruotarlo/revocarlo su Sanity.

## Endpoint TicketSMS (reverse-engineered)
TicketSMS non ha API pubblica documentata, ma il backend della loro SPA è leggibile **senza auth**:

- Base API: ricavata dall'env-lambda della SPA → `BACKEND_API_V4_BASE` / `…/api/v3`.
- **Usato:** `GET https://backend.ticketsms.it/api/v3/events/<codeUrl>` → `data.body[]` con:
  - componenti `eventDetails` → `description` (Quill Delta JSON)
  - componenti `ticket` → `typeTicketDescription`, `price.formatted/amount`, `sector.name`, `stato`
  - `ticketsPriceMin` (es. "a partire da €11.50")
- `<codeUrl>` = slug nell'URL pubblico `https://www.ticketsms.it/event/<slug>`.
- Auth Bearer presente solo per utenti loggati (wallet) → letture evento pubbliche non la richiedono.

## Implementazione
`sync/sanity_sync.py` (additivo, Milano invariato):
- `_extract_ticketsms_slug`, `_quill_to_text`, `_parse_ticketsms_event` (puro, testato offline), `_fetch_ticketsms_enrichment` (non solleva mai).
- Branch nel loop sync: `elif "ticketsms" in ticket_url → _fetch_ticketsms_enrichment`. Restituisce `{about, prices_str}`, stessa forma dell'enricher Xceed → `_build_document` li rende come "Descrizione:" e "Prezzi:".
- `prices_str` = "a partire da €X" di TicketSMS + **minimo per settore** (es. Posto Unico €10, VIP €45). Scelta: NON si elencano tutti gli scaglioni (TicketSMS li marca tutti `active`, rischio di mostrare tier non più acquistabili) — il "a partire da" è sempre onesto.
- Test: `tests/test_ticketsms_enrichment.py` (slug, quill, parse min-per-settore, safety su input vuoto/malformato).

Esempio output nel contesto del bot (Perreo XL 4 lug):
```
Prezzi:
  a partire da €11.50
  - Posto Unico: a partire da €10.00
  - VIP: a partire da €45.00
```

## Limiti / follow-up
- **Brittleness**: endpoint interno non versionato pubblicamente; il bundle JS è hash-named. Se TicketSMS cambia, l'enricher fallisce in silenzio (graceful: il bot resta col link, senza prezzi). Stessa categoria di rischio degli scraper Xceed/Dice già presenti.
- Prezzi uomo/donna (Perreo) collassati nel "a partire da" del settore — accettabile.
- Tavoli VIP (minimi €600/€300) restano via email/WhatsApp finché la mappa di prenotazione non è pronta: TicketSMS espone i *ticket* VIP (accesso area), non i *tavoli* bottle-service.
