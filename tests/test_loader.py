import textwrap
from datetime import date
import pytest
from eval.loader import load_cases, CaseValidationError
from eval.date_tokens import resolve_tokens


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


def test_resolve_tokens_basic():
    today = date(2026, 6, 14)
    assert resolve_tokens("{{TODAY}}", today) == "14 giugno 2026"
    assert resolve_tokens("evento il {{TODAY+7}}", today) == "evento il 21 giugno 2026"
    assert resolve_tokens("scaduto il {{TODAY-10}}", today) == "scaduto il 4 giugno 2026"
    assert resolve_tokens("nessun token", today) == "nessun token"


def test_resolve_tokens_crosses_month():
    today = date(2026, 6, 25)
    assert resolve_tokens("{{TODAY+10}}", today) == "5 luglio 2026"


def test_load_cases_resolves_date_tokens(tmp_path):
    today = date(2026, 6, 14)
    _write(tmp_path, "a.yaml", """
        - id: c1
          category: date_logic
          venue: gate_milano
          user_message: "tavolo per il {{TODAY+14}}?"
          rag_context: "EVENTO: LILCR — {{TODAY+14}} — Main Room"
          history:
            - {role: user, content: "evento del {{TODAY-2}}"}
    """)
    c = load_cases(tmp_path, today=today)[0]
    assert c.user_message == "tavolo per il 28 giugno 2026?"
    assert "28 giugno 2026" in c.rag_context
    assert c.history[0]["content"] == "evento del 12 giugno 2026"
