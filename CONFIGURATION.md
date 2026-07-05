# Configurazione & Runbook — Gate Chatbot

Guida operativa unica per Gate Milano + Gate Sardinia (Instagram DM + WhatsApp).
Pensata per lo staff: cosa è configurato, dove, e cosa fare quando qualcosa si rompe.

---

## 1. Panoramica

- **Cosa fa**: risponde in automatico ai DM Instagram (@gatemilano, @gatesardinia) e ai
  messaggi WhatsApp, con eventi/prezzi/tavoli presi da Sanity + biglietterie.
- **Hosting**: Railway (deploy automatico dal branch `main` del repo GitHub).
  Il push su GitHub **non** aggiorna la produzione da solo: serve il deploy di Railway.
- **Verifica versione in esecuzione**: `GET /health` → campo `version` = SHA del commit live.

---

## 2. Canali, API e token — LA PARTE CHE CONTA PER LE SCADENZE

| Canale | API usata | Token (env) | Scadenza |
|---|---|---|---|
| WhatsApp | `graph.facebook.com` | `WA_ACCESS_TOKEN` | **PERMANENTE** (System User, `expires_at: 0`) |
| Instagram @gatemilano | `graph.instagram.com` | `IG_GATEMILANO_TOKEN` | 60 giorni → **auto-rinnovato** |
| Instagram @gatesardinia | `graph.instagram.com` | `IG_GATESARDINIA_TOKEN` | 60 giorni → **auto-rinnovato** |

### WhatsApp — permanente
Usa un token **System User** (app "Milano & Sardinia"). I System User token non scadono
(`expires_at: 0`). Si invalidano solo se qualcuno li revoca a mano su Meta Business, o
per cambio permessi/app. La sentinella (§5) avvisa se succede.

### Instagram — 60 giorni ma auto-rinnovato → di fatto permanente
I token Instagram Login durano 60 giorni. Il bot li **rinnova da solo ogni lunedì**
(`instagram/token_refresh.py`), estendendoli di altri ~60 giorni ogni volta, e salva il
nuovo token sul volume. Finché il bot è attivo, non scadono mai.

> ⚠️ **REQUISITO CRITICO PER L'AUTO-RINNOVO**: la variabile **`PERSIST_DIR`** deve puntare
> a un **volume Railway montato** (es. `/data`). Senza volume, il token rinnovato resta
> solo in memoria e va perso a ogni deploy/riavvio, tornando al token originale che poi
> scade. **Verifica su Railway che `PERSIST_DIR` sia impostata e che ci sia un volume.**

### Perché graph.facebook.com per WhatsApp e graph.instagram.com per Instagram?
Sono due prodotti Meta diversi. Il token System User (Facebook) **non** invia DM Instagram
su questa app (manca la capability): Instagram richiede i token "Instagram Login" su
`graph.instagram.com`. Non mescolare i due: `IG_API_URL` deve restare
`https://graph.instagram.com/v22.0`.

---

## 3. Identificatori (non segreti)

| Cosa | Valore |
|---|---|
| App Meta | Milano & Sardinia — app_id `1969337093671796` |
| System User | `122100739077292105` |
| IG @gatemilano | id business `17841405933946552` · id legacy `35517015101275600` |
| IG @gatesardinia | id business `17841452139166980` · id legacy `24588954374135134` |
| WhatsApp numero | `+39 391 487 6443` — phone_number_id `1141424935713428` |
| WhatsApp Business Account (WABA) | `1172226671633211` |

---

## 4. Variabili d'ambiente (Railway)

**Segrete (non committare):**
- `ANTHROPIC_API_KEY` — API Claude
- `WA_ACCESS_TOKEN` — token System User (permanente)
- `IG_GATEMILANO_TOKEN`, `IG_GATESARDINIA_TOKEN` — token Instagram Login (IGAA…)
- `META_APP_SECRET` — verifica firma webhook (consigliata)
- `SANITY_API_TOKEN` — opz., per leggere anche le bozze eventi
- `DEBUG_KEY` — opz., protegge gli endpoint `/debug/*` (vedi §6)
- `DISCORD_BOT_TOKEN`, `DISCORD_*_WEBHOOK_URL` — notifiche/takeover staff

**Non segrete ma necessarie:**
- `IG_API_URL = https://graph.instagram.com/v22.0`
- `WA_PHONE_NUMBER_ID = 1141424935713428`
- `WA_BUSINESS_ACCOUNT_ID = 1172226671633211`
- `WA_VERIFY_TOKEN = gate_whatsapp_verify_2025` (verifica webhook)
- `PERSIST_DIR = /data` (o path del volume) — **indispensabile per auto-rinnovo + storico**
- `SANITY_WEBHOOK_SECRET` — opz., per il sync immediato alla pubblicazione

---

## 5. Webhook (ricezione messaggi)

I messaggi in arrivo li consegna Meta a questi URL. Se un canale "non risponde" ma il
token è valido, quasi sempre è il webhook.

| Canale | Callback URL | Verify token | Campo |
|---|---|---|---|
| WhatsApp | `https://<host>/webhook` | `WA_VERIFY_TOKEN` | `messages` |
| Instagram | `https://<host>/webhook/instagram` | `WA_VERIFY_TOKEN` | `messages` |

- WhatsApp è instradato con un **override per WABA** (`override_callback_uri`): impostabile
  via API con un token che ha `whatsapp_business_management`:
  ```
  POST https://graph.facebook.com/v22.0/1172226671633211/subscribed_apps
       override_callback_uri=https://<host>/webhook
       verify_token=gate_whatsapp_verify_2025
  ```
- Instagram si (ri)aggancia rigenerando i token dal dashboard (§7).

---

## 6. Endpoint di diagnostica `/debug/*`

Aperti se `DEBUG_KEY` non è impostata; altrimenti richiedono `?key=<DEBUG_KEY>`.

| Endpoint | A cosa serve |
|---|---|
| `GET /health` | stato + versione del commit in esecuzione |
| `GET /debug/tokens` | validità dei 3 token (IG×2 + WA), con l'errore esatto di Meta |
| `GET /debug/events` | quanti eventi in memoria per venue + esito ultimo sync Sanity |
| `GET /debug/last-messages` | ultimi messaggi in arrivo (IG+WA) e loro esito (`inviata: SI/NO`) |
| `GET /debug/context?venue=…&text=…` | il contesto RAG che il bot vedrebbe per un messaggio |
| `POST /debug/refresh-tokens` | forza subito il rinnovo dei token Instagram |

Comando staff su Discord: **`!eventi [milano|sardinia]`** → cosa ha il bot in memoria adesso.

---

## 7. Manutenzione — procedure

### Rigenerare un token Instagram (se l'auto-rinnovo fallisce a lungo)
1. developers.facebook.com → app **Milano & Sardinia** → **Instagram** → *Configurazione
   API con login di Instagram* → **"Genera token"** per l'account → login → copia (`IGAA…`).
2. Railway → aggiorna `IG_GATEMILANO_TOKEN` / `IG_GATESARDINIA_TOKEN` → Redeploy.
3. Verifica `GET /debug/tokens` → `true`.

### Rigenerare il token WhatsApp (raro — solo se revocato)
1. business.facebook.com → Utenti di sistema → **WhatsappChatbot** → **Genera token** →
   permessi `whatsapp_business_messaging`, `whatsapp_business_management` → copia (`EAA…`).
2. Railway → `WA_ACCESS_TOKEN` → Redeploy.

### Aggiornare la drinklist VIP
Manda il nuovo PDF: si sostituisce il file `static/drinklist_<venue>.pdf`. L'URL che il bot
invia resta lo stesso (`/static/drinklist_<venue>.pdf`), cambia solo il contenuto. **Non**
usare link Dropbox `dl=0` come allegato WhatsApp: serve un PDF diretto.

### Deploy
Push su `main` → Railway rideploya. Verifica con `GET /health` che `version` sia il commit atteso.

---

## 8. Playbook — "un canale non risponde"

1. `GET /health` → la `version` è quella attesa? (altrimenti il deploy non è passato)
2. `GET /debug/tokens` → il token del canale è `true`?
   - `false` → token scaduto/revocato → rigenera (§7). Il `detail` dà l'errore esatto di Meta.
3. Manda un messaggio di prova, poi `GET /debug/last-messages`:
   - **non compare** → problema di **ricezione** (webhook §5): l'override WhatsApp è impostato?
     l'app è iscritta al campo `messages`?
   - **`inviata: NO`** → problema di **invio**: numero/token; controlla `WA_PHONE_NUMBER_ID`.
   - **`takeover`** → conversazione in mano allo staff: `!rel` su Discord per riattivare.
   - **`inviata: SI`** → il bot ha risposto: se il cliente non vede, è lato numero/utente.
4. Eventi mancanti → `GET /debug/events` + `!eventi` su Discord: se un evento manca dallo
   store è un problema di dati/sync Sanity (evento non pubblicato, campo data vuoto).

---

## 9. Note di sicurezza

- I token nei messaggi di chat sono da considerare **compromessi**: rigenerali/revocali.
- Imposta `DEBUG_KEY` in produzione: `/debug/last-messages` mostra il contenuto dei DM.
- Imposta `META_APP_SECRET`: senza, chiunque conosca l'URL può iniettare messaggi falsi.
