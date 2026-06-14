# Corrections → Eval Cases (Fase 2A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ogni correzione staff genera una bozza di eval case (LLM) approvabile su Discord, esportabile via endpoint protetto e importabile nel repo come test di regressione.

**Architecture:** `rag/correction_cases.py` genera il case via LLM (tool-use); `rag/corrections.py` attacca la bozza alla correzione (`case`/`case_status`); Discord `!regola` genera la bozza, `!approva <id>` la approva; `main.py` espone i casi approvati su `GET /eval/correction-cases` (protetto da `EVAL_EXPORT_TOKEN`, fail-closed); `eval/import_correction_cases.py` (locale) li porta in `eval/cases/corrections.yaml`.

**Tech Stack:** Python 3.9, FastAPI, discord.py, anthropic (tool-use), pytest (`asyncio_mode=auto`). Test env: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest ...`. `FakeClient` importabile da `tests.conftest`.

**Commit:** ogni task committa LOCALMENTE. Push/PR solo a fine piano con ok di George.

**Spec:** `docs/superpowers/specs/2026-06-14-corrections-to-eval-cases-design.md`

---

## File Structure
- **Modify** `rag/corrections.py` — `get_correction`, `set_case`, `approve_case`, `get_approved_cases`.
- **Create** `rag/correction_cases.py` — `draft_case` (LLM generator).
- **Modify** `notifications/discord_bot.py` — `parse_correction_command` (+`!approva`), `handle_correction_command` (+approva, −regola), `handle_regola` (async), `on_message` routing.
- **Modify** `config.py` — `eval_export_token`.
- **Modify** `main.py` — `GET /eval/correction-cases`.
- **Create** `eval/import_correction_cases.py` — importer locale.
- **Modify** `tests/test_corrections.py`, `tests/test_discord_commands.py`; **Create** `tests/test_correction_cases.py`, `tests/test_export_endpoint.py`, `tests/test_import_correction_cases.py`.

---

## Task 1: Store — bozza attaccata alla correzione

**Files:**
- Modify: `rag/corrections.py`
- Test: `tests/test_corrections.py`

- [ ] **Step 1: Append failing tests to `tests/test_corrections.py`**

```python
def test_set_and_approve_case(monkeypatch, tmp_path):
    c = _fresh(monkeypatch, tmp_path)
    cid = c.add_correction("gate_milano", "regola Z", {}, "George")
    case = {"id": f"corr-{cid}", "category": "corrections", "venue": "gate_milano",
            "user_message": "u", "rag_context": "", "rubric": {"must": ["x"], "must_not": []},
            "assertions": {"forbidden_substrings": []}}
    assert c.set_case(cid, case) is True
    assert c.set_case("inesistente", case) is False
    # pending → non ancora negli approvati
    assert c.get_approved_cases() == []
    assert c.approve_case(cid) is True
    assert c.approve_case("inesistente") is False
    approved = c.get_approved_cases()
    assert len(approved) == 1 and approved[0]["id"] == f"corr-{cid}"


def test_get_correction(monkeypatch, tmp_path):
    c = _fresh(monkeypatch, tmp_path)
    cid = c.add_correction("gate_milano", "regola W", {}, "George")
    rec = c.get_correction(cid)
    assert rec is not None and rec["rule"] == "regola W"
    assert c.get_correction("nope") is None


def test_approve_case_requires_a_case(monkeypatch, tmp_path):
    c = _fresh(monkeypatch, tmp_path)
    cid = c.add_correction("gate_milano", "senza bozza", {}, "George")
    # nessuna bozza attaccata → approve_case fallisce
    assert c.approve_case(cid) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_corrections.py -q`
Expected: FAIL (`AttributeError: module 'rag.corrections' has no attribute 'set_case'`)

- [ ] **Step 3: Add functions to `rag/corrections.py`** (after `remove_correction`, before `get_rules_text`)

```python
def get_correction(correction_id: str) -> dict | None:
    _ensure_loaded()
    for items in _store.values():
        for c in items:
            if c["id"] == correction_id:
                return c
    return None


def set_case(correction_id: str, case: dict) -> bool:
    _ensure_loaded()
    for items in _store.values():
        for c in items:
            if c["id"] == correction_id:
                c["case"] = case
                c["case_status"] = "pending"
                _save()
                return True
    return False


def approve_case(correction_id: str) -> bool:
    _ensure_loaded()
    for items in _store.values():
        for c in items:
            if c["id"] == correction_id and c.get("case"):
                c["case_status"] = "approved"
                _save()
                return True
    return False


def get_approved_cases() -> list[dict]:
    _ensure_loaded()
    out: list[dict] = []
    for items in _store.values():
        for c in items:
            if c.get("case_status") == "approved" and c.get("case"):
                out.append(c["case"])
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_corrections.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add rag/corrections.py tests/test_corrections.py
git commit -m "feat(corrections): bozza eval case attaccata alla correzione (set/approve/get)"
```

---

## Task 2: Generatore LLM (`rag/correction_cases.py`)

**Files:**
- Create: `rag/correction_cases.py`
- Test: `tests/test_correction_cases.py`

- [ ] **Step 1: Write failing test — create `tests/test_correction_cases.py`**

```python
from tests.conftest import FakeClient
from rag.correction_cases import draft_case


async def test_draft_case_builds_eval_case():
    client = FakeClient({
        "user_message": "ho comprato ma non ho ricevuto i biglietti",
        "rag_context": "",
        "must": ["Deve indirizzare a marketing@gatemilano.com"],
        "must_not": ["Non deve mandare a info@gatemilano.com"],
        "forbidden_substrings": ["info@gatemilano.com"],
    })
    correction = {"id": "abc12345", "venue": "gate_milano", "rule": "biglietti non ricevuti -> marketing@",
                  "example": {"user_msg": "u", "wrong_reply": "w"}}
    case = await draft_case(correction, client=client, model="x")
    assert case["id"] == "corr-abc12345"
    assert case["category"] == "corrections"
    assert case["venue"] == "gate_milano"
    assert case["user_message"] == "ho comprato ma non ho ricevuto i biglietti"
    assert case["rubric"]["must"] == ["Deve indirizzare a marketing@gatemilano.com"]
    assert case["rubric"]["must_not"] == ["Non deve mandare a info@gatemilano.com"]
    assert case["assertions"]["forbidden_substrings"] == ["info@gatemilano.com"]


async def test_draft_case_none_when_no_must():
    client = FakeClient({"user_message": "x", "must": [], "must_not": []})
    correction = {"id": "a", "venue": "gate_milano", "rule": "r", "example": {}}
    assert await draft_case(correction, client=client, model="x") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_correction_cases.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'rag.correction_cases'`)

- [ ] **Step 3: Create `rag/correction_cases.py`**

```python
"""Genera un eval case di regressione da una correzione staff, via LLM (tool-use)."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DRAFT_INSTRUCTIONS = """\
Sei un generatore di test per un chatbot di un club/venue.
Ricevi: una REGOLA staff (il comportamento corretto da garantire), il messaggio utente
che ha innescato la correzione, e la risposta SBAGLIATA che il bot aveva dato.
Genera un eval case minimale che PASSA se il bot segue la regola e FALLISCE sulla
risposta sbagliata. Criteri verificabili e concisi.
- 'must': cosa la risposta DEVE fare per rispettare la regola.
- 'must_not': cosa NON deve fare (deriva dalla risposta sbagliata).
- 'rag_context': lascia "" salvo che la regola richieda dati di un evento; in tal caso
  scrivi un contesto sintetico minimo.
- 'forbidden_substrings': stringhe esatte vietate, solo se ovvie (es. un'email sbagliata).
Registra il risultato con lo strumento draft_eval_case.
"""

_DRAFT_TOOL = {
    "name": "draft_eval_case",
    "description": "Registra l'eval case generato.",
    "input_schema": {
        "type": "object",
        "properties": {
            "user_message": {"type": "string"},
            "rag_context": {"type": "string"},
            "must": {"type": "array", "items": {"type": "string"}},
            "must_not": {"type": "array", "items": {"type": "string"}},
            "forbidden_substrings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["user_message", "must", "must_not"],
    },
}


def _format_user(correction: dict) -> str:
    ex = correction.get("example") or {}
    return (
        f"REGOLA:\n{correction['rule']}\n\n"
        f"MESSAGGIO UTENTE:\n{ex.get('user_msg', '')}\n\n"
        f"RISPOSTA SBAGLIATA:\n{ex.get('wrong_reply', '')}"
    )


def _parse_tool(response) -> dict | None:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    return None


async def draft_case(correction: dict, *, client, model: str) -> dict | None:
    """Ritorna un eval case (schema eval) o None se la generazione fallisce."""
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=600,
            temperature=0,
            system=_DRAFT_INSTRUCTIONS,
            tools=[_DRAFT_TOOL],
            tool_choice={"type": "tool", "name": "draft_eval_case"},
            messages=[{"role": "user", "content": _format_user(correction)}],
        )
    except Exception:
        logger.exception("draft_case: errore LLM per correzione %s", correction.get("id"))
        return None
    data = _parse_tool(response)
    if not data or not data.get("must"):
        return None
    ex = correction.get("example") or {}
    return {
        "id": f"corr-{correction['id']}",
        "category": "corrections",
        "venue": correction["venue"],
        "user_message": data.get("user_message") or ex.get("user_msg", ""),
        "rag_context": data.get("rag_context", "") or "",
        "rubric": {
            "must": data.get("must", []),
            "must_not": data.get("must_not", []),
        },
        "assertions": {
            "forbidden_substrings": data.get("forbidden_substrings", []) or [],
        },
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_correction_cases.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add rag/correction_cases.py tests/test_correction_cases.py
git commit -m "feat(corrections): generatore LLM di eval case da correzione"
```

---

## Task 3: Comandi Discord (`!approva`, `!regola` con bozza)

**Files:**
- Modify: `notifications/discord_bot.py`
- Test: `tests/test_discord_commands.py`

- [ ] **Step 1: Update `tests/test_discord_commands.py`**

Replace the whole file with:

```python
import rag.corrections as cm
from tests.conftest import FakeClient
from notifications.discord_bot import (
    parse_correction_command,
    handle_correction_command,
    handle_regola,
)


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.persist_dir", str(tmp_path))
    cm.reset()


def _draft_client():
    return FakeClient({
        "user_message": "u",
        "rag_context": "",
        "must": ["Deve fare X"],
        "must_not": ["Non deve fare Y"],
        "forbidden_substrings": [],
    })


def test_parse_commands():
    assert parse_correction_command("!regola manda a marketing@") == ("regola", "manda a marketing@")
    assert parse_correction_command("!regole") == ("regole", "")
    assert parse_correction_command("!rimuovi abc123") == ("rimuovi", "abc123")
    assert parse_correction_command("!approva abc123") == ("approva", "abc123")
    assert parse_correction_command("ciao") == (None, "")
    # nessuna collisione coi comandi takeover
    assert parse_correction_command("!r ciao")[0] is None
    assert parse_correction_command("!rel")[0] is None


async def test_handle_regola_adds_correction_and_drafts(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    ctx = {"venue": "gate_milano", "user_msg": "non ho i biglietti", "bot_reply": "scrivi a info@"}
    out = await handle_regola("manda sempre a marketing@", ctx, "George", client=_draft_client(), model="x")
    assert "✅" in out and "!approva" in out
    items = cm.list_corrections("gate_milano")
    assert len(items) == 1
    assert "manda sempre a marketing@" in cm.get_rules_text("gate_milano")
    assert items[0]["case_status"] == "pending"
    assert items[0]["case"]["rubric"]["must"] == ["Deve fare X"]


async def test_handle_regola_draft_failure_keeps_correction(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    ctx = {"venue": "gate_milano", "user_msg": "u", "bot_reply": "w"}
    # FakeClient con 'must' vuoto → draft_case ritorna None
    client = FakeClient({"user_message": "u", "must": [], "must_not": []})
    out = await handle_regola("una regola", ctx, "George", client=client, model="x")
    assert "✅" in out  # la correzione è salvata comunque
    assert "manca" in out.lower() or "non generata" in out.lower()
    assert "una regola" in cm.get_rules_text("gate_milano")


async def test_handle_regola_without_context_errors(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    assert (await handle_regola("x", None, "George", client=_draft_client(), model="x")).startswith("❌")
    assert (await handle_regola("x", {}, "George", client=_draft_client(), model="x")).startswith("❌")


async def test_handle_regola_empty_payload_errors(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    ctx = {"venue": "gate_milano", "user_msg": "u", "bot_reply": "r"}
    assert (await handle_regola("", ctx, "George", client=_draft_client(), model="x")).startswith("❌")


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


def test_handle_approva(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    cid = cm.add_correction("gate_milano", "regola K", {}, "George")
    cm.set_case(cid, {"id": f"corr-{cid}", "rubric": {"must": ["x"], "must_not": []}})
    assert "✅" in handle_correction_command("approva", cid, None, "George")
    assert cm.get_approved_cases()[0]["id"] == f"corr-{cid}"
    # id senza bozza / inesistente
    assert handle_correction_command("approva", "nope", None, "George").startswith("❌")
    assert handle_correction_command("approva", "", None, "George").startswith("❌")
```

- [ ] **Step 2: Run to verify failure**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_discord_commands.py -q`
Expected: FAIL (`ImportError: cannot import name 'handle_regola'`)

- [ ] **Step 3: Edit `notifications/discord_bot.py`**

3a. In `parse_correction_command`, add the `!approva` branch BEFORE the final `return None, ""`:

```python
    if t.startswith("!approva "):
        return "approva", t[len("!approva "):].strip()
```

3b. Replace the whole `handle_correction_command` function with this version (the `regola` branch is removed; an `approva` branch is added):

```python
def handle_correction_command(cmd: str, payload: str, ctx: dict, author: str) -> str:
    """Esegue i comandi correzione SINCRONI e ritorna il testo per Discord.
    Il comando !regola (che genera la bozza LLM) è gestito da handle_regola (async)."""
    from rag import corrections
    if cmd == "regole":
        items = corrections.list_corrections()
        if not items:
            return "Nessuna correzione attiva."
        lines = ["Correzioni attive:"]
        for c in items:
            stato = c.get("case_status")
            suffix = f" [eval: {stato}]" if stato else ""
            lines.append(f"#{c['id']} [{c['venue']}] {c['rule']}{suffix}")
        return "\n".join(lines)
    if cmd == "rimuovi":
        if not payload:
            return "❌ Indica l'id: !rimuovi <id>"
        ok = corrections.remove_correction(payload)
        return f"🗑️ Rimossa #{payload}." if ok else f"❌ Nessuna correzione con id {payload}."
    if cmd == "approva":
        if not payload:
            return "❌ Indica l'id: !approva <id>"
        ok = corrections.approve_case(payload)
        return f"✅ Eval case approvato per #{payload}." if ok else f"❌ Nessuna bozza da approvare per id {payload}."
    return ""


async def handle_regola(payload: str, ctx: dict, author: str, *, client=None, model=None) -> str:
    """!regola: salva la correzione e genera la bozza di eval case (LLM)."""
    from rag import corrections, correction_cases
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
    if client is None:
        from ai.claude_client import _client as client
        from config import settings
        model = model or settings.model
    correction = corrections.get_correction(cid)
    case = await correction_cases.draft_case(correction, client=client, model=model) if correction else None
    if case:
        corrections.set_case(cid, case)
        must = "; ".join(case["rubric"]["must"]) or "—"
        mustnot = "; ".join(case["rubric"]["must_not"]) or "—"
        msg += f"\n📋 Bozza eval: MUST: {must} | MUST NOT: {mustnot}\nApprova con !approva {cid}"
    else:
        msg += "\n⚠️ Bozza eval non generata (manca / riprova più tardi)."
    return msg
```

3c. In `on_message`, replace the existing correction-command block:

```python
    cmd, payload = parse_correction_command(content)
    if cmd:
        reply = handle_correction_command(cmd, payload, ctx, message.author.display_name)
        if reply:
            await message.reply(reply, mention_author=False)
        return
```

with:

```python
    cmd, payload = parse_correction_command(content)
    if cmd == "regola":
        reply = await handle_regola(payload, ctx, message.author.display_name)
        if reply:
            await message.reply(reply, mention_author=False)
        return
    if cmd:
        reply = handle_correction_command(cmd, payload, ctx, message.author.display_name)
        if reply:
            await message.reply(reply, mention_author=False)
        return
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_discord_commands.py -q`
Expected: PASS
Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add notifications/discord_bot.py tests/test_discord_commands.py
git commit -m "feat(corrections): !regola genera bozza eval, !approva la conferma"
```

---

## Task 4: Endpoint export + config

**Files:**
- Modify: `config.py`, `main.py`
- Test: `tests/test_export_endpoint.py`

- [ ] **Step 1: Write failing test — create `tests/test_export_endpoint.py`**

```python
from fastapi.testclient import TestClient
import main


def test_export_endpoint(monkeypatch):
    client = TestClient(main.app)
    # token non configurato → 404 (disabilitato)
    monkeypatch.setattr("config.settings.eval_export_token", "")
    assert client.get("/eval/correction-cases").status_code == 404
    # token configurato, chiave errata → 403
    monkeypatch.setattr("config.settings.eval_export_token", "secret")
    assert client.get("/eval/correction-cases", params={"key": "wrong"}).status_code == 403
    # chiave giusta → 200 con i casi approvati
    monkeypatch.setattr("rag.corrections.get_approved_cases", lambda: [{"id": "corr-x"}])
    r = client.get("/eval/correction-cases", params={"key": "secret"})
    assert r.status_code == 200
    assert r.json() == {"cases": [{"id": "corr-x"}]}
```

- [ ] **Step 2: Run to verify failure**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_export_endpoint.py -q`
Expected: FAIL (404 assertion ok, but 403/200 fail → endpoint missing returns 404 for all). It will fail on the 403 assertion.

- [ ] **Step 3a: Add the setting to `config.py`** (after `discord_group_channel_id` or near other tokens)

```python
    # Token per l'endpoint di export degli eval case generati dalle correzioni.
    # Vuoto = endpoint disabilitato (404).
    eval_export_token: str = ""
```

- [ ] **Step 3b: Add the endpoint to `main.py`**

Change the FastAPI import line to include `HTTPException`:
```python
from fastapi import FastAPI, BackgroundTasks, HTTPException
```

Add the endpoint (near the other `@app.get` debug routes):
```python
@app.get("/eval/correction-cases")
async def correction_cases_export(key: str = ""):
    """Espone gli eval case approvati (per l'importer locale). Protetto da token."""
    from rag import corrections
    if not settings.eval_export_token:
        raise HTTPException(status_code=404)
    if key != settings.eval_export_token:
        raise HTTPException(status_code=403)
    return {"cases": corrections.get_approved_cases()}
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_export_endpoint.py -q`
Expected: PASS
Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add config.py main.py tests/test_export_endpoint.py
git commit -m "feat(corrections): endpoint export eval case approvati (token, fail-closed)"
```

---

## Task 5: Importer locale (`eval/import_correction_cases.py`)

**Files:**
- Create: `eval/import_correction_cases.py`
- Test: `tests/test_import_correction_cases.py`

- [ ] **Step 1: Write failing test — create `tests/test_import_correction_cases.py`**

```python
import yaml
import eval.import_correction_cases as imp


def test_merge_dedups_by_id():
    existing = [{"id": "corr-a", "rule": "x"}]
    incoming = [{"id": "corr-a", "rule": "x"}, {"id": "corr-b", "rule": "y"}]
    merged, added = imp._merge(existing, incoming)
    assert added == 1
    assert [c["id"] for c in merged] == ["corr-a", "corr-b"]


def test_main_writes_yaml(monkeypatch, tmp_path):
    target = tmp_path / "corrections.yaml"
    monkeypatch.setattr(imp, "CASES_FILE", target)
    monkeypatch.setattr(imp, "_fetch", lambda base, token: [
        {"id": "corr-1", "category": "corrections", "venue": "gate_milano",
         "user_message": "u", "rubric": {"must": ["x"], "must_not": []}},
    ])
    rc = imp.main(["http://bot.example", "--token", "secret"])
    assert rc == 0
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data[0]["id"] == "corr-1"
    # idempotente: seconda esecuzione non duplica
    imp.main(["http://bot.example", "--token", "secret"])
    data2 = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert len(data2) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_import_correction_cases.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'eval.import_correction_cases'`)

- [ ] **Step 3: Create `eval/import_correction_cases.py`**

```python
"""Importa nel repo gli eval case approvati esposti dal bot.

Uso: python -m eval.import_correction_cases <base_url> --token <TOKEN>
Idempotente: salta gli id già presenti in eval/cases/corrections.yaml.
Gira in locale (usa httpx + pyyaml, dev-deps); non parte in produzione.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
import yaml

CASES_FILE = Path(__file__).parent / "cases" / "corrections.yaml"


def _fetch(base_url: str, token: str) -> list[dict]:
    url = base_url.rstrip("/") + "/eval/correction-cases"
    r = httpx.get(url, params={"key": token}, timeout=20)
    r.raise_for_status()
    return r.json().get("cases", [])


def _merge(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], int]:
    seen = {c.get("id") for c in existing}
    added = [c for c in incoming if c.get("id") not in seen]
    return existing + added, len(added)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("base_url")
    p.add_argument("--token", required=True)
    args = p.parse_args(argv)

    incoming = _fetch(args.base_url, args.token)
    existing: list[dict] = []
    if CASES_FILE.exists():
        existing = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8")) or []
    merged, added = _merge(existing, incoming)
    CASES_FILE.parent.mkdir(parents=True, exist_ok=True)
    CASES_FILE.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Importati {added} nuovi casi (totale {len(merged)}) in {CASES_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_import_correction_cases.py -q`
Expected: PASS
Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add eval/import_correction_cases.py tests/test_import_correction_cases.py
git commit -m "feat(corrections): importer locale eval case approvati (dedup per id)"
```

---

## Verifica finale
- `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest -q` → tutto verde.
- `EVAL_CONCURRENCY=2 ./.venv/bin/python -m eval.run` (richiede `.env`) → 27/27 (nessun caso `corrections` ancora nel repo → nessun effetto).

## Verifica manuale (post-deploy, con George)
1. Discord: rispondi a un embed del bot con `!regola <direttiva>` → ✅ + bozza MUST/MUST NOT.
2. `!approva <id>` → ✅.
3. Imposta `EVAL_EXPORT_TOKEN` su Railway. In sessione: `python -m eval.import_correction_cases https://<bot-url> --token <TOKEN>` → scrive `eval/cases/corrections.yaml`.
4. `python -m eval.run` per validare i nuovi casi → commit.

## Push
A piano completato e suite verde, chiedere a George l'ok per `git push` + PR (deploy Railway). Ricordare la env var `EVAL_EXPORT_TOKEN`.
