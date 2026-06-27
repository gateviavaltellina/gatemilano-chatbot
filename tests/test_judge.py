import pytest
from eval.schema import Case, Rubric
from eval.judge import parse_verdict, judge_reply, build_judge_system, JudgeTruncated
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
async def test_judge_reply_raises_on_truncation(fake_judge_client):
    # stop_reason=max_tokens => il tool_use e' troncato e l'input incompleto:
    # NON deve diventare un 'fail' silenzioso, ma un errore infra (escluso dal punteggio).
    client = fake_judge_client(verdict="fail", stop_reason="max_tokens")
    case = Case(id="c", category="x", venue="gate_milano",
                user_message="ciao", rubric=Rubric(must=["x"]))
    with pytest.raises(JudgeTruncated):
        await judge_reply(case, "una risposta", client=client, model="claude-sonnet-4-6")


@pytest.mark.asyncio
async def test_judge_reply_requests_enough_tokens(fake_judge_client):
    # il reasoning-first ha bisogno di spazio: max_tokens troppo basso troncava il verdetto.
    client = fake_judge_client(verdict="pass")
    case = Case(id="c", category="x", venue="gate_milano",
                user_message="ciao", rubric=Rubric(must_not=["x"]))
    await judge_reply(case, "ciao", client=client, model="claude-sonnet-4-6")
    assert client.messages.last_kwargs["max_tokens"] >= 1500


@pytest.mark.asyncio
async def test_judge_reply_passes_rubric_and_uses_tool(fake_judge_client):
    client = fake_judge_client(verdict="pass")
    case = Case(id="c", category="x", venue="gate_milano",
                user_message="ciao", rubric=Rubric(must_not=["niente database"]))
    v = await judge_reply(case, "ciao come posso aiutarti?", client=client, model="claude-sonnet-4-6")
    assert v.verdict == "pass"
    kwargs = client.messages.last_kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "record_verdict"}
    assert "niente database" in str(kwargs["messages"])
