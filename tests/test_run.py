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


@pytest.mark.asyncio
async def test_run_case_marks_bot_fallback_as_error():
    judge_calls = []

    async def gen(venue, user_message, rag_context, history):
        return "Mi dispiace, al momento non riesco a rispondere. Per assistenza contatta info@gatemilano.com."

    async def judge(case, reply):
        judge_calls.append(case.id)
        return JudgeVerdict("pass")

    case = _case()
    res = await run_case(case, generate_fn=gen, judge_fn=judge)
    assert res.error is not None
    assert res.passed is False
    assert judge_calls == []  # ne' assertion ne' judge sull'errore infra
