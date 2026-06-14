# Staff Corrections Live — Design

**Data:** 2026-06-14
**Stato:** approvato (design), pronto per il piano di implementazione
**Autore:** George + Claude

## Contesto e problema

Il bot (WhatsApp/Instagram, `ai/claude_client.py` + RAG) a volte risponde in
modo sbagliato. Oggi correggere richiede: notare l'errore, modificare a mano
system prompt o knowledge base, girare l'eval, committare, attendere il deploy
Railway. È lento e richiede un intervento da sviluppatore.

Obiettivo: **George nota una risposta sbagliata, scrive una correzione su
Discord, e il bot la applica subito** ("scrivo e si auto-migliora"), senza
deploy e senza toccare il codice.

Nota terminologica: NON è fine tuning dei pesi del modello. È un layer di
**regole di correzione iniettate nel contesto** con priorità sullo staff —
istantaneo, reversibile e verificabile.

## Decisioni (prese in brainstorming)

1. **Input** = reply su Discord al messaggio sbagliato (riusa il meccanismo
   dell'human takeover già esistente).
2. **Applicazione** = regole globali sempre attive (iniettate nel contesto a
   ogni messaggio del venue). Adatto a poche decine di correzioni; oltre, si
   passerà al retrieval (Fase 2).
3. **Contenuto** = regola generale in linguaggio naturale (una direttiva che
   generalizza), non la singola risposta esatta.
4. **Eval synergy** = si salva l'esempio catturato (domanda + risposta
   sbagliata + regola) in ogni correzione, ma la generazione automatica di
   eval case è Fase 2.

## Goals

- Correzione applicata entro pochi secondi, senza deploy.
- Solo staff autenticato (il canale Discord è già access-controlled).
- Le correzioni sopravvivono a riavvii/deploy.
- George può elencare e rimuovere le correzioni (undo).
- Nessun dato perso: ogni correzione conserva l'esempio per la Fase 2.

## Non-goals (Fase 2, spec separati)

- Generazione automatica di eval case dagli esempi catturati.
- Consolidamento delle correzioni nella KB canonica.
- Matching per pertinenza / embeddings (ora le regole sono tutte globali).
- Correzioni cross-venue (ora ogni correzione è legata a un venue).

## Architettura

Tre unità isolate, ciascuna con uno scopo chiaro e interfaccia definita.

### `rag/corrections.py` — lo store

Scopo: tenere le correzioni per venue e renderle come testo iniettabile.
Dipendenze: solo `config` (per `PERSIST_DIR`). Nessuna dipendenza da Discord o
Anthropic, così è testabile in isolamento.

API:
- `add_correction(venue: str, rule: str, example: dict, author: str) -> str`
  — crea una correzione, la salva (write-through) e ritorna l'`id`.
- `list_corrections(venue: str) -> list[dict]` — tutte le correzioni del venue.
- `remove_correction(venue: str, correction_id: str) -> bool` — rimuove per id.
- `get_rules_text(venue: str) -> str` — rende il blocco regole per l'iniezione
  (stringa vuota se nessuna correzione).
- `load()` / `save()` interni: persistenza su `{PERSIST_DIR}/corrections.json`.
  No-op in memoria se `PERSIST_DIR` è vuoto (stesso tradeoff di `persistence.py`).
  Write-through a ogni `add`/`remove` (eventi rari → niente attesa del salvataggio
  periodico).

Modello dati di una correzione:
```
{
  "id": "<breve id>",
  "venue": "gate_milano",
  "timestamp": "2026-06-14T22:00:00Z",
  "rule": "<direttiva in linguaggio naturale>",
  "author": "<nome utente Discord>",
  "example": {"user_msg": "<domanda cliente>", "wrong_reply": "<risposta sbagliata>"},
  "source_phone": "<opzionale: id conversazione>"
}
```

Formato file: `{ "gate_milano": [ ...correzioni... ], "gate_sardinia": [ ... ] }`.

### `ai/claude_client.py` — iniezione nel contesto

Scopo: far vedere al modello le regole staff con priorità massima.
In `build_system_blocks`, se `corrections.get_rules_text(venue)` non è vuoto,
si antepone al blocco **dinamico** (non cacheato) una sezione:

```
CORREZIONI STAFF (priorità massima — sovrascrivono qualsiasi regola precedente,
KB inclusa):
- <regola 1>
- <regola 2>
...
```

Va nel blocco dinamico (non in quello statico cacheato) per due motivi:
effetto immediato (niente attesa TTL cache) e nessun bust della cache statica a
ogni nuova correzione. Costo in token trascurabile (regole brevi).

Precedenza: il blocco dinamico segue lo statico nel system; l'etichetta
"priorità massima" rende esplicito che vince su KB e regole precedenti.

### `notifications/discord_bot.py` — handler dei comandi

Scopo: trasformare un reply di George in una chiamata allo store.
In `on_message`, oltre alla logica human-takeover esistente, riconosce i comandi
(solo se il messaggio è un reply a un embed prodotto dal bot):

- `!regola <direttiva>` — aggiunge una correzione. Risolve il venue e cattura
  l'`example` (domanda utente + risposta sbagliata) dall'embed citato, riusando
  `_phone_from_reply` / `discord_msg_context`. Conferma: "✅ Regola salvata
  (#id). Si applica da subito."
- `!regole` — elenca le correzioni del venue corrente con id.
- `!rimuovi <id>` — rimuove la correzione indicata.

Il parsing del comando è estratto in una funzione pura
`parse_correction_command(text) -> (cmd, payload)` per essere testabile senza
discord.py.

## Flusso dati

1. Bot risponde al cliente (WA/IG) → `notify_conversation` posta l'embed su
   Discord (esistente).
2. George risponde all'embed con `!regola <direttiva>`.
3. `on_message` riconosce il comando + reply → risolve venue + cattura esempio
   → `corrections.add_correction(...)` → salvataggio su volume.
4. Prossimo messaggio cliente di quel venue → `build_system_blocks` antepone le
   "CORREZIONI STAFF" al blocco dinamico → il bot applica subito la regola.

## Error handling

- `PERSIST_DIR` assente → store in memoria, warn nei log (perso al restart),
  stesso comportamento di `persistence.py`.
- Reply non a un embed del bot, o venue non risolvibile → risposta d'errore su
  Discord che spiega come usare il comando.
- Regola vuota dopo `!regola` → risposta d'errore.
- `!rimuovi <id>` con id inesistente → risposta d'errore.
- **Cap morbido**: quando le correzioni di un venue superano una soglia (~30),
  avviso su Discord che conviene consolidare nella KB (Fase 2). Nessun
  troncamento silenzioso: il numero viene loggato.

## Testing

- `rag/corrections.py`: add/list/remove/get_rules_text; round-trip di
  persistenza su `tmp_path`; no-op in memoria senza `PERSIST_DIR`.
- `parse_correction_command`: parsing dei tre comandi e dei casi limite
  (testo vuoto, comando sconosciuto), senza dipendere da discord.py.
- `build_system_blocks`: con correzioni presenti, il blocco "CORREZIONI STAFF"
  compare nel blocco **dinamico** e NON in quello statico cacheato; assente
  quando non ci sono correzioni.

## Sicurezza

Le correzioni sono accettate solo dal canale Discord, già access-controlled
(allowlist staff). Mai da utenti finali su WhatsApp/IG. Nessuna nuova superficie
di input pubblica.

## Fase 2 (futuro, fuori da questo spec)

- Generazione semi-automatica di eval case dagli `example` salvati (bozza →
  approvazione su Discord → entra nella suite).
- Consolidamento periodico delle correzioni nella KB canonica + validazione eval.
- Matching per pertinenza (keyword/embeddings) quando il numero di correzioni
  cresce oltre il cap morbido.
