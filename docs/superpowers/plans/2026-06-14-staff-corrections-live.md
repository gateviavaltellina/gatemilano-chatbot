# Staff Corrections Live — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permettere allo staff di correggere il bot in tempo reale via Discord (regole globali per venue iniettate nel contesto), senza deploy.

**Architecture:** Uno store `rag/corrections.py` (JSON write-through su `PERSIST_DIR`) tiene le regole per venue. `ai/claude_client.build_system_blocks` le antepone al blocco system dinamico (non cacheato). Su Discord, `on_message` riconosce `!regola`/`!regole`/`!rimuovi` in reply a un embed del bot; `notify_conversation` arricchisce il context registrato con venue + esempio così la reply cattura la conversazione.

**Tech Stack:** Python 3.9, FastAPI, discord.py (`discord.Client`), pytest (env var fittizie: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy`).

**Nota commit:** i commit di ogni task sono LOCALI. Il push su `main` (deploy Railway) va fatto solo con ok esplicito di George, a fine piano.

**Spec di riferimento:** `docs/superpowers/specs/2026-06-14-staff-corrections-live-design.md`

---

## File Structure

- **Create** `rag/corrections.py` — store correzioni (add/list/remove/get_rules_text + persistenza). Dipende solo da `config`.
- **Create** `tests/test_corrections.py` — test dello store.
- **Create** `tests/test_discord_commands.py` — test di `parse_correction_command` e `handle_correction_command`.
- **Modify** `ai/claude_client.py` — `build_system_blocks` inietta le correzioni nel blocco dinamico.
- **Modify** `notifications/discord.py` — `notify_conversation` arricchisce il context (venue + esempio) via helper `_conversation_context`.
- **Modify** `notifications/discord_bot.py` — `parse_correction_command`, `handle_correction_command`, e branch in `on_message`.
- **Modify** `tests/test_prompt_cache.py` — verifica iniezione nel blocco dinamico.
- **Modify** `tests/test_discord_notify.py` (Create se non esiste) — test di `_conversation_context`.

---

## Task 1: Store correzioni (`rag/corrections.py`)

**Files:**
- Create: `rag/corrections.py`
- Test: `tests/test_corrections.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_corrections.py`:

```python
import importlib
import rag.corrections as cm


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.persist_dir", str(tmp_path))
    cm.reset()
    return cm


def test_add_and_get_rules_text(monkeypatch, tmp_path):
    c = _fresh(monkeypatch, tmp_path)
    cid = c.add_correction("gate_milano", "manda sempre a marketing@", {"user_msg": "non ho i biglietti", "wrong_reply": "scrivi a info@"}, "George")
    assert len(cid) == 8
    text = c.get_rules_text("gate_milano")
    assert "CORREZIONI STAFF" in text
    assert "manda sempre a marketing@" in text
    # altro venue non vede la regola
    assert c.get_rules_text("gate_sardinia") == ""


def test_list_and_remove(monkeypatch, tmp_path):
    c = _fresh(monkeypatch, tmp_path)
    cid = c.add_correction("gate_milano", "regola A", {}, "George")
    c.add_correction("gate_sardinia", "regola B", {}, "George")
    assert len(c.list_corrections()) == 2          # tutte
    assert len(c.list_corrections("gate_milano")) == 1
    assert c.remove_correction(cid) is True
    assert c.remove_correction("inesistente") is False
    assert c.get_rules_text("gate_milano") == ""


def test_persistence_round_trip(monkeypatch, tmp_path):
    c = _fresh(monkeypatch, tmp_path)
    c.add_correction("gate_milano", "regola persistente", {}, "George")
    c.reset()  # simula riavvio: ricarica dal disco
    assert "regola persistente" in c.get_rules_text("gate_milano")


def test_in_memory_without_persist_dir(monkeypatch):
    monkeypatch.setattr("config.settings.persist_dir", "")
    cm.reset()
    cm.add_correction("gate_milano", "solo memoria", {}, "George")
    assert "solo memoria" in cm.get_rules_text("gate_milano")
    cm.reset()  # senza file, il reset perde tutto
    assert cm.get_rules_text("gate_milano") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_corrections.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rag.corrections'`

- [ ] **Step 3: Write minimal implementation**

Create `rag/corrections.py`:

```python
"""Store delle correzioni staff: regole globali per venue, iniettate nel contesto.

Direttive in linguaggio naturale che lo staff aggiunge da Discord per correggere
il bot in tempo reale. Vedi docs/superpowers/specs/2026-06-14-staff-corrections-live-design.md.

Persistenza su {PERSIST_DIR}/corrections.json (write-through). In memoria se
PERSIST_DIR è vuoto (perso al riavvio, come persistence.py).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

# Oltre questa soglia per venue, l'handler Discord avvisa di consolidare nella KB.
SOFT_CAP = 30

# venue -> list[correction]. None = non ancora caricato dal disco.
_store: dict[str, list[dict]] | None = None


def _path() -> Path | None:
    d = (settings.persist_dir or "").strip()
    return Path(d) / "corrections.json" if d else None


def _ensure_loaded() -> None:
    global _store
    if _store is not None:
        return
    _store = {}
    path = _path()
    if path and path.exists():
        try:
            _store = json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.exception("Errore caricamento correzioni")
            _store = {}


def _save() -> None:
    path = _path()
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_store, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        logger.exception("Errore salvataggio correzioni")


def add_correction(venue: str, rule: str, example: dict, author: str) -> str:
    _ensure_loaded()
    cid = uuid.uuid4().hex[:8]
    _store.setdefault(venue, []).append({
        "id": cid,
        "venue": venue,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rule": rule,
        "author": author,
        "example": example or {},
    })
    _save()
    return cid


def list_corrections(venue: str | None = None) -> list[dict]:
    _ensure_loaded()
    if venue is not None:
        return list(_store.get(venue, []))
    out: list[dict] = []
    for items in _store.values():
        out.extend(items)
    return out


def remove_correction(correction_id: str) -> bool:
    _ensure_loaded()
    for items in _store.values():
        for i, c in enumerate(items):
            if c["id"] == correction_id:
                items.pop(i)
                _save()
                return True
    return False


def get_rules_text(venue: str) -> str:
    _ensure_loaded()
    items = _store.get(venue, [])
    if not items:
        return ""
    lines = ["CORREZIONI STAFF (priorità massima — sovrascrivono qualsiasi regola precedente, KB inclusa):"]
    for c in items:
        lines.append(f"- {c['rule']}")
    return "\n".join(lines)


def reset() -> None:
    """Forza il reload dal disco al prossimo accesso (usato dai test e dopo restart)."""
    global _store
    _store = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_corrections.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add rag/corrections.py tests/test_corrections.py
git commit -m "feat(corrections): store correzioni staff per venue con persistenza"
```

---

## Task 2: Iniezione nel system prompt (`ai/claude_client.py`)

**Files:**
- Modify: `ai/claude_client.py` (import + `build_system_blocks`)
- Test: `tests/test_prompt_cache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompt_cache.py`:

```python
def test_corrections_injected_in_dynamic_block(monkeypatch):
    from ai.claude_client import build_system_blocks
    monkeypatch.setattr(
        "rag.corrections.get_rules_text",
        lambda venue: "CORREZIONI STAFF (priorità massima):\n- regola di test",
    )
    blocks = build_system_blocks("gate_milano", "contesto rag", "lunedì 14 giugno 2026, 22:00")
    static_text, dynamic_text = blocks[0]["text"], blocks[1]["text"]
    # le correzioni stanno nel blocco dinamico, NON in quello statico cacheato
    assert "regola di test" in dynamic_text
    assert "regola di test" not in static_text
    # il blocco statico resta cacheato
    assert blocks[0]["cache_control"]["type"] == "ephemeral"


def test_no_corrections_no_block(monkeypatch):
    from ai.claude_client import build_system_blocks
    monkeypatch.setattr("rag.corrections.get_rules_text", lambda venue: "")
    blocks = build_system_blocks("gate_milano", "contesto rag", "lunedì 14 giugno 2026, 22:00")
    assert "CORREZIONI STAFF" not in blocks[1]["text"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_prompt_cache.py::test_corrections_injected_in_dynamic_block -q`
Expected: FAIL — `AssertionError` ("regola di test" non presente nel dinamico, perché non ancora iniettata)

- [ ] **Step 3: Write minimal implementation**

In `ai/claude_client.py`, dentro `build_system_blocks`, sostituire il blocco che costruisce `dynamic_system` con:

```python
    dynamic_system = SYSTEM_DYNAMIC_TEMPLATE.format(
        current_datetime=current_datetime,
        rag_context=rag_context or "Nessuna informazione specifica disponibile al momento.",
    )
    from rag import corrections
    corrections_text = corrections.get_rules_text(venue)
    if corrections_text:
        dynamic_system = f"{corrections_text}\n\n{dynamic_system}"
```

(L'import locale evita qualsiasi rischio di ciclo; `rag.corrections` importa solo `config`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_prompt_cache.py -q`
Expected: PASS (tutti, inclusi i due nuovi)

- [ ] **Step 5: Commit**

```bash
git add ai/claude_client.py tests/test_prompt_cache.py
git commit -m "feat(corrections): inietta le regole staff nel blocco system dinamico"
```

---

## Task 3: Cattura esempio in `notify_conversation` (`notifications/discord.py`)

**Files:**
- Modify: `notifications/discord.py` (helper `_conversation_context` + uso in `notify_conversation`)
- Test: `tests/test_discord_notify.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_discord_notify.py`:

```python
from notifications.discord import _conversation_context


def test_conversation_context_adds_venue_and_example():
    ctx = _conversation_context(None, "gate_milano", "domanda", "risposta sbagliata")
    assert ctx["venue"] == "gate_milano"
    assert ctx["user_msg"] == "domanda"
    assert ctx["bot_reply"] == "risposta sbagliata"


def test_conversation_context_preserves_existing_ig_context():
    ctx = _conversation_context(
        {"ig_account_id": "A", "sender_id": "S"}, "gate_milano", "d", "r"
    )
    assert ctx["ig_account_id"] == "A"
    assert ctx["sender_id"] == "S"
    assert ctx["venue"] == "gate_milano"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_discord_notify.py -q`
Expected: FAIL — `ImportError: cannot import name '_conversation_context'`

- [ ] **Step 3: Write minimal implementation**

In `notifications/discord.py`, aggiungere l'helper (sopra `notify_conversation`):

```python
def _conversation_context(context: dict | None, venue: str, user_msg: str, bot_reply: str) -> dict:
    """Context registrato per il messaggio Discord: include venue + esempio, così
    una reply !regola può catturare domanda e risposta sbagliata da correggere."""
    return {**(context or {}), "venue": venue, "user_msg": user_msg, "bot_reply": bot_reply}
```

Poi in `notify_conversation`, sostituire la riga di registrazione:

```python
            if msg_id:
                from notifications.discord_bot import register_message
                register_message(msg_id, phone, _conversation_context(context, venue, user_msg, bot_reply))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_discord_notify.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add notifications/discord.py tests/test_discord_notify.py
git commit -m "feat(corrections): cattura venue+esempio nel context delle notifiche conversazione"
```

---

## Task 4: Parsing e gestione comandi (`notifications/discord_bot.py`)

**Files:**
- Modify: `notifications/discord_bot.py` (`parse_correction_command`, `handle_correction_command`)
- Test: `tests/test_discord_commands.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_discord_commands.py`:

```python
import rag.corrections as cm
from notifications.discord_bot import parse_correction_command, handle_correction_command


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.persist_dir", str(tmp_path))
    cm.reset()


def test_parse_commands():
    assert parse_correction_command("!regola manda a marketing@") == ("regola", "manda a marketing@")
    assert parse_correction_command("!regole") == ("regole", "")
    assert parse_correction_command("!rimuovi abc123") == ("rimuovi", "abc123")
    assert parse_correction_command("ciao") == (None, "")
    # niente collisione coi comandi takeover esistenti
    assert parse_correction_command("!r ciao")[0] is None
    assert parse_correction_command("!rel")[0] is None


def test_handle_regola_adds_correction(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    ctx = {"venue": "gate_milano", "user_msg": "non ho i biglietti", "bot_reply": "scrivi a info@"}
    out = handle_correction_command("regola", "manda sempre a marketing@", ctx, "George")
    assert "✅" in out
    assert "manda sempre a marketing@" in cm.get_rules_text("gate_milano")


def test_handle_regola_without_context_errors(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    out = handle_correction_command("regola", "qualcosa", None, "George")
    assert out.startswith("❌")


def test_handle_regole_lists(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    cm.add_correction("gate_milano", "regola X", {}, "George")
    out = handle_correction_command("regole", "", None, "George")
    assert "regola X" in out


def test_handle_rimuovi(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    cid = cm.add_correction("gate_milano", "regola Y", {}, "George")
    assert "🗑️" in handle_correction_command("rimuovi", cid, None, "George")
    assert handle_correction_command("rimuovi", "nope", None, "George").startswith("❌")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_discord_commands.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_correction_command'`

- [ ] **Step 3: Write minimal implementation**

In `notifications/discord_bot.py`, aggiungere (dopo gli import, prima di `on_message`):

```python
def parse_correction_command(text: str):
    """Riconosce i comandi correzione. Ritorna (cmd, payload) o (None, '')."""
    t = (text or "").strip()
    if t.startswith("!regola "):
        return "regola", t[len("!regola "):].strip()
    if t == "!regole":
        return "regole", ""
    if t.startswith("!rimuovi "):
        return "rimuovi", t[len("!rimuovi "):].strip()
    return None, ""


def handle_correction_command(cmd: str, payload: str, ctx: dict, author: str) -> str:
    """Esegue il comando correzione e ritorna il testo di conferma per Discord."""
    from rag import corrections
    if cmd == "regola":
        if not ctx or not ctx.get("venue"):
            return "❌ Rispondi a un messaggio di conversazione del bot per usare !regola"
        if not payload:
            return "❌ Scrivi la regola dopo !regola (es. !regola per i rimborsi manda sempre a info@)"
        venue = ctx["venue"]
        example = {"user_msg": ctx.get("user_msg", ""), "wrong_reply": ctx.get("bot_reply", "")}
        cid = corrections.add_correction(venue, payload, example, author)
        count = len(corrections.list_corrections(venue))
        msg = f"✅ Regola salvata (#{cid}) per {venue}. Si applica da subito."
        if count > corrections.SOFT_CAP:
            msg += f"\n⚠️ {count} correzioni per {venue}: conviene consolidarle nella KB."
        return msg
    if cmd == "regole":
        items = corrections.list_corrections()
        if not items:
            return "Nessuna correzione attiva."
        lines = ["Correzioni attive:"]
        for c in items:
            lines.append(f"#{c['id']} [{c['venue']}] {c['rule']}")
        return "\n".join(lines)
    if cmd == "rimuovi":
        if not payload:
            return "❌ Indica l'id: !rimuovi <id>"
        ok = corrections.remove_correction(payload)
        return f"🗑️ Rimossa #{payload}." if ok else f"❌ Nessuna correzione con id {payload}."
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_discord_commands.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add notifications/discord_bot.py tests/test_discord_commands.py
git commit -m "feat(corrections): parsing e gestione comandi !regola/!regole/!rimuovi"
```

---

## Task 5: Aggancio in `on_message` (`notifications/discord_bot.py`)

**Files:**
- Modify: `notifications/discord_bot.py` (`on_message`)

Nessun unit test automatico (richiede `discord.Message` reale); la logica è già coperta dai test di Task 4. Verifica manuale a fine task.

- [ ] **Step 1: Add the command branch in `on_message`**

In `notifications/discord_bot.py`, in `on_message`, subito DOPO il filtro sul canale (la riga `return` del blocco `if settings.discord_channel_id and ...`) e PRIMA di `if content.startswith("!r "):`, inserire:

```python
    cmd, payload = parse_correction_command(content)
    if cmd:
        reply = handle_correction_command(cmd, payload, ctx, message.author.display_name)
        if reply:
            await message.reply(reply, mention_author=False)
        return
```

- [ ] **Step 2: Verify import resolution and no syntax errors**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -c "import notifications.discord_bot"`
Expected: nessun errore (exit 0)

- [ ] **Step 3: Run the full test suite (no regressions)**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest -q`
Expected: PASS (tutti)

- [ ] **Step 4: Commit**

```bash
git add notifications/discord_bot.py
git commit -m "feat(corrections): aggancia i comandi correzione in on_message"
```

---

## Task 6: Verifica end-to-end dell'effetto sul bot

**Files:** nessuna modifica — test di integrazione manuale dello store→iniezione.

- [ ] **Step 1: Add an integration test (store → prompt)**

Append to `tests/test_corrections.py`:

```python
def test_correction_reaches_system_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.persist_dir", str(tmp_path))
    cm.reset()
    cm.add_correction("gate_milano", "REGOLA E2E: di' sempre ciao", {}, "George")
    from ai.claude_client import build_system_blocks
    blocks = build_system_blocks("gate_milano", "ctx", "lunedì 14 giugno 2026, 22:00")
    assert "REGOLA E2E: di' sempre ciao" in blocks[1]["text"]
    assert "REGOLA E2E" not in blocks[0]["text"]
```

- [ ] **Step 2: Run it**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_corrections.py -q`
Expected: PASS

- [ ] **Step 3: Run the full suite + the eval (no regressions)**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest -q`
Expected: PASS (tutti)

Run (eval deterministico, richiede `.env` con chiave): `EVAL_CONCURRENCY=2 ./.venv/bin/python -m eval.run`
Expected: `27/27 pass` (nessuna correzione presente in test → nessun effetto sull'eval)

- [ ] **Step 4: Commit**

```bash
git add tests/test_corrections.py
git commit -m "test(corrections): integrazione store→system prompt"
```

---

## Verifica manuale (dopo il deploy, con George)

1. Dal canale Discord, rispondere a un embed di conversazione del bot con `!regola <direttiva di test>` → atteso ✅ con id.
2. `!regole` → elenca la correzione con id e venue.
3. Inviare al bot (WA/IG) un messaggio attinente → il bot applica la regola.
4. `!rimuovi <id>` → 🗑️; nuova prova → la regola non si applica più.

## Push

A piano completato e suite verde, chiedere a George l'ok per `git push origin main` (deploy Railway). Verificare che `PERSIST_DIR` sia configurato sul servizio Railway, altrimenti le correzioni si perdono al riavvio.
