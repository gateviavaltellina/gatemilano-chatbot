# Ritoccare il comportamento del bot — guida staff

Due modi per cambiare come risponde il bot, dal più semplice al più avanzato.

---

## 1. Modo veloce: `!regola` su Discord (nessun codice, subito)

Rispondi su Discord a una risposta sbagliata del bot con:
```
!regola <come dovrebbe rispondere>
```
Esempi:
- `!regola per le navette da San Teodoro dai il numero di Salvatore +39 328 813 4685`
- `!regola se scrivono in inglese rispondi in inglese`

- Si applica **SUBITO**, senza deploy.
- Genera anche un caso di test così l'errore non torna.
- `!regole` = elenco regole attive · `!rimuovi <id>` = rimuovi una regola.

👉 Copre il ~90% dei ritocchi. Usa questo per primo.

---

## 2. Console Anthropic (Workbench): provare prima di applicare

La Console è un **banco di prova**: incolli il prompt del bot, provi un messaggio,
vedi la risposta, ritocchi. **Non è la produzione** — quello che trovi lì poi va
riportato (con `!regola` o nei file).

### a) Accedi
**console.anthropic.com** → **Workbench** (login con l'account dell'API key del bot).

### b) Prendi il prompt VERO del bot
Così provi sul prompt reale, non a caso. Apri nel browser (scegli venue e un messaggio):
```
https://gatemilano-chatbot-production.up.railway.app/debug/prompt?venue=gate_sardinia&text=ciao
```
(se hai impostato `DEBUG_KEY`, aggiungi `&key=LA_TUA_CHIAVE`)

Copia il contenuto del campo **`system_prompt`** e annota il campo **`model`**.

### c) Prova nel Workbench
1. Incolla quel testo nel campo **System**.
2. Imposta **Model** uguale al valore `model` visto sopra.
3. In **User** scrivi un messaggio da cliente (italiano, inglese, spagnolo…).
4. **Run** → guarda la risposta.
5. Modifica le regole nel System, ri-**Run**, finché la risposta è giusta.

### d) Applica la modifica trovata
- È una **regola** su un caso ("in questo caso fai/non fare X") → mettila con
  **`!regola`** su Discord. Fatto.
- È un cambio **strutturale** (tono generale, nuove sezioni, prezzi, knowledge base)
  → va nei file del bot (`ai/claude_client.py`, `rag/knowledge/*.md`): passa la
  modifica allo sviluppatore o a Claude Code.

---

## Da NON fare
- Non incollare mai API key o token in posti pubblici.
- La Console **non modifica la produzione**: serve solo a provare.
- Il prompt è lungo (regole + knowledge base): è normale.

## In pratica
| Cosa vuoi fare | Strumento |
|---|---|
| Correggere un caso specifico, subito | `!regola` su Discord |
| Provare un nuovo tono / esperimento più grosso | Console (Workbench), poi applicare |
| Cambiare prezzi/eventi/orari | Sanity (eventi) o knowledge base (`.md`) |
