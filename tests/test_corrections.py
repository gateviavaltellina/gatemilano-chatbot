import rag.corrections as cm


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.persist_dir", str(tmp_path))
    cm.reset()
    return cm


def test_add_and_get_rules_text(monkeypatch, tmp_path):
    c = _fresh(monkeypatch, tmp_path)
    cid = c.add_correction("gate_milano", "manda sempre a marketing@", {"user_msg": "non ho i biglietti", "wrong_reply": "scrivi a info@"}, "George")
    assert len(cid) == 8
    text = c.get_rules_text("gate_milano")
    assert "CORREZIONI STAFF" in text
    assert "manda sempre a marketing@" in text
    # altro venue non vede la regola
    assert c.get_rules_text("gate_sardinia") == ""


def test_list_and_remove(monkeypatch, tmp_path):
    c = _fresh(monkeypatch, tmp_path)
    cid = c.add_correction("gate_milano", "regola A", {}, "George")
    c.add_correction("gate_sardinia", "regola B", {}, "George")
    assert len(c.list_corrections()) == 2          # tutte
    assert len(c.list_corrections("gate_milano")) == 1
    assert c.remove_correction(cid) is True
    assert c.remove_correction("inesistente") is False
    assert c.get_rules_text("gate_milano") == ""


def test_persistence_round_trip(monkeypatch, tmp_path):
    c = _fresh(monkeypatch, tmp_path)
    c.add_correction("gate_milano", "regola persistente", {}, "George")
    c.reset()  # simula riavvio: ricarica dal disco
    assert "regola persistente" in c.get_rules_text("gate_milano")


def test_in_memory_without_persist_dir(monkeypatch):
    monkeypatch.setattr("config.settings.persist_dir", "")
    cm.reset()
    cm.add_correction("gate_milano", "solo memoria", {}, "George")
    assert "solo memoria" in cm.get_rules_text("gate_milano")
    cm.reset()  # senza file, il reset perde tutto
    assert cm.get_rules_text("gate_milano") == ""


def test_correction_reaches_system_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.persist_dir", str(tmp_path))
    cm.reset()
    cm.add_correction("gate_milano", "REGOLA E2E: di' sempre ciao", {}, "George")
    from ai.claude_client import build_system_blocks
    blocks = build_system_blocks("gate_milano", "ctx", "lunedì 14 giugno 2026, 22:00")
    assert "REGOLA E2E: di' sempre ciao" in blocks[1]["text"]
    assert "REGOLA E2E" not in blocks[0]["text"]
