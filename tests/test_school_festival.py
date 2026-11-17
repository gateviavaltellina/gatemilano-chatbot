"""Festival DM must leave the festival account, with no Gate data or credentials."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from config import settings
from instagram import client, token_store, token_refresh
import instagram.webhook as igw
import whatsapp.webhook as waw
from ai import claude_client
from rag.context_builder import build_rag_context
from notifications.token_health import _targets

VENUE = "milano_school_festival"
ACCOUNT = "123456789012345"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    # raising=False lets the tests expose missing feature behavior before Settings exists.
    monkeypatch.setitem(settings.__dict__, "ig_schoolfestival_id", ACCOUNT)
    monkeypatch.setitem(settings.__dict__, "ig_schoolfestival_token", "festival-token")
    monkeypatch.setattr(settings, "persist_dir", str(tmp_path))
    monkeypatch.setattr(settings, "meta_app_secret", "")
    monkeypatch.setattr(settings, "meta_app_secret_ig", "")
    monkeypatch.setattr(settings, "discord_webhook_url", "")
    monkeypatch.setattr(settings, "discord_ig_webhook_url", "")
    monkeypatch.setattr(token_store, "_tokens", {})
    monkeypatch.setattr(token_store, "_origin", {})
    monkeypatch.setattr(igw, "_ig_conversations", {})
    monkeypatch.setattr(igw, "_processed_ids", igw.OrderedDict())
    monkeypatch.setattr(waw, "_processed_ids", waw.OrderedDict())


@pytest.mark.parametrize("endpoint", ["/webhook/instagram", "/webhook"])
def test_dm_reply_uses_festival_account_and_token(endpoint, monkeypatch):
    import main
    captured = []
    prompts = []
    original = httpx.AsyncClient

    def transport(request):
        captured.append(request)
        return httpx.Response(200, json={"message_id": "sent"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        **kw, transport=httpx.MockTransport(transport)))

    async def model_create(**kwargs):
        prompts.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Ci vediamo all’Alcatraz!")],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1), stop_reason="end_turn")

    monkeypatch.setattr(claude_client._client.messages, "create", model_create)
    body = {"object": "instagram", "entry": [{"id": ACCOUNT, "messaging": [{
        "sender": {"id": "900001"}, "recipient": {"id": ACCOUNT},
        "message": {"mid": "school-dm", "text": "Dove si fa e quanto costa il VIP?"},
    }]}]}
    response = TestClient(main.app).post(endpoint, json=body)
    assert response.status_code == 200
    assert len(captured) == 1
    assert captured[0].url.path == f"/v22.0/{ACCOUNT}/messages"
    assert captured[0].headers["authorization"] == "Bearer festival-token"
    system = "\n".join(b["text"] for b in prompts[0]["system"])
    assert "MILANO SCHOOL FESTIVAL" in system
    assert "Alcatraz" in system
    assert "PERREO" not in system
    assert "info@gatemilano.com" not in system
    assert "drinklist_perreo" not in captured[0].content.decode()


def test_token_rotation_persists_and_health_checks_festival():
    token_store.load()
    token_store.set_token(VENUE, "festival-renewed")
    token_store._tokens.clear()
    token_store._origin.clear()
    token_store.load()
    assert client._token_for_account(ACCOUNT) == "festival-renewed"
    assert any(name == "Instagram @milano.school.festival" and token == "festival-renewed"
               for name, _, token in _targets())
    assert token_store.get("unknown") == ""


async def test_refresh_includes_festival(monkeypatch):
    async def refresh(token):
        return f"new-{token}"
    monkeypatch.setattr(token_refresh, "_refresh_one", refresh)
    result = await token_refresh.refresh_all()
    assert result[VENUE] is True
    assert client._token_for_account(ACCOUNT) == "new-festival-token"


async def test_context_never_queries_gate_events_or_tables(monkeypatch):
    import rag.context_builder as cb
    def forbidden(*args, **kwargs):
        pytest.fail("Festival must not query the Gate event store")
    monkeypatch.setattr(cb, "find_event_dates_by_name", forbidden)
    context, dates = await build_rag_context(VENUE, "tavoli Gate Milano domani?",
                                            [{"role": "user", "content": "Perreo Sardinia"}])
    assert "2026" in context
    assert dates == []


@pytest.mark.parametrize("endpoint", ["/webhook/instagram", "/webhook"])
@pytest.mark.parametrize("recipient,sender", [("unknown", "900001"), ("35517015101275600", ACCOUNT)])
def test_unknown_recipient_and_group_sender_ignored(endpoint, recipient, sender, monkeypatch):
    import main
    async def forbidden(*args, **kwargs):
        pytest.fail("Unknown account or group bot must not enter reply pipeline")
    monkeypatch.setattr(igw, "process_ig_message", forbidden)
    body = {"object": "instagram", "entry": [{"id": recipient, "messaging": [{
        "recipient": {"id": recipient}, "sender": {"id": sender},
        "message": {"mid": "ignore-dm", "text": "Ciao"},
    }]}]}
    assert TestClient(main.app).post(endpoint, json=body).status_code == 200


def test_festival_takeover_key_does_not_collide_with_gate():
    key = getattr(igw, "_staff_key", lambda account, sender: f"ig:{sender[:12]}")
    assert key(ACCOUNT, "900001") != key("35517015101275600", "900001")
    assert key(ACCOUNT, "9000010000001") != key(ACCOUNT, "9000010000002")


def test_disabled_account_never_matches_empty_id(monkeypatch):
    monkeypatch.setitem(settings.__dict__, "ig_schoolfestival_id", "")
    assert client._token_for_account("") == ""
    assert igw._venue_for_account("") is None


def test_conflicting_festival_id_preserves_gate_accounts(monkeypatch, caplog):
    monkeypatch.setitem(settings.__dict__, "ig_schoolfestival_id", settings.ig_gatemilano_id)
    monkeypatch.setattr(settings, "ig_gatemilano_token", "milano-token")
    assert igw._venue_for_account("24588954374135134") == "gate_sardinia"
    assert igw._venue_for_account(settings.ig_gatemilano_id) == "gate_milano"
    assert client._token_for_account(settings.ig_gatemilano_id) == "milano-token"
    assert "conflitto" in caplog.text.lower()


async def test_model_failure_does_not_direct_to_gate_email(monkeypatch):
    async def fail(**kwargs):
        raise RuntimeError("offline")
    monkeypatch.setattr(claude_client._client.messages, "create", fail)
    reply = await claude_client.generate_response(VENUE, "ciao", "", [])
    assert "gatemilano" not in reply
    assert "qui" in reply.lower()


def test_event_context_changes_after_event():
    from rag import school_festival
    rome = ZoneInfo("Europe/Rome")
    before = school_festival.build_context(datetime(2026, 11, 17, tzinfo=rome))
    during = school_festival.build_context(datetime(2026, 12, 23, 1, tzinfo=rome))
    after = school_festival.build_context(datetime(2026, 12, 24, tzinfo=rome))
    assert "futuro" in before
    assert "notte dell'evento" in during
    assert "trascorso" in after


async def test_story_image_does_not_inherit_gate_age_policy(monkeypatch):
    captured = []
    async def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Chiedi allo staff qui.")],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1), stop_reason="end_turn")
    monkeypatch.setattr(claude_client._client.messages, "create", create)
    await claude_client.generate_response(VENUE, "da che età?", "", [],
        image_block={"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "fake"}})
    note = captured[0]["messages"][-1]["content"][1]["text"]
    assert "16+" not in note
    assert "18+" not in note
    assert "storia" in note.lower()
