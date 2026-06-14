# Consolidamento Correzioni → KB (Fase 2B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un tool locale, LLM-assistito e revisionato da umano, che fonde le correzioni con eval case approvato nella KB canonica (solo edit additivi), validato dagli eval case di 2A.

**Architecture:** `rag/corrections.py` espone le regole approvate; `main.py` aggiunge un endpoint read-only `GET /eval/corrections` (token); `eval/consolidate_corrections.py` (locale) recupera le regole, un LLM propone dove inserirle nella KB, e il tool applica edit SOLO additivi (con dedup e sezione di fallback). George rivede il `git diff`, gira l'eval, committa, e poi rimuove le correzioni con `!rimuovi`.

**Tech Stack:** Python 3.9, FastAPI, anthropic (tool-use), httpx, pytest (`asyncio_mode=auto`). Test env: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest ...`. `FakeClient` da `tests.conftest`.

**Commit:** ogni task committa LOCALMENTE. Push/PR a fine piano con ok di George.

**Spec:** `docs/superpowers/specs/2026-06-14-consolidate-corrections-to-kb-design.md`

---

## File Structure
- **Modify** `rag/corrections.py` — `get_approved_corrections()`.
- **Modify** `main.py` — `GET /eval/corrections`.
- **Create** `eval/consolidate_corrections.py` — `_apply_edit` (T3); `_fetch`/`propose_placement`/`main` (T4).
- **Modify** `tests/test_corrections.py`, `tests/test_export_endpoint.py`; **Create** `tests/test_consolidate_corrections.py`.

---

## Task 1: Espone le regole approvate (`rag/corrections.py`)

**Files:**
- Modify: `rag/corrections.py`
- Test: `tests/test_corrections.py`

- [ ] **Step 1: Append failing test to `tests/test_corrections.py`**

```python
def test_get_approved_corrections(monkeypatch, tmp_path):
    c = _fresh(monkeypatch, tmp_path)
    cid = c.add_correction("gate_milano", "manda a marketing@", {}, "George")
    c.add_correction("gate_milano", "regola senza caso", {}, "George")  # non approvata
    c.set_case(cid, {"id": f"corr-{cid}", "rubric": {"must": ["x"], "must_not": []}})
    c.approve_case(cid)
    approved = c.get_approved_corrections()
    assert approved == [{"id": cid, "venue": "gate_milano", "rule": "manda a marketing@"}]
```

- [ ] **Step 2: Run to verify failure**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_corrections.py -q`
Expected: FAIL (AttributeError: ... has no attribute 'get_approved_corrections')

- [ ] **Step 3: Add the function to `rag/corrections.py`** (right after `get_approved_cases`)

```python
def get_approved_corrections() -> list[dict]:
    _ensure_loaded()
    out: list[dict] = []
    for items in _store.values():
        for c in items:
            if c.get("case_status") == "approved":
                out.append({"id": c["id"], "venue": c["venue"], "rule": c["rule"]})
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_corrections.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add rag/corrections.py tests/test_corrections.py
git commit -m "feat(consolidate): get_approved_corrections (regole approvate)"
```

---

## Task 2: Endpoint read-only (`main.py`)

**Files:**
- Modify: `main.py`
- Test: `tests/test_export_endpoint.py`

- [ ] **Step 1: Append failing test to `tests/test_export_endpoint.py`**

```python
def test_corrections_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    monkeypatch.setattr("config.settings.eval_export_token", "")
    assert client.get("/eval/corrections").status_code == 404
    monkeypatch.setattr("config.settings.eval_export_token", "secret")
    assert client.get("/eval/corrections", params={"key": "wrong"}).status_code == 403
    monkeypatch.setattr(
        "rag.corrections.get_approved_corrections",
        lambda: [{"id": "a", "venue": "gate_milano", "rule": "r"}],
    )
    r = client.get("/eval/corrections", params={"key": "secret"})
    assert r.status_code == 200
    assert r.json() == {"corrections": [{"id": "a", "venue": "gate_milano", "rule": "r"}]}
```

- [ ] **Step 2: Run to verify failure**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_export_endpoint.py -q`
Expected: FAIL (the 403/200 assertions fail — route missing returns 404 for all).

- [ ] **Step 3: Add the endpoint to `main.py`** (right after the existing `correction_cases_export` route)

```python
@app.get("/eval/corrections")
async def corrections_export(key: str = ""):
    """Espone le correzioni approvate (regole) per il consolidamento locale. Protetto da token."""
    from rag import corrections
    if not settings.eval_export_token:
        raise HTTPException(status_code=404)
    if key != settings.eval_export_token:
        raise HTTPException(status_code=403)
    return {"corrections": corrections.get_approved_corrections()}
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_export_endpoint.py -q`  → PASS
Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest -q`  → all pass

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_export_endpoint.py
git commit -m "feat(consolidate): endpoint read-only /eval/corrections (token, fail-closed)"
```

---

## Task 3: Edit additivo della KB (`eval/consolidate_corrections.py`)

**Files:**
- Create: `eval/consolidate_corrections.py`
- Test: `tests/test_consolidate_corrections.py`

- [ ] **Step 1: Write failing test — create `tests/test_consolidate_corrections.py`**

```python
import eval.consolidate_corrections as cc


def test_apply_edit_under_existing_heading():
    kb = "# Titolo\n\n## Biglietti\n- riga esistente\n\n## Altro\n- x\n"
    out = cc._apply_edit(kb, "## Biglietti", "nuova regola biglietti")
    lines = out.split("\n")
    i = lines.index("## Biglietti")
    assert lines[i + 1] == "- nuova regola biglietti"
    assert "- riga esistente" in out  # non rimuove l'esistente


def test_apply_edit_fallback_when_heading_missing():
    kb = "# Titolo\n\n## Biglietti\n- x\n"
    out = cc._apply_edit(kb, "## Inesistente", "regola orfana")
    assert cc._CONSOLIDATED_SECTION in out
    assert "- regola orfana" in out


def test_apply_edit_dedup_no_change_if_present():
    kb = "# Titolo\n\n## Biglietti\n- regola gia presente\n"
    out = cc._apply_edit(kb, "## Biglietti", "regola gia presente")
    assert out == kb
```

- [ ] **Step 2: Run to verify failure**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_consolidate_corrections.py -q`
Expected: FAIL (ModuleNotFoundError: No module named 'eval.consolidate_corrections')

- [ ] **Step 3: Create `eval/consolidate_corrections.py`** with the pure helper

```python
"""Consolida le correzioni staff approvate nella KB canonica (tool locale, dev-only).

Uso: python -m eval.consolidate_corrections <base_url> --token <TOKEN>
Edit SOLO additivi alla KB (mai riscrive testo esistente). Vedi
docs/superpowers/specs/2026-06-14-consolidate-corrections-to-kb-design.md.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CONSOLIDATED_SECTION = "## Regole consolidate (da correzioni staff)"


def _apply_edit(kb_text: str, section: str, line: str) -> str:
    """Aggiunge '- line' sotto l'heading 'section' se esiste, altrimenti sotto una
    sezione dedicata in fondo. Idempotente: se 'line' è già nel testo, no-op.
    Non riscrive MAI testo esistente."""
    if line in kb_text:  # dedup
        return kb_text
    bullet = f"- {line}"
    heading = (section or "").strip()
    lines = kb_text.split("\n")
    if heading and heading in lines:
        i = lines.index(heading)
        lines.insert(i + 1, bullet)
        return "\n".join(lines)
    # fallback: sezione consolidata in fondo
    text = kb_text.rstrip()
    if _CONSOLIDATED_SECTION not in kb_text:
        text += f"\n\n{_CONSOLIDATED_SECTION}"
    return text + f"\n{bullet}\n"
```

- [ ] **Step 4: Run to verify pass**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_consolidate_corrections.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/consolidate_corrections.py tests/test_consolidate_corrections.py
git commit -m "feat(consolidate): edit additivo della KB (insert/fallback/dedup)"
```

---

## Task 4: Fetch + LLM placement + orchestrazione (`eval/consolidate_corrections.py`)

**Files:**
- Modify: `eval/consolidate_corrections.py`
- Test: `tests/test_consolidate_corrections.py`

- [ ] **Step 1: Append failing tests to `tests/test_consolidate_corrections.py`**

```python
import config
from tests.conftest import FakeClient


async def test_propose_placement_returns_section_and_line():
    client = FakeClient({"section": "## Biglietti", "line": "manda a marketing@"})
    out = await cc.propose_placement("## Biglietti\n## Altro", "regola", client=client, model="x")
    assert out == {"section": "## Biglietti", "line": "manda a marketing@"}


async def test_propose_placement_none_on_empty_line():
    client = FakeClient({"section": "", "line": ""})
    out = await cc.propose_placement("## Biglietti", "regola", client=client, model="x")
    assert out is None


async def test_main_consolidates_and_prints_ids(monkeypatch, tmp_path, capsys):
    (tmp_path / "gate_milano.md").write_text("# KB\n\n## Biglietti\n- x\n", encoding="utf-8")
    monkeypatch.setattr(config, "KNOWLEDGE_DIR", tmp_path)
    monkeypatch.setattr(cc, "_fetch", lambda base, token: [
        {"id": "c1", "venue": "gate_milano", "rule": "manda a marketing@"},
    ])

    async def fake_place(kb, rule, *, client, model):
        return {"section": "## Biglietti", "line": "manda a marketing@"}

    monkeypatch.setattr(cc, "propose_placement", fake_place)
    rc = await cc.main(["http://x", "--token", "secret"])
    assert rc == 0
    kb = (tmp_path / "gate_milano.md").read_text(encoding="utf-8")
    assert "- manda a marketing@" in kb
    assert "c1" in capsys.readouterr().out  # stampa l'id consolidato
    # idempotente: la regola è ora nella KB → seconda esecuzione non la ri-aggiunge
    rc2 = await cc.main(["http://x", "--token", "secret"])
    kb2 = (tmp_path / "gate_milano.md").read_text(encoding="utf-8")
    assert kb2.count("- manda a marketing@") == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_consolidate_corrections.py -q`
Expected: FAIL (AttributeError: module has no attribute 'propose_placement')

- [ ] **Step 3: Extend `eval/consolidate_corrections.py`**

Add these imports at the top (after `import logging`):
```python
import argparse
import asyncio
import sys

import httpx

import config
```

Add the rest below `_apply_edit`:
```python
_PLACEMENT_INSTRUCTIONS = """\
Integri una REGOLA staff nella knowledge base di un chatbot di un club/venue.
Ricevi la regola e l'elenco delle sezioni (heading) della KB.
Scegli la sezione più pertinente: riporta il testo ESATTO dell'heading (incluso "## ").
Se nessuna è adatta, lascia 'section' vuoto.
Scrivi 'line': una sola riga di guida, concisa, in stile KB, che cattura la regola
(senza markdown, senza trattino iniziale).
Registra con lo strumento propose_kb_placement.
"""

_PLACEMENT_TOOL = {
    "name": "propose_kb_placement",
    "description": "Propone dove integrare la regola nella KB.",
    "input_schema": {
        "type": "object",
        "properties": {
            "section": {"type": "string"},
            "line": {"type": "string"},
        },
        "required": ["section", "line"],
    },
}


def _headings(kb_text: str) -> list[str]:
    return [l for l in kb_text.split("\n") if l.startswith("## ")]


def _format_placement(kb_text: str, rule: str) -> str:
    hs = "\n".join(_headings(kb_text)) or "(nessuna sezione)"
    return f"REGOLA:\n{rule}\n\nSEZIONI DISPONIBILI:\n{hs}"


async def propose_placement(kb_text: str, rule: str, *, client, model: str) -> dict | None:
    """Chiede all'LLM dove inserire la regola. Ritorna {section, line} o None."""
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=300,
            temperature=0,
            system=_PLACEMENT_INSTRUCTIONS,
            tools=[_PLACEMENT_TOOL],
            tool_choice={"type": "tool", "name": "propose_kb_placement"},
            messages=[{"role": "user", "content": _format_placement(kb_text, rule)}],
        )
    except Exception:
        logger.exception("propose_placement: errore LLM")
        return None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            data = block.input
            if data.get("line"):
                return {"section": data.get("section", "") or "", "line": data["line"]}
    return None


def _fetch(base_url: str, token: str) -> list[dict]:
    url = base_url.rstrip("/") + "/eval/corrections"
    r = httpx.get(url, params={"key": token}, timeout=20)
    r.raise_for_status()
    return r.json().get("corrections", [])


async def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("base_url")
    p.add_argument("--token", required=True)
    args = p.parse_args(argv)

    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=config.settings.anthropic_api_key)
    model = config.settings.model

    corrections = _fetch(args.base_url, args.token)
    if not corrections:
        print("Niente da consolidare.")
        return 0

    by_venue: dict[str, list[dict]] = {}
    for c in corrections:
        by_venue.setdefault(c["venue"], []).append(c)

    consolidated_ids: list[str] = []
    for venue, items in by_venue.items():
        kb_path = config.KNOWLEDGE_DIR / f"{venue}.md"
        if not kb_path.exists():
            print(f"⚠️ KB mancante per {venue}, salto")
            continue
        kb = kb_path.read_text(encoding="utf-8")
        changed = False
        for corr in items:
            if corr["rule"] in kb:
                continue  # già presente
            placement = await propose_placement(kb, corr["rule"], client=client, model=model)
            if not placement:
                print(f"⚠️ Salto {corr['id']}: placement non generato")
                continue
            new_kb = _apply_edit(kb, placement["section"], placement["line"])
            if new_kb != kb:
                kb = new_kb
                changed = True
                consolidated_ids.append(corr["id"])
        if changed:
            kb_path.write_text(kb, encoding="utf-8")

    if consolidated_ids:
        print("Consolidati:", ", ".join(consolidated_ids))
        print("Rivedi col `git diff`, poi `python -m eval.run`, poi commit+push.")
        print("Infine su Discord: " + " ".join(f"!rimuovi {i}" for i in consolidated_ids))
    else:
        print("Nessuna regola consolidata (già presenti o placement falliti).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest tests/test_consolidate_corrections.py -q`  → PASS
Run: `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest -q`  → all pass

- [ ] **Step 5: Commit**

```bash
git add eval/consolidate_corrections.py tests/test_consolidate_corrections.py
git commit -m "feat(consolidate): fetch + LLM placement + orchestrazione consolidamento"
```

---

## Verifica finale
- `ANTHROPIC_API_KEY=dummy WA_ACCESS_TOKEN=dummy ./.venv/bin/python -m pytest -q` → tutto verde.
- `EVAL_CONCURRENCY=2 ./.venv/bin/python -m eval.run` (richiede `.env`) → 27/27 (la 2B non cambia il runtime del bot; nota: `vip-extra-modello-misto` può fallire per l'anomalia judge pre-esistente — non è una regressione).

## Verifica manuale (con George)
1. Con correzioni approvate in produzione: `python -m eval.consolidate_corrections https://<bot-url> --token <EVAL_EXPORT_TOKEN>`.
2. `git diff rag/knowledge/` → rivedi gli edit additivi.
3. `python -m eval.run` → i casi `corrections` (2A) passano contro la sola KB.
4. commit + push (deploy KB).
5. Su Discord, `!rimuovi <id>` per ogni id stampato dal tool.

## Push
A piano completato e suite verde, chiedere a George l'ok per push + PR.
