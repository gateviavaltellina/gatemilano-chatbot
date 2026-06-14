import yaml
import eval.import_correction_cases as imp


def test_merge_dedups_by_id():
    existing = [{"id": "corr-a", "rule": "x"}]
    incoming = [{"id": "corr-a", "rule": "x"}, {"id": "corr-b", "rule": "y"}]
    merged, added = imp._merge(existing, incoming)
    assert added == 1
    assert [c["id"] for c in merged] == ["corr-a", "corr-b"]


def test_valid_skips_missing_required_fields():
    good, skipped = imp._valid([
        {"id": "corr-ok", "category": "corrections", "venue": "gate_milano", "user_message": "u"},
        {"id": "corr-bad", "category": "corrections", "venue": "gate_milano", "user_message": ""},
        {"category": "corrections", "venue": "gate_milano", "user_message": "u"},
    ])
    assert skipped == 2
    assert [c["id"] for c in good] == ["corr-ok"]


def test_main_skips_invalid_case(monkeypatch, tmp_path):
    target = tmp_path / "corrections.yaml"
    monkeypatch.setattr(imp, "CASES_FILE", target)
    monkeypatch.setattr(imp, "_fetch", lambda base, token: [
        {"id": "corr-ok", "category": "corrections", "venue": "gate_milano", "user_message": "u"},
        {"id": "corr-bad", "category": "corrections", "venue": "gate_milano", "user_message": ""},
    ])
    imp.main(["http://bot.example", "--token", "secret"])
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert [c["id"] for c in data] == ["corr-ok"]


def test_main_writes_yaml(monkeypatch, tmp_path):
    target = tmp_path / "corrections.yaml"
    monkeypatch.setattr(imp, "CASES_FILE", target)
    monkeypatch.setattr(imp, "_fetch", lambda base, token: [
        {"id": "corr-1", "category": "corrections", "venue": "gate_milano",
         "user_message": "u", "rubric": {"must": ["x"], "must_not": []}},
    ])
    rc = imp.main(["http://bot.example", "--token", "secret"])
    assert rc == 0
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data[0]["id"] == "corr-1"
    # idempotente: seconda esecuzione non duplica
    imp.main(["http://bot.example", "--token", "secret"])
    data2 = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert len(data2) == 1
