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
