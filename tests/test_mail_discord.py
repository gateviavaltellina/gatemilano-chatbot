"""Email su Discord come le chat IG/WA: lo staff vede e invia senza aprire Gmail.

Le protezioni contano più della comodità: un `!ok` o un `!r` non devono mai far
partire due risposte allo stesso cliente.
"""
import httpx
import pytest

from config import settings
from mail import gmail_client as gm
from mail import staff
from notifications import discord as nd


def _ctx(**kw):
    base = {
        "channel": "email", "venue": "gate_milano", "draft_id": "d1", "gmail_id": "m1",
        "sent_by": "", "user_msg": "orari?", "bot_reply": "Apriamo alle 23.",
        "mail": {"from_email": "sofia@example.com", "subject": "Orari",
                 "message_id_header": "<a@b>", "references": "", "thread_id": "t1"},
    }
    base.update(kw)
    return base


@pytest.fixture
def gmail(monkeypatch):
    calls = []

    async def _no_reply(_tid):
        return False

    async def _send_draft(did):
        calls.append(("send_draft", did))

    async def _send_reply(mail, text):
        calls.append(("send_reply", mail["from_email"], text))

    async def _delete_draft(did):
        calls.append(("delete_draft", did))

    async def _label(mid, name):
        calls.append(("label", mid, name))

    monkeypatch.setattr(gm, "configured", lambda: True)
    monkeypatch.setattr(gm, "thread_has_our_reply", _no_reply)
    monkeypatch.setattr(gm, "send_draft", _send_draft)
    monkeypatch.setattr(gm, "send_reply", _send_reply)
    monkeypatch.setattr(gm, "delete_draft", _delete_draft)
    monkeypatch.setattr(gm, "add_label", _label)
    return calls


async def test_ok_invia_la_bozza(gmail):
    ctx = _ctx()
    out = await staff.staff_action("!ok", ctx, "George")
    assert ("send_draft", "d1") in gmail
    assert ("label", "m1", "Bot/Risposto") in gmail
    assert ctx["sent_by"] == "George"
    assert out.startswith("✅")


async def test_r_invia_testo_e_scarta_la_bozza(gmail):
    ctx = _ctx()
    await staff.staff_action("!r Ciao Sofia, apriamo alle 23:30.", ctx, "George")
    assert ("send_reply", "sofia@example.com", "Ciao Sofia, apriamo alle 23:30.") in gmail
    # La bozza del bot va tolta, o qualcuno potrebbe inviarla dopo da Gmail.
    assert ("delete_draft", "d1") in gmail


async def test_mai_due_risposte(gmail):
    ctx = _ctx()
    await staff.staff_action("!ok", ctx, "George")
    gmail.clear()
    out = await staff.staff_action("!r altro testo", ctx, "Marco")
    assert "Già risposto" in out
    assert not [c for c in gmail if c[0].startswith("send")]


async def test_risposta_gia_data_da_gmail_blocca(gmail, monkeypatch):
    async def _yes(_tid):
        return True
    monkeypatch.setattr(gm, "thread_has_our_reply", _yes)
    out = await staff.staff_action("!ok", _ctx(), "George")
    assert "già risposto" in out
    assert not [c for c in gmail if c[0].startswith("send")]


async def test_bozza_sparita_da_gmail(gmail, monkeypatch):
    async def _gone(_did):
        req = httpx.Request("POST", "https://x")
        raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))
    monkeypatch.setattr(gm, "send_draft", _gone)
    ctx = _ctx()
    out = await staff.staff_action("!ok", ctx, "George")
    assert "Bozza non trovata" in out
    assert ctx["sent_by"] == ""


async def test_ok_senza_bozza(gmail):
    out = await staff.staff_action("!ok", _ctx(draft_id=""), "George")
    assert "!r" in out


async def test_altri_comandi_non_sono_email(gmail):
    assert await staff.staff_action("!t", _ctx(), "George") is None
    assert await staff.staff_action("!r   ", _ctx(), "George") is None


async def test_contesto_perso(gmail):
    out = await staff.staff_action("!ok", {}, "George")
    assert "Gmail" in out


async def test_embed_con_domanda_risposta_e_comandi(monkeypatch):
    preso = {}

    async def _post(url, payload, key, context, attempts=2):
        preso.update(url=url, payload=payload, key=key, context=context)

    monkeypatch.setattr(nd, "_post_and_register", _post)
    monkeypatch.setattr(settings, "discord_mail_webhook_url", "https://discord.test/hook")
    msg = {"id": "m1", "thread_id": "t1", "from_email": "sofia@example.com",
           "subject": "Orari", "body": "A che ora aprite sabato?",
           "message_id_header": "<a@b>", "references": ""}
    await nd.notify_email(msg, {"categoria": "orari"}, "gate_milano",
                          "Apriamo alle 23.", "bozza", reason="solo-bozze", draft_id="d1")
    testo = str(preso["payload"])
    assert "A che ora aprite sabato?" in testo and "Apriamo alle 23." in testo
    assert "!ok" in testo and "!r" in testo
    assert preso["key"] == "mail:t1"
    assert preso["context"]["draft_id"] == "d1"
    assert preso["context"]["mail"]["from_email"] == "sofia@example.com"
    assert preso["url"].endswith("?wait=true")


async def test_inviata_in_automatico_segnata_come_gia_risposta(monkeypatch):
    preso = {}

    async def _post(url, payload, key, context, attempts=2):
        preso.update(context=context)

    monkeypatch.setattr(nd, "_post_and_register", _post)
    monkeypatch.setattr(settings, "discord_mail_webhook_url", "https://discord.test/hook")
    msg = {"id": "m1", "thread_id": "t1", "from_email": "a@b.c", "subject": "x", "body": "y"}
    await nd.notify_email(msg, {}, "gate_milano", "ok", "inviata")
    # Un !ok o !r su un'email già partita in automatico non deve fare un secondo invio.
    assert preso["context"]["sent_by"] == "bot"
