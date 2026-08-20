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


async def test_handle_sync_ok(monkeypatch):
    # !sync forza il re-sync: verifichiamo che chiami i due sync e riporti i conteggi.
    import notifications.discord_bot as db
    import sync.sanity_sync as ss
    import sync.xceed_sync as xs
    calls = []

    async def _fake_sanity():
        calls.append("sanity")

    async def _fake_xceed():
        calls.append("xceed")

    monkeypatch.setattr(ss, "sync_all_venues", _fake_sanity)
    monkeypatch.setattr(xs, "sync_all_venues", _fake_xceed)
    out = await db.handle_sync()
    assert calls == ["sanity", "xceed"]
    assert "Sync completato" in out
    assert "Milano" in out and "Sardegna" in out


async def test_handle_sync_sanity_failure(monkeypatch):
    import notifications.discord_bot as db
    import sync.sanity_sync as ss

    async def _boom():
        raise RuntimeError("sanity down")

    monkeypatch.setattr(ss, "sync_all_venues", _boom)
    out = await db.handle_sync()
    assert "❌" in out and "Sanity" in out


# --- !stato: diagnosi token/canali per "il bot non risponde più" ---

async def test_handle_stato_reports_broken_token(monkeypatch):
    import notifications.token_health as th
    from notifications.discord_bot import handle_stato

    monkeypatch.setattr(th, "_targets", lambda: [
        ("Instagram @gatesardinia", "http://x/me", "tok1"),
        ("WhatsApp Cloud API", "http://y", "tok2"),
    ])

    async def _ok(url, token):
        if "x" in url:
            return False, "[190/460] session invalidated because the user changed their password"
        return True, "ok"
    monkeypatch.setattr(th, "_token_ok", _ok)

    out = await handle_stato()
    assert "🚨 Instagram @gatesardinia" in out
    assert "session invalidated" in out          # errore ESATTO di Meta, non dedotto
    assert "✅ WhatsApp Cloud API: token valido" in out
    assert "Eventi in memoria" in out


async def test_handle_stato_all_ok(monkeypatch):
    import notifications.token_health as th
    from notifications.discord_bot import handle_stato

    monkeypatch.setattr(th, "_targets", lambda: [("Instagram @gatemilano", "http://x/me", "t")])

    async def _ok(url, token):
        return True, "ok"
    monkeypatch.setattr(th, "_token_ok", _ok)

    out = await handle_stato()
    assert "✅ Instagram @gatemilano: token valido" in out
    assert "🚨" not in out


async def test_handle_stato_shows_inbound_trace(monkeypatch):
    import notifications.token_health as th
    import notifications.debug_trace as dt
    from notifications.discord_bot import handle_stato

    monkeypatch.setattr(th, "_targets", lambda: [])
    dt._events.clear()
    dt.record("ig", "1234567890", "ciao, info?", "webhook in ingresso")
    dt.record("ig", "24588954374135134", "test da account gruppo",
              "scartato: mittente è un account del gruppo (anti-loop)")

    out = await handle_stato()
    assert "Ultimi messaggi in ingresso" in out
    assert "webhook in ingresso" in out
    assert "account del gruppo" in out       # lo scarto anti-loop è visibile
    assert "567890" in out                   # coda id mittente
    dt._events.clear()


async def test_ig_bot_sender_dropped_but_traced(monkeypatch):
    # DM da un account del gruppo (es. @gatemilano → @gatesardinia): niente
    # risposta (anti-loop), ma l'evento resta tracciato per la diagnosi !stato.
    from fastapi.testclient import TestClient
    import main
    import instagram.webhook as igw
    import notifications.debug_trace as dt

    dt._events.clear()
    called = []

    async def _spy(*a, **k):
        called.append(a)
    monkeypatch.setattr(igw, "process_ig_message", _spy)

    body = {
        "object": "instagram",
        "entry": [{
            "id": "17841452139166980",
            "messaging": [{
                "sender": {"id": "17841405933946552"},   # account Milano del gruppo
                "recipient": {"id": "17841452139166980"},
                "message": {"mid": "grp-1", "text": "test interno"},
            }],
        }],
    }
    r = TestClient(main.app).post("/webhook/instagram", json=body)
    assert r.status_code == 200
    assert called == []                                  # nessuna risposta
    stages = [e["stage"] for e in dt.recent()]
    assert any("account del gruppo" in s for s in stages)
    dt._events.clear()
