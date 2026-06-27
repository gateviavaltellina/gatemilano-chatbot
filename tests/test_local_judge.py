"""Giudizio eval su abbonamento Claude Code invece che via API: assemblaggio
repliche (generate via API) + verdetti (prodotti da Claude Code) nel formato
standard dei risultati, con la stessa logica di 'passed' di CaseResult."""
from eval.local_judge import assemble_results


def _replies(cases):
    return {"cases": cases}


def test_assertion_failure_is_fail_regardless_of_verdict():
    replies = _replies([{
        "id": "c1", "category": "x", "user_message": "u", "reply": "r",
        "assertion_failures": ["forbidden: Xceed"], "error": None,
        "rubric": {"must": ["deve fare X"], "must_not": []},
    }])
    out = assemble_results(replies, {"c1": {"verdict": "pass", "violated": [], "reasoning": "ok"}}, "claude-code")
    c = out["cases"][0]
    assert c["passed"] is False
    assert c["assertion_failures"] == ["forbidden: Xceed"]


def test_empty_rubric_passes_without_judge():
    replies = _replies([{
        "id": "c2", "category": "x", "user_message": "u", "reply": "r",
        "assertion_failures": [], "error": None,
        "rubric": {"must": [], "must_not": []},
    }])
    out = assemble_results(replies, {}, "claude-code")
    c = out["cases"][0]
    assert c["passed"] is True
    assert c["judge"] is None


def test_judge_verdict_drives_pass_fail():
    replies = _replies([
        {"id": "p", "category": "x", "user_message": "u", "reply": "r",
         "assertion_failures": [], "error": None, "rubric": {"must": ["X"], "must_not": []}},
        {"id": "f", "category": "x", "user_message": "u", "reply": "r",
         "assertion_failures": [], "error": None, "rubric": {"must": ["Y"], "must_not": []}},
    ])
    verdicts = {
        "p": {"verdict": "pass", "violated": [], "reasoning": "ok"},
        "f": {"verdict": "fail", "violated": ["Y"], "reasoning": "manca Y"},
    }
    out = assemble_results(replies, verdicts, "claude-code")
    by = {c["id"]: c for c in out["cases"]}
    assert by["p"]["passed"] is True
    assert by["f"]["passed"] is False
    assert by["f"]["judge"]["violated"] == ["Y"]


def test_error_is_fail():
    replies = _replies([{
        "id": "e", "category": "x", "user_message": "u", "reply": "",
        "assertion_failures": [], "error": "API down", "rubric": {"must": ["X"], "must_not": []},
    }])
    out = assemble_results(replies, {}, "claude-code")
    assert out["cases"][0]["passed"] is False


def test_missing_verdict_for_rubric_case_is_fail():
    # rubrica non vuota ma verdetto mancante (giudice non l'ha valutato) → fail prudente
    replies = _replies([{
        "id": "m", "category": "x", "user_message": "u", "reply": "r",
        "assertion_failures": [], "error": None, "rubric": {"must": ["X"], "must_not": []},
    }])
    out = assemble_results(replies, {}, "claude-code")
    assert out["cases"][0]["passed"] is False
