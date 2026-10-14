"""Risposte dei clienti a george@ nelle conversazioni del bot (caso reale 13/10: Aymen).

Il bot risponde da george@, quindi i clienti rispondono lì. Il bot segue SOLO le
conversazioni dove ha scritto lui; quelle di George restano sue.
"""
import pytest

from config import settings
from mail import followups
from mail import gmail_client as gm
from mail import refunds, responder

BOT, SEEN = "L_BOT", "L_SEEN"


def _msg(**kw):
    base = {"id": "c2", "thread_id": "t1", "from_email": "cliente@example.com",
            "from": "cliente@example.com", "to": "george@gatemilano.com", "cc": "",
            "delivered_to": ["george@gatemilano.com"], "subject": "Re: Rimborso",
            "body": "ci hanno detto che era pieno", "date_ms": 2000, "label_ids": [],
            "list_unsubscribe": "", "precedence": "", "auto_submitted": "",
            "has_attachments": False}
    base.update(kw)
    return base


@pytest.fixture
def g(monkeypatch):
    st = {"thread": [], "calls": []}
    followups._not_bot.clear()

    async def _get(mid):
        return _msg() if mid == "c2" else {"body": "testo della nostra risposta"}

    async def _thread(tid):
        return st["thread"]

    async def _ensure(name):
        return {gm.BOT_SENT_LABEL: BOT, refunds.LABEL_SEEN: SEEN}.get(name, "X")

    async def _label(*a):
        st["calls"].append(("label",) + a)

    async def _process_sender(sender, msgs):
        st["calls"].append(("rimborso", sender))
        return "ok"

    async def _process_one(mid, followup=False, prior=""):
        st["calls"].append(("info", mid, followup, prior))
        return "bozza"

    monkeypatch.setattr(gm, "get_message", _get)
    monkeypatch.setattr(gm, "thread_overview", _thread)
    monkeypatch.setattr(gm, "ensure_label", _ensure)
    monkeypatch.setattr(gm, "add_label", _label)
    monkeypatch.setattr(refunds, "process_sender", _process_sender)
    monkeypatch.setattr(responder, "process_one", _process_one)
    monkeypatch.setattr(settings, "refunds_enabled", True)
    monkeypatch.setattr(settings, "mail_autoresponder_enabled", True)
    return st


async def test_conversazione_di_george_non_si_tocca(g):
    g["thread"] = [{"id": "c1", "label_ids": ["INBOX"], "date_ms": 1000},
                   {"id": "s1", "label_ids": ["SENT"], "date_ms": 1500},
                   {"id": "c2", "label_ids": ["INBOX"], "date_ms": 2000}]
    assert await followups.handle("c2") == "non del bot"
    assert g["calls"] == []


async def test_risposta_in_un_rimborso_rientra_nella_verifica(g):
    g["thread"] = [{"id": "c1", "label_ids": [SEEN], "date_ms": 1000},
                   {"id": "b1", "label_ids": ["SENT", BOT], "date_ms": 1500},
                   {"id": "c2", "label_ids": ["INBOX"], "date_ms": 2000}]
    esito = await followups.handle("c2")
    assert esito == "rimborso: ok"
    assert ("rimborso", "cliente@example.com") in g["calls"]


async def test_risposta_a_info_diventa_bozza_col_contesto(g):
    g["thread"] = [{"id": "c1", "label_ids": ["INBOX"], "date_ms": 1000},
                   {"id": "b1", "label_ids": ["SENT", BOT], "date_ms": 1500},
                   {"id": "c2", "label_ids": ["INBOX"], "date_ms": 2000}]
    await followups.handle("c2")
    assert ("info", "c2", True, "testo della nostra risposta") in g["calls"]


async def test_se_lo_staff_ha_gia_risposto_si_ferma(g):
    g["thread"] = [{"id": "b1", "label_ids": ["SENT", BOT], "date_ms": 1500},
                   {"id": "c2", "label_ids": ["INBOX"], "date_ms": 2000},
                   {"id": "s2", "label_ids": ["SENT"], "date_ms": 2500}]   # George, a mano
    assert await followups.handle("c2") == "seguita dallo staff"
    assert g["calls"] == []


def test_query_esclude_mail_gia_gestite():
    q = followups._query()
    assert f"to:{settings.mail_reply_as}" in q and "-in:sent" in q
    assert f'-label:"{refunds.LABEL_SEEN}"' in q


def test_query_rimborsi_include_le_risposte_a_george():
    q = refunds._query_sender("cliente@example.com")
    assert f"deliveredto:{settings.refund_inbox}" in q and f"to:{settings.mail_reply_as}" in q


# --- il seguito su info@ non parte mai da solo ------------------------------------

async def test_seguito_info_sempre_in_bozza(monkeypatch):
    from mail import triage
    azioni = []
    m = _msg(subject="Re: Orari", body="ok grazie, e a che ora chiudete?")

    async def _get(_id):
        return m

    async def _verdict(_m):
        return {"categoria": "orari", "confidenza": "alta", "domanda": "orari"}

    async def _compose(_m, _v, prior=""):
        assert prior == "la nostra"
        return "Chiudiamo alle 5."

    async def _draft(*a, **k):
        azioni.append("draft")
        return "d1"

    async def _send(*a, **k):
        azioni.append("send")

    async def _boom(*a, **k):
        raise AssertionError("nel seguito non si controlla il thread")

    async def _nop(*a, **k):
        pass

    async def _yes(*a, **k):
        return True

    monkeypatch.setattr(settings, "mail_draft_only", False)
    monkeypatch.setattr(gm, "get_message", _get)
    monkeypatch.setattr(gm, "thread_has_our_reply", _boom)
    monkeypatch.setattr(gm, "can_send_as", _yes)
    monkeypatch.setattr(gm, "create_draft", _draft)
    monkeypatch.setattr(gm, "send_reply", _send)
    monkeypatch.setattr(gm, "add_label", _nop)
    monkeypatch.setattr(triage, "classify", _verdict)
    monkeypatch.setattr(responder, "_compose", _compose)
    monkeypatch.setattr(responder, "_notify", _nop)
    esito = await responder.process_one("c2", followup=True, prior="la nostra")
    assert azioni == ["draft"] and "rileggi" in esito
