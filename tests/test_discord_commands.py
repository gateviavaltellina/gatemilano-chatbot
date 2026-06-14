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
    client = FakeClient({"user_message": "u", "must": [], "must_not": []})
    out = await handle_regola("una regola", ctx, "George", client=client, model="x")
    assert "✅" in out
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
    assert handle_correction_command("approva", "nope", None, "George").startswith("❌")
    assert handle_correction_command("approva", "", None, "George").startswith("❌")
