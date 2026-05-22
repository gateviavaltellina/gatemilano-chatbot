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
