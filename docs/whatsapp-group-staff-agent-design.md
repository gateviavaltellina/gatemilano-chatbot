# Design — Agent di coordinamento staff in un gruppo WhatsApp

Data: 2026-06-06
Stato: **MVP implementato** (Groups API ufficiale confermata)

## Aggiornamento — schema confermato + MVP in codice (2026-06-06)

Groups API ufficiale **confermata** dalla doc Meta (Official Business Account).
Schema reale pinnato dalla reference Groups Messaging:

- **Invio**: `POST /v25.0/{PHONE_NUMBER_ID}/messages` con
  `{"messaging_product":"whatsapp","recipient_type":"group","to":"<GROUP_ID>","type":"text","text":{...}}`
- **Ricezione**: stesso webhook dei DM; il messaggio in `value.messages[]` ha in
  più il campo **`group_id`** (e `from` = telefono del partecipante).

MVP in codice (testato, suite 32/32):
- `whatsapp/client.py` → `send_group_message(group_id, text)`
- `whatsapp/group.py` → `process_group_message` + comandi `!help !eventi !stasera
  !lineup !gate`, gate a prefisso (no spam), allowlist **default chiuso**
- `whatsapp/webhook.py` → rileva `group_id` e dirotta al ramo gruppo
- `config.py` / `.env.example` → `WA_GROUP_ALLOWLIST`

**Prossimo passo (spike runtime):** creare un gruppo di test (Group Management
API o WhatsApp Manager), mandare un messaggio nel gruppo → il `group_id` compare
nei log Railway ("Messaggio gruppo IGNORATO ... group_id"), copiarlo in
`WA_GROUP_ALLOWLIST` → l'agent risponde ai comandi. Poi: prompt staff dedicato,
brief proattivo, promemoria (vedi §9).

---


## 1. Obiettivo e scenario

Portare un agent (stesso "cervello" Claude del chatbot esistente) dentro un
**piccolo gruppo WhatsApp di staff** (≤ 8 membri) per il **coordinamento interno**:
lookup eventi/lineup, promemoria, brief proattivi, ponte verso le automazioni
(Sanity/Xceed, dropbox-watcher).

Canale: **WhatsApp Cloud API ufficiale — Groups API** (stesso numero business
+39 391 487 6443, Official Business Account già attivo). Niente librerie non
ufficiali → nessun rischio ban/ToS.

## 2. Vincoli (verificati su fonti Meta/BSP, giugno 2026)

- **Max 8 membri** per gruppo (oltre al business account); limite 10.000 gruppi.
- I gruppi sono **creati e gestiti dal business** (non si entra in gruppi creati
  dagli utenti).
- **Niente messaggi interattivi** nei gruppi (bottoni/liste), niente template con
  bottoni, niente commerce/auth/disappearing/view-once.
- **Prezzo per-messaggio scalato sui destinatari**: un messaggio a un gruppo di N
  membri = N addebiti, SALVO la **finestra di servizio gratuita** che si apre/si
  rinnova quando un membro scrive al business (free-form + utility/marketing in
  quella finestra a costo zero).
- Non si può: nascondere la lista partecipanti, modificare/eliminare messaggi,
  segnare come letto.

> ⚠️ DA CONFERMARE sulla doc ufficiale Meta (la pagina è una SPA non scrapabile):
> i nomi esatti di endpoint e campi della Groups API (vedi §6). Tutto il resto
> dell'architettura è indipendente da questi dettagli.

## 3. Architettura (mappata sul codice esistente)

Stesso pattern del chatbot 1-1 (`whatsapp/webhook.py` → `generate_response` →
`whatsapp/client.py`), con 4 differenze. Riusa: Claude client, knowledge base,
prompt caching, persistenza, notifiche Discord.

| Pezzo | Bot 1-1 attuale | Agent di gruppo |
|------|------------------|-----------------|
| Ingresso | webhook → `msg.from` (telefono) | stesso webhook → **group id** + sender |
| Quando risponde | a ogni DM | **solo su trigger** (menzione/comando) |
| Contesto | per-telefono (`_conversations[phone]`) | **per-gruppo** (`_group_conversations[group_id]`) |
| Uscita | `send_message(phone, text)` | `send_group_message(group_id, text)` |
| Cervello | `generate_response(...)` | **lo stesso** (system prompt dedicato "staff") |

```
WhatsApp Group ──► Meta Cloud API ──► /webhook (FastAPI, già esistente)
                                          │
                                   è un messaggio di gruppo?
                                          │ sì
                                   trigger? (@menzione o "!gate ...")
                                          │ sì
                            build context staff (eventi/lineup da Sanity/Xceed)
                                          │
                                generate_response (Claude)  ◄── system prompt STAFF
                                          │
                            send_group_message(group_id, reply)
```

## 4. Trigger — quando l'agent parla (fondamentale)

In un gruppo NON si risponde a tutto. Regole:
- Risponde **solo** se: (a) menzionato (`@Gate` / il numero del bot citato), oppure
  (b) il messaggio inizia con un **prefisso comando** (es. `!gate ...`, `/gate ...`).
- Comandi rapidi dedicati (deterministici, no LLM): `!eventi`, `!lineup <evento>`,
  `!stasera`, `!promemoria <quando> <testo>`.
- Tutto il resto del traffico di gruppo: **ignorato** (ma conta per tenere viva la
  finestra gratuita e per il contesto).
- Anti-loop: ignora i messaggi inviati dal business stesso (echo).

## 5. Capacità dell'agent staff (riuso dati esistenti)

Attinge alle stesse fonti del chatbot + watcher:
- **Eventi & lineup** (Sanity + Xceed): "chi suona venerdì?", "lineup di Aftermath?",
  "eventi del weekend?", "a che ora apre stasera?". (riusa `rag/event_store`,
  `sync/*`, e la lineup reale Xceed — utile anche per la "Option B" del watcher).
- **Brief proattivo**: ogni mattina (o on-demand) posta nel gruppo gli eventi di
  stasera + lineup + sold-out. Sfrutta la finestra gratuita.
- **Promemoria**: "ricordami alle 18 di…" → scheduler (APScheduler già in `main.py`).
- **Ponte automazioni**: stato sync, alert dal dropbox-watcher, ecc.

## 6. API WhatsApp Groups — meccaniche (shape attesa, campi DA CONFERMARE)

Seguendo le convenzioni Cloud API (`POST /{PHONE_NUMBER_ID}/...`, webhook sotto
`entry[].changes[].value`). I nomi esatti vanno verificati sulla doc Groups prima
di scrivere il client.

- **Creare gruppo / gestire membri**: `POST /{PHONE_NUMBER_ID}/groups`
  (subject + lista membri). Add/remove membri: sotto-risorsa del gruppo.
- **Inviare al gruppo**: probabilmente `POST /{PHONE_NUMBER_ID}/messages` con un
  destinatario di tipo gruppo (`recipient_type: "group"` + identificatore gruppo),
  oppure endpoint dedicato. → CONFERMARE.
- **Ricevere**: stesso webhook `whatsapp_business_account`; i messaggi di gruppo
  arrivano in `value.messages[]` con un **identificatore di gruppo** (group id) e
  il `from` del singolo mittente. → CONFERMARE il campo esatto del group id.

> Nota: appena costruiamo, il primo task è una chiamata di prova (crea gruppo +
> invio + cattura di un webhook reale) per **pinnare lo schema** prima di scrivere
> la logica.

## 7. Componenti da costruire (file-by-file)

Nuovi / modifiche, in stile col codice esistente:
- `whatsapp/group_client.py` — `send_group_message(group_id, text)`,
  `create_group(subject, members)`, gestione membri. (gemello di `whatsapp/client.py`)
- `whatsapp/webhook.py` — ramo "messaggio di gruppo": dedup, gate trigger,
  dispatch a `process_group_message`.
- `whatsapp/group_webhook.py` (o sezione in webhook): `process_group_message`,
  contesto `_group_conversations[group_id]`, comandi rapidi, chiamata a Claude.
- `ai/claude_client.py` — un **system prompt "staff"** dedicato (tono interno,
  niente regole customer-facing; conosce lineup/eventi/automazioni).
- `persistence.py` — aggiungere `_group_conversations` allo snapshot (già pronto a
  estendersi).
- Comandi proattivi: job APScheduler per il brief; promemoria.

## 8. Sicurezza e costi

- **Allowlist membri**: l'agent risponde solo se il gruppo è uno di quelli noti
  (group id in whitelist) → evita abusi.
- **Costo**: per-messaggio scalato sui membri, ma con un gruppo staff piccolo e la
  finestra gratuita (lo staff scrive spesso) il costo reale è basso. Il brief
  proattivo fuori finestra costa N messaggi → valutare orario.
- **Firma webhook**: già implementata (`META_APP_SECRET`) — vale anche qui.

## 9. Piano a fasi

1. **Spike API** (mezza giornata): crea un gruppo di test via API, manda un
   messaggio, cattura un webhook reale → pinnare lo schema Groups (§6).
2. **MVP**: ramo gruppo nel webhook + gate menzione/comando + `send_group_message`
   + 2-3 comandi (`!eventi`, `!lineup`, `!stasera`) usando i dati Sanity/Xceed.
3. **Proattivo**: brief mattutino + promemoria.
4. **Hardening**: allowlist, persistenza gruppo, test, deploy Railway.

## 10. Alternativa da pesare prima di costruire — Discord

Per il coordinamento staff **puro**, Discord (già attivo col bot del chatbot) è:
gratis, nativo per i bot, senza cap di 8, e riuserebbe ancora più codice
(`notifications/discord_bot.py`). WhatsApp-gruppo ha senso se lo staff vive su
WhatsApp e non lo vuoi spostare. Decisione da prendere prima della Fase 1.

## 11. Aperti / da confermare
- Schema esatto Groups API (§6) — spike Fase 1.
- Lo staff vuole davvero WhatsApp o va bene Discord? (§10)
- Lista comandi prioritari e dati esposti.
- Il brief proattivo: orari e costo accettabile.
