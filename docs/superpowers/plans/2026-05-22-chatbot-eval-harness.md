# Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Costruire un eval harness che misuri il comportamento del chatbot Gate Milano su casi reali, così che le future modifiche al system prompt si possano validare contro un baseline.

**Architecture:** Test set YAML con contesto RAG congelato → runner che chiama la stessa `generate_response()` di produzione → asserzioni deterministiche (costo zero) + LLM-as-judge su Sonnet → report con diff vs run precedente. Tutti i pezzi puri sono testati in TDD; i pezzi che chiamano l'API accettano client/funzioni iniettabili così sono testabili con fake.

**Tech Stack:** Python 3.14, `anthropic` (AsyncAnthropic), `pyyaml`, `pytest` + `pytest-asyncio`. Riferimento spec: `docs/superpowers/specs/2026-05-22-chatbot-eval-harness-design.md`.

---

## File Structure

```
eval/
  __init__.py          # package marker
  schema.py            # dataclasses: Assertions, Rubric, Case, JudgeVerdict, CaseResult
  loader.py            # load_cases(dir) -> list[Case]  (+ validazione)
  assertions.py        # detect_markdown(text), run_assertions(reply, assertions) -> list[str]
  judge.py             # build_judge_system(), parse_verdict(resp), judge_reply(case, reply, *, client, model)
  run.py               # run_case(...), run_all(...), main()  (entrypoint: python -m eval.run)
  report.py            # load_results, summarize, diff, render, main()  (python -m eval.report)
  cases/
    system_exposure.yaml
    vip_tables.yaml
    hours.yaml
    date_logic.yaml
    regression.yaml
  data/discord_sample_2026-05.json   # già presente (gitignored)
  results/             # output run (gitignored)
tests/
  __init__.py
  conftest.py          # fixtures: fake anthropic client/response
  test_assertions.py
  test_loader.py
  test_judge.py
  test_run.py
  test_report.py
```

Note di responsabilità:
- `schema.py` non ha logica, solo dati — testato indirettamente via loader/run.
- `assertions.py`, `report.py` sono funzioni pure → TDD pesante.
- `judge.py` e `run.py` isolano l'I/O di rete dietro parametri iniettabili → testati con fake, nessuna chiamata reale nei test.
- Il runner usa `ai.claude_client.generate_response` **as-is** (nessuna modifica a file di produzione in questo piano — vedi spec, caching rinviato alla fase 2).

---

### Task 0: Setup ambiente e scheletro package

**Files:**
- Restore: `requirements.txt` (cancellata sul working tree, presente in HEAD)
- Modify: `requirements.txt` (aggiungi pyyaml, pytest, pytest-asyncio)
- Modify: `.gitignore` (aggiungi `eval/results/`)
- Create: `eval/__init__.py`, `tests/__init__.py`, `pytest.ini`

- [ ] **Step 1: Ripristina requirements.txt e crea venv**

```bash
cd /Users/george/Documents/Claude/gatemilano-chatbot
git checkout requirements.txt
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
Expected: install ok (fastapi, anthropic, ecc.).

- [ ] **Step 2: Aggiungi le dipendenze di sviluppo**

Aggiungi in fondo a `requirements.txt`:
```
pyyaml>=6.0
pytest>=8.0
pytest-asyncio>=0.23
```
Poi:
```bash
pip install pyyaml pytest pytest-asyncio
```

- [ ] **Step 3: Crea pytest.ini**

Create `pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 4: Crea i package marker e ignora i risultati**

```bash
mkdir -p eval/cases eval/results tests
touch eval/__init__.py tests/__init__.py
echo "eval/results/" >> .gitignore
```

- [ ] **Step 5: Verifica che pytest giri (a vuoto)**

Run: `pytest`
Expected: "no tests ran" senza errori di import.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .gitignore pytest.ini eval/__init__.py tests/__init__.py
git commit -m "chore: setup eval package skeleton + dev deps"
```

---

### Task 1: schema.py — strutture dati

**Files:**
- Create: `eval/schema.py`
- Test: coperto indirettamente in Task 3/5 (nessun test dedicato: solo dataclass)

- [ ] **Step 1: Scrivi schema.py**

Create `eval/schema.py`:
```python
"""Strutture dati dell'eval harness (solo dati, nessuna logica I/O)."""
from dataclasses import dataclass, field


@dataclass
class Assertions:
    forbidden_substrings: list[str] = field(default_factory=list)
    forbidden_markdown: bool = False


@dataclass
class Rubric:
    must: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.must and not self.must_not


@dataclass
class Case:
    id: str
    category: str
    venue: str
    user_message: str
    rag_context: str = ""
    history: list[dict] = field(default_factory=list)
    rubric: Rubric = field(default_factory=Rubric)
    assertions: Assertions = field(default_factory=Assertions)


@dataclass
class JudgeVerdict:
    verdict: str  # "pass" | "fail"
    violated: list[str] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class CaseResult:
    id: str
    category: str
    user_message: str
    reply: str
    assertion_failures: list[str] = field(default_factory=list)
    judge: JudgeVerdict | None = None
    usage: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        if self.assertion_failures:
            return False
        if self.judge is None:
            return True
        return self.judge.verdict == "pass"
```

- [ ] **Step 2: Verifica import**

Run: `python -c "from eval.schema import Case, CaseResult, JudgeVerdict; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add eval/schema.py
git commit -m "feat(eval): strutture dati del harness"
```

---

### Task 2: assertions.py — controlli deterministici

**Files:**
- Create: `eval/assertions.py`
- Test: `tests/test_assertions.py`

- [ ] **Step 1: Scrivi i test (falliscono)**

Create `tests/test_assertions.py`:
```python
from eval.schema import Assertions
from eval.assertions import detect_markdown, run_assertions


def test_detect_markdown_finds_bold():
    assert detect_markdown("ecco il **prezzo** giusto") != []


def test_detect_markdown_finds_bullet_lines():
    assert detect_markdown("Opzioni:\n- uno\n- due") != []


def test_detect_markdown_clean_text_ok():
    assert detect_markdown("Stasera c'e Perreo XL alle 23, biglietti da 10 euro.") == []


def test_forbidden_substring_caught_case_insensitive():
    a = Assertions(forbidden_substrings=["database", "+39 391 487 6443"])
    fails = run_assertions("non e nel mio DATABASE", a)
    assert any("database" in f.lower() for f in fails)


def test_forbidden_markdown_flag():
    a = Assertions(forbidden_markdown=True)
    assert run_assertions("ecco **questo**", a) != []


def test_no_assertions_no_failures():
    assert run_assertions("qualsiasi testo", Assertions()) == []
```

- [ ] **Step 2: Esegui i test (devono fallire)**

Run: `pytest tests/test_assertions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.assertions'`

- [ ] **Step 3: Implementa assertions.py**

Create `eval/assertions.py`:
```python
"""Controlli deterministici sulla risposta del bot (eseguiti prima del judge)."""
import re

from eval.schema import Assertions

# Marcatori markdown vietati su WhatsApp (il bot deve scrivere testo semplice)
_BOLD = re.compile(r"\*{1,3}[^*\n]+\*{1,3}")
_ITALIC = re.compile(r"(?<!\w)_{1,2}[^_\n]+_{1,2}(?!\w)")
_BULLET = re.compile(r"^\s*[-*]\s+", re.MULTILINE)


def detect_markdown(text: str) -> list[str]:
    """Ritorna l'elenco dei marcatori markdown trovati (vuoto se pulito)."""
    found = []
    if _BOLD.search(text):
        found.append("bold (*...*)")
    if _ITALIC.search(text):
        found.append("italic (_..._)")
    if _BULLET.search(text):
        found.append("bullet list")
    return found


def run_assertions(reply: str, assertions: Assertions) -> list[str]:
    """Ritorna l'elenco delle violazioni deterministiche (vuoto = pass)."""
    failures: list[str] = []
    low = reply.lower()
    for sub in assertions.forbidden_substrings:
        if sub.lower() in low:
            failures.append(f"contiene la stringa vietata: {sub!r}")
    if assertions.forbidden_markdown:
        for marker in detect_markdown(reply):
            failures.append(f"markdown vietato: {marker}")
    return failures
```

- [ ] **Step 4: Esegui i test (devono passare)**

Run: `pytest tests/test_assertions.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add eval/assertions.py tests/test_assertions.py
git commit -m "feat(eval): asserzioni deterministiche (markdown, stringhe vietate)"
```

---

### Task 3: loader.py — caricamento casi YAML

**Files:**
- Create: `eval/loader.py`
- Test: `tests/test_loader.py`

- [ ] **Step 1: Scrivi i test (falliscono)**

Create `tests/test_loader.py`:
```python
import textwrap
import pytest
from eval.loader import load_cases, CaseValidationError


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_load_single_case(tmp_path):
    _write(tmp_path, "a.yaml", """
        - id: c1
          category: system_exposure
          venue: gate_milano
          user_message: "ciao"
          rubric:
            must_not: ["non deve esporre il database"]
          assertions:
            forbidden_substrings: ["database"]
    """)
    cases = load_cases(tmp_path)
    assert len(cases) == 1
    c = cases[0]
    assert c.id == "c1"
    assert c.venue == "gate_milano"
    assert c.rubric.must_not == ["non deve esporre il database"]
    assert c.assertions.forbidden_substrings == ["database"]


def test_load_merges_multiple_files(tmp_path):
    _write(tmp_path, "a.yaml", '- {id: a, category: x, venue: gate_milano, user_message: "m"}')
    _write(tmp_path, "b.yaml", '- {id: b, category: x, venue: gate_milano, user_message: "m"}')
    ids = {c.id for c in load_cases(tmp_path)}
    assert ids == {"a", "b"}


def test_missing_required_field_raises(tmp_path):
    _write(tmp_path, "bad.yaml", '- {id: c1, category: x, venue: gate_milano}')
    with pytest.raises(CaseValidationError):
        load_cases(tmp_path)


def test_duplicate_id_raises(tmp_path):
    _write(tmp_path, "a.yaml", '- {id: dup, category: x, venue: gate_milano, user_message: "m"}')
    _write(tmp_path, "b.yaml", '- {id: dup, category: x, venue: gate_milano, user_message: "m"}')
    with pytest.raises(CaseValidationError):
        load_cases(tmp_path)
```

- [ ] **Step 2: Esegui i test (devono fallire)**

Run: `pytest tests/test_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.loader'`

- [ ] **Step 3: Implementa loader.py**

Create `eval/loader.py`:
```python
"""Caricamento e validazione dei casi di test da file YAML."""
from pathlib import Path

import yaml

from eval.schema import Assertions, Case, Rubric

_REQUIRED = ("id", "category", "venue", "user_message")


class CaseValidationError(Exception):
    pass


def _parse_case(raw: dict, source: str) -> Case:
    for key in _REQUIRED:
        if not raw.get(key):
            raise CaseValidationError(f"{source}: caso senza campo obbligatorio {key!r}: {raw!r}")
    rubric_raw = raw.get("rubric") or {}
    assert_raw = raw.get("assertions") or {}
    return Case(
        id=raw["id"],
        category=raw["category"],
        venue=raw["venue"],
        user_message=raw["user_message"],
        rag_context=raw.get("rag_context", "") or "",
        history=raw.get("history") or [],
        rubric=Rubric(
            must=rubric_raw.get("must") or [],
            must_not=rubric_raw.get("must_not") or [],
        ),
        assertions=Assertions(
            forbidden_substrings=assert_raw.get("forbidden_substrings") or [],
            forbidden_markdown=bool(assert_raw.get("forbidden_markdown", False)),
        ),
    )


def load_cases(cases_dir) -> list[Case]:
    cases_dir = Path(cases_dir)
    cases: list[Case] = []
    seen: set[str] = set()
    for path in sorted(cases_dir.glob("*.yaml")):
        raw_list = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(raw_list, list):
            raise CaseValidationError(f"{path.name}: il file deve contenere una lista di casi")
        for raw in raw_list:
            case = _parse_case(raw, path.name)
            if case.id in seen:
                raise CaseValidationError(f"id duplicato: {case.id!r}")
            seen.add(case.id)
            cases.append(case)
    return cases
```

- [ ] **Step 4: Esegui i test (devono passare)**

Run: `pytest tests/test_loader.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add eval/loader.py tests/test_loader.py
git commit -m "feat(eval): loader e validazione casi YAML"
```

---

### Task 4: judge.py — LLM-as-judge (Sonnet)

**Files:**
- Create: `eval/judge.py`
- Test: `tests/test_judge.py`
- Create/Modify: `tests/conftest.py` (fake client/response)

- [ ] **Step 1: Scrivi le fixture fake**

Create `tests/conftest.py`:
```python
import pytest


class FakeBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.name = "record_verdict"
        self.input = payload


class FakeUsage:
    def __init__(self, **kw):
        self.input_tokens = kw.get("input_tokens", 0)
        self.output_tokens = kw.get("output_tokens", 0)
        self.cache_read_input_tokens = kw.get("cache_read_input_tokens", 0)
        self.cache_creation_input_tokens = kw.get("cache_creation_input_tokens", 0)


class FakeResponse:
    def __init__(self, payload, usage=None):
        self.content = [FakeBlock(payload)]
        self.usage = usage or FakeUsage()


class FakeMessages:
    def __init__(self, payload):
        self._payload = payload
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeResponse(self._payload)


class FakeClient:
    def __init__(self, payload):
        self.messages = FakeMessages(payload)


@pytest.fixture
def fake_judge_client():
    def _make(verdict="pass", violated=None, reasoning="ok"):
        return FakeClient({"verdict": verdict, "violated": violated or [], "reasoning": reasoning})
    return _make
```

- [ ] **Step 2: Scrivi i test (falliscono)**

Create `tests/test_judge.py`:
```python
import pytest
from eval.schema import Case, Rubric
from eval.judge import parse_verdict, judge_reply, build_judge_system
from tests.conftest import FakeResponse


def test_parse_verdict_reads_tool_block():
    resp = FakeResponse({"verdict": "fail", "violated": ["espone email"], "reasoning": "x"})
    v = parse_verdict(resp)
    assert v.verdict == "fail"
    assert v.violated == ["espone email"]


def test_build_judge_system_has_cache_control():
    blocks = build_judge_system()
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_judge_reply_passes_rubric_and_uses_tool(fake_judge_client):
    client = fake_judge_client(verdict="pass")
    case = Case(id="c", category="x", venue="gate_milano",
                user_message="ciao", rubric=Rubric(must_not=["niente database"]))
    v = await judge_reply(case, "ciao come posso aiutarti?", client=client, model="claude-sonnet-4-6")
    assert v.verdict == "pass"
    kwargs = client.messages.last_kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "record_verdict"}
    # la rubrica del caso deve finire nel messaggio utente
    assert "niente database" in str(kwargs["messages"])
```

- [ ] **Step 3: Esegui i test (devono fallire)**

Run: `pytest tests/test_judge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.judge'`

- [ ] **Step 4: Implementa judge.py**

Create `eval/judge.py`:
```python
"""LLM-as-judge: valuta una risposta del bot contro la rubrica del caso."""
from eval.schema import Case, JudgeVerdict

_JUDGE_INSTRUCTIONS = """\
Sei un valutatore severo di un chatbot per un club/venue.
Ricevi: il messaggio dell'utente, la risposta del bot, e una rubrica di criteri.
Valuta la risposta SOLO contro i criteri della rubrica, non con criteri tuoi.
- I criteri 'must' devono essere tutti soddisfatti.
- I criteri 'must_not' non devono essere violati.
Se anche un solo criterio non e rispettato, il verdetto e 'fail'.
Elenca in 'violated' i criteri non rispettati (testo esatto). Sii conciso nel reasoning.
Registra sempre il risultato con lo strumento record_verdict.
"""

_VERDICT_TOOL = {
    "name": "record_verdict",
    "description": "Registra il verdetto della valutazione.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "fail"]},
            "violated": {"type": "array", "items": {"type": "string"}},
            "reasoning": {"type": "string"},
        },
        "required": ["verdict", "violated", "reasoning"],
    },
}


def build_judge_system() -> list[dict]:
    """System come blocchi: prefisso statico cacheabile."""
    return [{"type": "text", "text": _JUDGE_INSTRUCTIONS, "cache_control": {"type": "ephemeral"}}]


def _format_user(case: Case, reply: str) -> str:
    must = "\n".join(f"- {m}" for m in case.rubric.must) or "(nessuno)"
    must_not = "\n".join(f"- {m}" for m in case.rubric.must_not) or "(nessuno)"
    return (
        f"MESSAGGIO UTENTE:\n{case.user_message}\n\n"
        f"RISPOSTA DEL BOT:\n{reply}\n\n"
        f"CRITERI must (devono valere):\n{must}\n\n"
        f"CRITERI must_not (non devono valere):\n{must_not}"
    )


def parse_verdict(response) -> JudgeVerdict:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            data = block.input
            return JudgeVerdict(
                verdict=data.get("verdict", "fail"),
                violated=data.get("violated", []),
                reasoning=data.get("reasoning", ""),
            )
    return JudgeVerdict(verdict="fail", violated=["judge: nessun tool_use nella risposta"], reasoning="")


async def judge_reply(case: Case, reply: str, *, client, model: str) -> JudgeVerdict:
    response = await client.messages.create(
        model=model,
        max_tokens=500,
        system=build_judge_system(),
        tools=[_VERDICT_TOOL],
        tool_choice={"type": "tool", "name": "record_verdict"},
        messages=[{"role": "user", "content": _format_user(case, reply)}],
    )
    return parse_verdict(response)
```

- [ ] **Step 5: Esegui i test (devono passare)**

Run: `pytest tests/test_judge.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add eval/judge.py tests/test_judge.py tests/conftest.py
git commit -m "feat(eval): LLM-as-judge su Sonnet con tool use + prompt caching"
```

---

### Task 5: run.py — orchestrazione

**Files:**
- Create: `eval/run.py`
- Test: `tests/test_run.py`

- [ ] **Step 1: Scrivi i test (falliscono)**

Create `tests/test_run.py`:
```python
import pytest
from eval.schema import Case, Rubric, Assertions, JudgeVerdict
from eval.run import run_case, run_all


def _case(**kw):
    base = dict(id="c", category="x", venue="gate_milano", user_message="ciao")
    base.update(kw)
    return Case(**base)


@pytest.mark.asyncio
async def test_run_case_skips_judge_on_assertion_failure():
    judge_calls = []

    async def gen(venue, user_message, rag_context, history):
        return "non e nel mio database"

    async def judge(case, reply):
        judge_calls.append(case.id)
        return JudgeVerdict("pass")

    case = _case(assertions=Assertions(forbidden_substrings=["database"]))
    res = await run_case(case, generate_fn=gen, judge_fn=judge)
    assert res.passed is False
    assert res.assertion_failures
    assert judge_calls == []  # judge NON chiamato


@pytest.mark.asyncio
async def test_run_case_calls_judge_when_assertions_pass():
    async def gen(venue, user_message, rag_context, history):
        return "ciao, come posso aiutarti?"

    async def judge(case, reply):
        return JudgeVerdict("fail", violated=["x"])

    case = _case(rubric=Rubric(must_not=["x"]))
    res = await run_case(case, generate_fn=gen, judge_fn=judge)
    assert res.judge.verdict == "fail"
    assert res.passed is False


@pytest.mark.asyncio
async def test_run_all_runs_every_case():
    async def gen(venue, user_message, rag_context, history):
        return "ok"

    async def judge(case, reply):
        return JudgeVerdict("pass")

    cases = [_case(id="a"), _case(id="b"), _case(id="c")]
    results = await run_all(cases, generate_fn=gen, judge_fn=judge, concurrency=2)
    assert {r.id for r in results} == {"a", "b", "c"}
    assert all(r.passed for r in results)
```

- [ ] **Step 2: Esegui i test (devono fallire)**

Run: `pytest tests/test_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.run'`

- [ ] **Step 3: Implementa run.py**

Create `eval/run.py`:
```python
"""Runner dell'eval harness: esegue i casi e salva i risultati."""
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from eval.assertions import run_assertions
from eval.loader import load_cases
from eval.schema import Case, CaseResult

CASES_DIR = Path(__file__).parent / "cases"
RESULTS_DIR = Path(__file__).parent / "results"


async def run_case(case: Case, *, generate_fn, judge_fn) -> CaseResult:
    reply = await generate_fn(case.venue, case.user_message, case.rag_context, case.history)
    failures = run_assertions(reply, case.assertions)
    if failures:
        return CaseResult(
            id=case.id, category=case.category, user_message=case.user_message,
            reply=reply, assertion_failures=failures, judge=None,
        )
    verdict = None if case.rubric.is_empty() else await judge_fn(case, reply)
    return CaseResult(
        id=case.id, category=case.category, user_message=case.user_message,
        reply=reply, assertion_failures=[], judge=verdict,
    )


async def run_all(cases, *, generate_fn, judge_fn, concurrency: int = 5) -> list[CaseResult]:
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(c):
        async with sem:
            return await run_case(c, generate_fn=generate_fn, judge_fn=judge_fn)

    return await asyncio.gather(*(_guarded(c) for c in cases))


def save_results(results: list[CaseResult], model: str) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{ts}.json"
    payload = {
        "timestamp": ts,
        "model": model,
        "cases": [asdict(r) | {"passed": r.passed} for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def main() -> int:
    from anthropic import AsyncAnthropic
    from config import settings
    from ai.claude_client import generate_response
    from eval.judge import judge_reply

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    judge_model = settings.model  # Sonnet

    async def judge_fn(case, reply):
        return await judge_reply(case, reply, client=client, model=judge_model)

    cases = load_cases(CASES_DIR)
    if not cases:
        print("Nessun caso trovato in", CASES_DIR)
        return 1
    print(f"Eseguo {len(cases)} casi...")
    results = await run_all(cases, generate_fn=generate_response, judge_fn=judge_fn)
    path = save_results(results, model=settings.model)
    passed = sum(r.passed for r in results)
    print(f"Risultati: {passed}/{len(results)} pass — salvati in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 4: Esegui i test (devono passare)**

Run: `pytest tests/test_run.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add eval/run.py tests/test_run.py
git commit -m "feat(eval): runner con asserzioni-prima-del-judge e salvataggio risultati"
```

---

### Task 6: report.py — tabella + diff vs run precedente

**Files:**
- Create: `eval/report.py`
- Test: `tests/test_report.py`

- [ ] **Step 1: Scrivi i test (falliscono)**

Create `tests/test_report.py`:
```python
from eval.report import summarize, diff_runs, render


def _run(cases):
    return {"timestamp": "t", "model": "m", "cases": cases}


def _c(id, category, passed):
    return {"id": id, "category": category, "passed": passed,
            "reply": "", "user_message": "", "assertion_failures": [], "judge": None}


def test_summarize_counts_per_category():
    run = _run([_c("a", "vip", True), _c("b", "vip", False), _c("c", "hours", True)])
    s = summarize(run)
    assert s["vip"] == (1, 2)   # (passed, total)
    assert s["hours"] == (1, 1)


def test_diff_detects_regression_and_improvement():
    prev = _run([_c("a", "x", True), _c("b", "x", False)])
    curr = _run([_c("a", "x", False), _c("b", "x", True)])
    d = diff_runs(prev, curr)
    assert d["regressions"] == ["a"]
    assert d["improvements"] == ["b"]


def test_render_returns_string_with_totals():
    run = _run([_c("a", "vip", True), _c("b", "vip", False)])
    out = render(run)
    assert "vip" in out
    assert "1/2" in out
```

- [ ] **Step 2: Esegui i test (devono fallire)**

Run: `pytest tests/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.report'`

- [ ] **Step 3: Implementa report.py**

Create `eval/report.py`:
```python
"""Report dei risultati eval: tabella per categoria + diff vs run precedente."""
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"


def load_results(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def summarize(run: dict) -> dict[str, tuple[int, int]]:
    out: dict[str, list[int]] = {}
    for c in run["cases"]:
        agg = out.setdefault(c["category"], [0, 0])
        agg[1] += 1
        if c["passed"]:
            agg[0] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def diff_runs(prev: dict, curr: dict) -> dict:
    prev_pass = {c["id"]: c["passed"] for c in prev["cases"]}
    regressions, improvements = [], []
    for c in curr["cases"]:
        was = prev_pass.get(c["id"])
        if was is None:
            continue
        if was and not c["passed"]:
            regressions.append(c["id"])
        elif not was and c["passed"]:
            improvements.append(c["id"])
    return {"regressions": regressions, "improvements": improvements}


def render(run: dict, prev: dict | None = None) -> str:
    lines = [f"Run {run['timestamp']} — modello {run['model']}", ""]
    total_p = total_t = 0
    for cat, (p, t) in sorted(summarize(run).items()):
        lines.append(f"  {cat:20} {p}/{t}")
        total_p += p
        total_t += t
    lines.append(f"  {'TOTALE':20} {total_p}/{total_t}")
    fails = [c for c in run["cases"] if not c["passed"]]
    if fails:
        lines += ["", "FALLITI:"]
        for c in fails:
            why = ", ".join(c["assertion_failures"]) or (
                ", ".join((c["judge"] or {}).get("violated", [])) if c["judge"] else "?")
            lines.append(f"  [{c['category']}] {c['id']}: {why}")
    if prev:
        d = diff_runs(prev, run)
        lines += ["", f"vs run precedente — regressioni: {d['regressions'] or 'nessuna'} | "
                      f"miglioramenti: {d['improvements'] or 'nessuno'}"]
    return "\n".join(lines)


def _latest_two() -> tuple[Path | None, Path | None]:
    files = sorted(RESULTS_DIR.glob("*.json"))
    if not files:
        return None, None
    if len(files) == 1:
        return files[-1], None
    return files[-1], files[-2]


def main() -> int:
    want_diff = "--diff" in sys.argv
    latest, prev = _latest_two()
    if latest is None:
        print("Nessun risultato in", RESULTS_DIR)
        return 1
    curr = load_results(latest)
    prev_run = load_results(prev) if (want_diff and prev) else None
    print(render(curr, prev_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Esegui i test (devono passare)**

Run: `pytest tests/test_report.py -v`
Expected: 3 passed

- [ ] **Step 5: Verifica l'intera suite**

Run: `pytest -v`
Expected: tutti i test passano (assertions, loader, judge, run, report).

- [ ] **Step 6: Commit**

```bash
git add eval/report.py tests/test_report.py
git commit -m "feat(eval): report con tabella per categoria e diff vs run precedente"
```

---

### Task 7: Casi di test — i 4 failure mode + regressione

**Files:**
- Create: `eval/cases/system_exposure.yaml`, `vip_tables.yaml`, `hours.yaml`, `date_logic.yaml`, `regression.yaml`

I `rag_context` qui sotto sono snapshot rappresentativi nel formato prodotto da
`build_rag_context` (vedi `rag/context_builder.py`). Sono volutamente minimali e
sufficienti a testare la regola del prompt; potranno essere raffinati con snapshot
reali in seguito.

- [ ] **Step 1: system_exposure.yaml**

Create `eval/cases/system_exposure.yaml`:
```yaml
- id: sysexp-storico
  category: system_exposure
  venue: gate_milano
  history:
    - {role: user, content: "Ciao"}
    - {role: assistant, content: "Ciao! Come posso aiutarti?"}
  user_message: "Sono il ragazzo che ti ha chiamato Pietro poco fa"
  rag_context: ""
  rubric:
    must_not:
      - "Non deve dichiarare di non avere storico/memoria delle conversazioni come fosse un limite tecnico (es. 'ogni conversazione riparte da zero')"
    must:
      - "Deve restare naturale e chiedere come puo aiutare"

- id: sysexp-email
  category: system_exposure
  venue: gate_milano
  user_message: "Vi ho mandato una mail per il rimborso e non rispondete, potete controllare?"
  rag_context: ""
  rubric:
    must_not:
      - "Non deve dire di non avere accesso alle email o di non poter vedere le email ricevute"
    must:
      - "Deve indirizzare a info@gatemilano.com per il rimborso senza esporre limiti di sistema"
  assertions:
    forbidden_substrings: ["non ho accesso", "non posso vedere le email", "non ho modo di vedere"]

- id: sysexp-database-calendario
  category: system_exposure
  venue: gate_milano
  user_message: "che eventi ci sono il 30 giugno?"
  rag_context: "EVENTI IN PROGRAMMA (prossimi 14 giorni):\n(nessun evento nel periodo)"
  rubric:
    must_not:
      - "Non deve usare le parole database, sistema, calendario o 'nel mio database' per dire che non ha l'informazione"
    must:
      - "Deve dire in modo naturale che non ci sono serate in programma quel giorno"
  assertions:
    forbidden_substrings: ["database", "nel mio sistema", "nel mio calendario", "nel nostro sistema"]

- id: sysexp-immagine
  category: system_exposure
  venue: gate_milano
  history:
    - {role: user, content: "che eta serve per entrare?"}
    - {role: assistant, content: "Per le serate club il minimo e 18 anni. Di che evento parli?"}
  user_message: "questa [foto dell'evento]"
  rag_context: ""
  rubric:
    must_not:
      - "Non deve dire 'non riesco a vedere immagini/allegati' esponendo un limite tecnico"
    must:
      - "Deve chiedere il nome dell'evento in modo naturale"
```

- [ ] **Step 2: vip_tables.yaml**

Create `eval/cases/vip_tables.yaml` (verità di riferimento: `rag/prices.py`):
```yaml
- id: vip-f5-premium
  category: vip_tables
  venue: gate_milano
  history:
    - {role: user, content: "tavolo per sabato a Perreo, siamo in 3"}
    - {role: assistant, content: "Perfetto, per 3 va bene un tavolo. Che zona preferisci?"}
  user_message: "F5"
  rag_context: |
    PREZZI TAVOLI (fissi):
      VIP Face standard (F6-F21): minimo 300 per 8 persone, 35 per persona extra
      VIP Face premium (F1-F5): minimo 600 per 10 persone, 50 per persona extra
      VIP Balcony (B1-B5): minimo 300 per 8 persone, 35 per persona extra
      Console (C1-C3): minimo 500 per 10 persone, 50 per persona extra
  rubric:
    must:
      - "Deve indicare che F5 e premium con minimo 600 euro (NON 300)"
    must_not:
      - "Non deve dire che F5 costa 300 euro"

- id: vip-no-minimo-persone
  category: vip_tables
  venue: gate_milano
  user_message: "siamo in 2 ragazze, possiamo avere un tavolo a Perreo?"
  rag_context: |
    PREZZI TAVOLI (fissi):
      VIP Face standard (F6-F21): minimo 300 per 8 persone, 35 per persona extra
      VIP Balcony (B1-B5): minimo 300 per 8 persone, 35 per persona extra
  rubric:
    must_not:
      - "Non deve dire che esiste un numero minimo di persone per avere un tavolo (es. 'minimo 8 persone')"
    must:
      - "Puo spiegare che il minimo e di spesa (300 euro) indipendente dal numero di persone"

- id: vip-extra-alla-porta
  category: vip_tables
  venue: gate_milano
  user_message: "siamo in 10 per un tavolo Balcony a Perreo, quanto paghiamo?"
  rag_context: |
    PREZZI TAVOLI (fissi):
      VIP Balcony (B1-B5): minimo 300 per 8 persone, 35 per persona extra
  rubric:
    must:
      - "Deve dire che il minimo online resta 300 e le 2 persone extra pagano 35 ciascuna alla porta"
    must_not:
      - "Non deve sommare gli extra nel pagamento online ne inventare un minimo diverso da 300"

- id: vip-no-telefono
  category: vip_tables
  venue: gate_milano
  user_message: "come prenoto un tavolo per Perreo sabato?"
  rag_context: |
    PREZZI TAVOLI (fissi):
      VIP Face standard (F6-F21): minimo 300 per 8 persone, 35 per persona extra
  rubric:
    must_not:
      - "Non deve fornire numeri di telefono per prenotare"
  assertions:
    forbidden_substrings: ["+39 391 487 6443", "chiama", "telefono"]
```

- [ ] **Step 3: hours.yaml**

Create `eval/cases/hours.yaml`:
```yaml
- id: hours-no-speculazione
  category: hours
  venue: gate_milano
  user_message: "a che ora inizia THE URS CONCERT? da che ora si entra?"
  rag_context: |
    EVENTO: THE URS CONCERT — Main Room
    Biglietti: https://xceed.me/en/milano/event/the-urs-concert/227201/channel/gate-milano-website
    (orario di inizio non specificato nel contesto)
  rubric:
    must_not:
      - "Non deve inventare o supporre orari (es. 'probabilmente apertura porte alle 17')"
    must:
      - "Per i concerti deve dire di controllare l'evento su gatemilano.it o Xceed per l'orario esatto"

- id: hours-weekend-certo
  category: hours
  venue: gate_milano
  user_message: "fino a che ora siete aperti venerdi?"
  rag_context: ""
  rubric:
    must:
      - "Deve rispondere con certezza 23:00 - 05:00 per venerdi/sabato"

- id: hours-ingresso-vip-non-inventato
  category: hours
  venue: gate_milano
  user_message: "c'e un orario limite per entrare con il tavolo VIP?"
  rag_context: |
    PREZZI TAVOLI (fissi):
      VIP Face standard (F6-F21): minimo 300 per 8 persone, 35 per persona extra
  rubric:
    must_not:
      - "Non deve inventare un orario limite di ingresso VIP (es. 'entro le 03:30')"
```

- [ ] **Step 4: date_logic.yaml**

Create `eval/cases/date_logic.yaml`:
```yaml
- id: date-fuori-stagione
  category: date_logic
  venue: gate_milano
  user_message: "avete eventi il 12 luglio?"
  rag_context: "EVENTI IN PROGRAMMA (prossimi 14 giorni):\n(nessun evento nel periodo)"
  rubric:
    must:
      - "Deve dire in modo naturale che non ci sono eventi in quella data"
    must_not:
      - "Non deve esporre il concetto di 'sistema/database/calendario' come limite"

- id: date-evento-esterno
  category: date_logic
  venue: gate_milano
  user_message: "i biglietti per il concerto di Westside Gunn del 30 maggio sono in cassa?"
  rag_context: |
    EVENTI 30 maggio: PERREO XL (Main Room), HEAD STUDIOS x MERITOCRACY RECORDS (Club Room)
  rubric:
    must:
      - "Deve dire che non risulta un evento Westside Gunn e suggerire di controllare gatemilano.it o Instagram @gatemilano"
    must_not:
      - "Non deve confermare che i biglietti sono in cassa per un evento che non ha nel contesto"

- id: date-multipli-ordine
  category: date_logic
  venue: gate_milano
  user_message: "cosa c'e stasera?"
  rag_context: |
    EVENTI stasera:
      Club Room: Schranz Movement Milano (hard techno)
      Main Room: PERREO XL (reggaeton)
  rubric:
    must:
      - "Deve elencare ENTRAMBI gli eventi"
      - "Deve citare prima Main Room (PERREO XL) e poi Club Room (Schranz), mai Club Room per prima"
```

- [ ] **Step 5: regression.yaml**

Create `eval/cases/regression.yaml`:
```yaml
- id: reg-no-markdown
  category: regression
  venue: gate_milano
  user_message: "quali tavoli avete per Perreo?"
  rag_context: |
    PREZZI TAVOLI (fissi):
      VIP Face standard (F6-F21): minimo 300 per 8 persone, 35 per persona extra
      VIP Balcony (B1-B5): minimo 300 per 8 persone, 35 per persona extra
  rubric:
    must:
      - "Deve rispondere in testo semplice adatto a WhatsApp"
  assertions:
    forbidden_markdown: true

- id: reg-no-telefono-generico
  category: regression
  venue: gate_milano
  user_message: "ho un reclamo, a chi mi rivolgo?"
  rag_context: ""
  rubric:
    must:
      - "Deve indirizzare a info@gatemilano.com"
  assertions:
    forbidden_substrings: ["+39 391 487 6443"]

- id: reg-venue-esterna
  category: regression
  venue: gate_milano
  user_message: "info accessibilita per il concerto di Carl Cox al Carroponte?"
  rag_context: ""
  rubric:
    must:
      - "Deve chiarire che non gestisce il Carroponte e indirizzare alla venue corretta"
    must_not:
      - "Non deve inventare informazioni sull'evento al Carroponte"
```

- [ ] **Step 6: Verifica che i casi si carichino**

Run: `python -c "from eval.loader import load_cases; from pathlib import Path; cs=load_cases(Path('eval/cases')); print(len(cs), 'casi'); print(sorted({c.category for c in cs}))"`
Expected: `16 casi` e le 5 categorie.

- [ ] **Step 7: Commit**

```bash
git add eval/cases/
git commit -m "feat(eval): casi di test per i 4 failure mode + regressione"
```

---

### Task 8: Baseline run

**Files:** nessuno nuovo (esecuzione)

- [ ] **Step 1: Verifica che la API key sia disponibile**

Run: `python -c "from config import settings; print('key set:', bool(settings.anthropic_api_key))"`
Expected: `key set: True` (richiede `.env` con `ANTHROPIC_API_KEY`).

Se la chiave non c'e in locale, recuperala dalle env di Railway o chiedi all'utente
prima di procedere.

- [ ] **Step 2: Esegui il baseline**

Run: `python -m eval.run`
Expected: `Risultati: N/16 pass — salvati in eval/results/<timestamp>.json`

- [ ] **Step 3: Genera il report**

Run: `python -m eval.report`
Expected: tabella per categoria con i totali e l'elenco dei falliti.

- [ ] **Step 4: Verifica il prompt caching del judge**

Run: `python -c "import json,glob; f=sorted(glob.glob('eval/results/*.json'))[-1]; d=json.load(open(f)); print('file:', f)"`
Ispeziona almeno un caso giudicato: nei risultati grezzi il judge deve aver
prodotto un verdetto. (Il `cache_read_input_tokens` cresce dalla seconda chiamata
in poi nella stessa run.)

- [ ] **Step 5: Annota il baseline**

Aggiorna la memoria di progetto `project_chatbot_eval_harness.md` con il numero di
casi che il prompt attuale fallisce e quali categorie sono piu deboli. Questo e il
punto di partenza per la fase 2 (modifiche al prompt).

- [ ] **Step 6: Nessun commit di codice** — i risultati sono gitignored. Eventuale
commit solo se sono stati corretti casi durante il baseline.

---

## Self-Review (compilata)

**Spec coverage:**
- Test set YAML + contesto congelato → Task 7 (16 casi, 5 categorie). ✓
- Runner che chiama `generate_response` reale → Task 5. ✓
- Asserzioni deterministiche pre-judge → Task 2 + Task 5 (skip judge su fail). ✓
- LLM-judge su Sonnet con rubrica → Task 4. ✓
- Prompt caching nel judge → Task 4 (`build_judge_system`, cache_control). ✓
- Caching produzione rinviato a fase 2 → nessun task tocca `generate_response`. ✓
- Report con diff vs run precedente → Task 6. ✓
- ≥3 casi per failure mode → system_exposure(4), vip(4), hours(3), date(3), regression(3). ✓
- Baseline eseguito → Task 8. ✓
- `requirements.txt` ripristinata + `pyyaml` → Task 0. ✓

**Placeholder scan:** nessun TBD/TODO; ogni step di codice contiene il codice completo.

**Type consistency:** `Case`, `CaseResult`, `JudgeVerdict`, `Rubric`, `Assertions`
usati con gli stessi campi tra schema/loader/judge/run/report. `generate_fn` firma
`(venue, user_message, rag_context, history)` coerente con `generate_response` reale.
`judge_fn` firma `(case, reply)` coerente tra run.py e i test.
```
