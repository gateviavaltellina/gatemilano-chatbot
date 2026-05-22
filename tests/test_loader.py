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
