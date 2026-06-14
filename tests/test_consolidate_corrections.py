import eval.consolidate_corrections as cc


def test_apply_edit_under_existing_heading():
    kb = "# Titolo\n\n## Biglietti\n- riga esistente\n\n## Altro\n- x\n"
    out = cc._apply_edit(kb, "## Biglietti", "nuova regola biglietti")
    lines = out.split("\n")
    i = lines.index("## Biglietti")
    assert lines[i + 1] == "- nuova regola biglietti"
    assert "- riga esistente" in out  # non rimuove l'esistente


def test_apply_edit_fallback_when_heading_missing():
    kb = "# Titolo\n\n## Biglietti\n- x\n"
    out = cc._apply_edit(kb, "## Inesistente", "regola orfana")
    assert cc._CONSOLIDATED_SECTION in out
    assert "- regola orfana" in out


def test_apply_edit_dedup_no_change_if_present():
    kb = "# Titolo\n\n## Biglietti\n- regola gia presente\n"
    out = cc._apply_edit(kb, "## Biglietti", "regola gia presente")
    assert out == kb


import config
from tests.conftest import FakeClient


async def test_propose_placement_returns_section_and_line():
    client = FakeClient({"section": "## Biglietti", "line": "manda a marketing@"})
    out = await cc.propose_placement("## Biglietti\n## Altro", "regola", client=client, model="x")
    assert out == {"section": "## Biglietti", "line": "manda a marketing@"}


async def test_propose_placement_none_on_empty_line():
    client = FakeClient({"section": "", "line": ""})
    out = await cc.propose_placement("## Biglietti", "regola", client=client, model="x")
    assert out is None


async def test_main_consolidates_and_prints_ids(monkeypatch, tmp_path, capsys):
    (tmp_path / "gate_milano.md").write_text("# KB\n\n## Biglietti\n- x\n", encoding="utf-8")
    monkeypatch.setattr(config, "KNOWLEDGE_DIR", tmp_path)
    monkeypatch.setattr(cc, "_fetch", lambda base, token: [
        {"id": "c1", "venue": "gate_milano", "rule": "manda a marketing@"},
    ])

    async def fake_place(kb, rule, *, client, model):
        return {"section": "## Biglietti", "line": "manda a marketing@"}

    monkeypatch.setattr(cc, "propose_placement", fake_place)
    rc = await cc.main(["http://x", "--token", "secret"])
    assert rc == 0
    kb = (tmp_path / "gate_milano.md").read_text(encoding="utf-8")
    assert "- manda a marketing@" in kb
    assert "c1" in capsys.readouterr().out
    # idempotente: la regola è ora nella KB → seconda esecuzione non la ri-aggiunge
    rc2 = await cc.main(["http://x", "--token", "secret"])
    kb2 = (tmp_path / "gate_milano.md").read_text(encoding="utf-8")
    assert kb2.count("- manda a marketing@") == 1
