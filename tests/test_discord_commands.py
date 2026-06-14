import rag.corrections as cm
from notifications.discord_bot import parse_correction_command, handle_correction_command


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.persist_dir", str(tmp_path))
    cm.reset()


def test_parse_commands():
    assert parse_correction_command("!regola manda a marketing@") == ("regola", "manda a marketing@")
    assert parse_correction_command("!regole") == ("regole", "")
    assert parse_correction_command("!rimuovi abc123") == ("rimuovi", "abc123")
    assert parse_correction_command("ciao") == (None, "")
    # niente collisione coi comandi takeover esistenti
    assert parse_correction_command("!r ciao")[0] is None
    assert parse_correction_command("!rel")[0] is None


def test_handle_regola_adds_correction(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    ctx = {"venue": "gate_milano", "user_msg": "non ho i biglietti", "bot_reply": "scrivi a info@"}
    out = handle_correction_command("regola", "manda sempre a marketing@", ctx, "George")
    assert "✅" in out
    assert "manda sempre a marketing@" in cm.get_rules_text("gate_milano")


def test_handle_regola_without_context_errors(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    # ctx None, ctx vuoto e ctx senza venue devono tutti dare errore
    assert handle_correction_command("regola", "qualcosa", None, "George").startswith("❌")
    assert handle_correction_command("regola", "qualcosa", {}, "George").startswith("❌")
    assert handle_correction_command("regola", "qualcosa", {"user_msg": "x"}, "George").startswith("❌")


def test_handle_regola_empty_payload_errors(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    ctx = {"venue": "gate_milano", "user_msg": "u", "bot_reply": "r"}
    assert handle_correction_command("regola", "", ctx, "George").startswith("❌")


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
