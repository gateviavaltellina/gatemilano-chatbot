"""Rimborsi automatici (rimborsi@): verifica della richiesta e azioni dello staff.

Policy staff 13/10: rimborso SOLO a chi, con biglietto valido, è stato respinto
all'ingresso; servono prova d'acquisto, biglietti e motivazione; entro il lunedì
successivo; importo pieno. La selezione avviene prima della scansione: un biglietto
usato vuol dire che il cliente è entrato.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from config import settings
from mail import gmail_client as gm
from mail import refunds, staff, templates, triage
from mail import xceed_tickets as xt

ROME = ZoneInfo("Europe/Rome")


def _a(**kw):
    base = {"respinto_alla_porta": True, "motivazione": "la sicurezza ci ha fermati senza motivo",
            "non_venuto": False, "prova_acquisto": True, "biglietti": True,
            "codici": ["24556189"], "nomi": ["Ibrahima Diouf"], "lingua": "it"}
    base.update(kw)
    return base


def _ticket(bid=24556189, checked=False, active=True, email="cliente@example.com",
            first="Ibrahima", last="Diouf", qr="QR1"):
    return {"qrCode": qr, "firstName": first, "lastName": last, "email": email,
            "hasCheckedIn": checked, "isActive": active,
            "offer": {"name": "Ingresso", "price": {"amount": "17.60", "currency": "EUR"}},
            "booking": {"bookingId": bid, "email": email, "firstName": first, "lastName": last}}


# Sabato 10/10/2026 alle 23:00 di Milano.
_EVENT = {"id": 1, "uuid": "u1", "name": "PERREO XL",
          "startingTime": int(datetime(2026, 10, 10, 23, tzinfo=ROME).timestamp())}


def _found(*tickets, match="codice"):
    return {"event": _EVENT, "tickets": list(tickets), "match": match}


# --- scadenza ----------------------------------------------------------------------

@pytest.mark.parametrize("day,deadline", [
    ("2026-10-10", "2026-10-12"),   # sabato → lunedì dopo
    ("2026-10-09", "2026-10-12"),   # venerdì → lunedì dopo
    ("2026-10-12", "2026-10-19"),   # lunedì → il lunedì SUCCESSIVO
])
def test_scadenza_lunedi_successivo(day, deadline):
    d = refunds.refund_deadline(day)
    assert d.strftime("%Y-%m-%d") == deadline and d.hour == 23


# --- cosa manca al cliente ------------------------------------------------------

def test_completa():
    assert refunds.missing_from_customer(_a()) == []


@pytest.mark.parametrize("kw,manca", [
    ({"prova_acquisto": False}, "prova_acquisto"),
    ({"biglietti": False}, "biglietti"),
    ({"motivazione": ""}, "motivazione"),
    # "Rimborso" e una foto non bastano: serve dire cosa è successo alla porta.
    ({"respinto_alla_porta": False, "motivazione": ""}, "motivazione"),
])
def test_cosa_manca(kw, manca):
    assert manca in refunds.missing_from_customer(_a(**kw))


# --- esito ---------------------------------------------------------------------------

_IN_TIME = datetime(2026, 10, 11, 1, 0, tzinfo=ROME)


def test_tutto_ok():
    v = refunds.assess(_a(), _found(_ticket()), _IN_TIME)
    assert v["status"] == "ok"


def test_non_venuto_per_motivi_propri():
    v = refunds.assess(_a(respinto_alla_porta=False, motivazione="", non_venuto=True),
                       {}, _IN_TIME)
    assert v["status"] == "non_rimborsabile"


def test_biglietto_usato_vuol_dire_entrato():
    v = refunds.assess(_a(), _found(_ticket(checked=True)), _IN_TIME)
    assert v["status"] == "da_verificare"
    assert any("USATI" in t for _, t in v["checks"])


def test_fuori_tempo():
    late = datetime(2026, 10, 14, 10, 0, tzinfo=ROME)
    v = refunds.assess(_a(), _found(_ticket()), late)
    assert v["status"] == "da_verificare"
    assert any("FUORI TEMPO" in t for _, t in v["checks"])


def test_non_trovato_su_xceed():
    v = refunds.assess(_a(), {}, _IN_TIME)
    assert v["status"] == "da_verificare"


def test_trovato_solo_per_nome_va_verificato():
    v = refunds.assess(_a(), _found(_ticket(), match="nome"), _IN_TIME)
    assert v["status"] == "da_verificare"


def test_gia_rimborsato_o_annullato():
    v = refunds.assess(_a(), _found(_ticket(active=False)), _IN_TIME)
    assert v["status"] == "da_verificare"


# --- ricerca biglietti ----------------------------------------------------------------

def test_match_per_codice_prende_tutta_la_prenotazione():
    tickets = [_ticket(qr="A"), _ticket(qr="B", first="Amico", last="Uno"), _ticket(bid=999, qr="C")]
    found, how = xt.match_tickets(tickets, set(), {"24556189"}, set())
    assert how == "codice" and len(found) == 2


def test_match_per_email():
    found, how = xt.match_tickets([_ticket()], {"Cliente@Example.com"}, set(), set())
    assert how == "email" and found


def test_match_per_nome_e_debole():
    found, how = xt.match_tickets([_ticket(email="altro@x.it")], {"no@x.it"}, set(), {"ibrahima diouf"})
    assert how == "nome"


def test_nessun_match():
    assert xt.match_tickets([_ticket()], {"no@x.it"}, {"1"}, {"mario rossi"}) == ([], "")


# --- testi fissi -------------------------------------------------------------------------

def test_messaggio_cosa_manca():
    t = templates.refund_missing("it", ["biglietti", "motivazione"])
    assert "QR code" in t and "cosa è successo all'ingresso" in t
    assert "prova d'acquisto" not in t


def test_messaggi_in_inglese():
    assert "proof of purchase" in templates.refund_missing("en", ["prova_acquisto"])
    assert "fees included" in templates.refund_done("en", "PERREO XL")


def test_rimborso_fatto_dice_importo_pieno():
    assert "commissioni comprese" in templates.refund_done("it", "PERREO XL")


# --- casella e risposte ------------------------------------------------------------------

def test_hard_skip_sulla_casella_rimborsi():
    msg = {"from_email": "c@x.it", "from": "c@x.it", "to": "Rimborsi@gatemilano.com",
           "delivered_to": ["rimborsi@gatemilano.com"], "body": "rimborso", "label_ids": []}
    assert triage.hard_skip(msg, box="rimborsi@gatemilano.com") == ""
    assert triage.hard_skip(msg) != ""   # per info@ non è sua


def test_reply_to_torna_a_rimborsi():
    import base64
    raw = gm._build_raw("c@x.it", "Rimborso", "ciao", "<a@b>", "", reply_to="rimborsi@gatemilano.com")
    assert "Reply-To: rimborsi@gatemilano.com" in base64.urlsafe_b64decode(raw).decode()


# --- flusso -------------------------------------------------------------------------------

def _msg(i="m1", body="rimborso", ms=None):
    return {"id": i, "thread_id": "t" + i, "from_email": "cliente@example.com",
            "from": "cliente@example.com", "subject": "Rimborso", "body": body,
            "message_id_header": f"<{i}@x>", "references": "", "attachments": [],
            "date_ms": ms or int(_IN_TIME.timestamp() * 1000), "label_ids": [],
            "to": "rimborsi@gatemilano.com", "delivered_to": ["rimborsi@gatemilano.com"]}


@pytest.fixture
def flow(monkeypatch):
    st = {"replies": [], "labels": [], "notes": [], "existing": set(), "staff": False}

    async def _list(q, n):
        for lab in list(st["existing"]):
            if f'label:"{lab}"' in q:
                return ["x"]
        return [] if "label:" in q.replace("-label:", "") else ["m1"]

    async def _get(i):
        return _msg(i)

    async def _staff(tid, lab):
        return st["staff"]

    async def _send(msg, text, label="", reply_to=""):
        st["replies"].append((text, label, reply_to))

    async def _label(mid, lab):
        st["labels"].append(lab)

    async def _notify(sender, msgs, a, verdict, found, note=""):
        st["notes"].append((verdict.get("status"), note))

    monkeypatch.setattr(gm, "list_unprocessed", _list)
    monkeypatch.setattr(gm, "get_message", _get)
    monkeypatch.setattr(gm, "thread_staff_replied", _staff)
    monkeypatch.setattr(gm, "send_reply", _send)
    monkeypatch.setattr(gm, "add_label", _label)
    monkeypatch.setattr(refunds, "_notify", _notify)
    return st


async def test_incompleta_scrive_cosa_manca(flow, monkeypatch):
    async def _an(msgs):
        return _a(biglietti=False)
    monkeypatch.setattr(refunds, "analyze", _an)
    esito = await refunds.process_sender("cliente@example.com", [_msg()])
    assert esito == "incompleta"
    text, label, reply_to = flow["replies"][0]
    assert "QR code" in text
    assert label == refunds.LABEL_BOT_SENT and reply_to == settings.refund_inbox


async def test_completa_conferma_ricezione_una_volta(flow, monkeypatch):
    async def _an(msgs):
        return _a()

    async def _find(*a, **k):
        return _found(_ticket())
    monkeypatch.setattr(refunds, "analyze", _an)
    monkeypatch.setattr(xt, "find_request_tickets", _find)
    assert await refunds.process_sender("cliente@example.com", [_msg()]) == "ok"
    assert "abbiamo ricevuto la tua richiesta di rimborso completa" in flow["replies"][0][0]
    # Secondo giro (sollecito): niente seconda conferma.
    flow["replies"].clear()
    flow["existing"].add(refunds.LABEL_ACK)
    await refunds.process_sender("cliente@example.com", [_msg("m2")])
    assert flow["replies"] == []


async def test_gia_rimborsata_solo_nota(flow, monkeypatch):
    flow["existing"].add(refunds.LABEL_DONE)

    async def _boom(msgs):
        raise AssertionError("non deve rianalizzare")
    monkeypatch.setattr(refunds, "analyze", _boom)
    esito = await refunds.process_sender("cliente@example.com", [_msg()])
    assert esito.startswith("nota") and flow["replies"] == []


async def test_staff_ha_risposto_a_mano_il_bot_si_ferma(flow, monkeypatch):
    flow["staff"] = True

    async def _boom(msgs):
        raise AssertionError("non deve rianalizzare")
    monkeypatch.setattr(refunds, "analyze", _boom)
    assert (await refunds.process_sender("cliente@example.com", [_msg()])) == "nota (staff)"
    assert flow["replies"] == []


async def test_spento_non_fa_nulla(monkeypatch):
    monkeypatch.setattr(settings, "refunds_enabled", False)
    assert await refunds.run_once() == {"skipped": "disattivato"}


async def test_rimborso_automatico_spento_di_default(flow, monkeypatch):
    assert settings.refund_auto_execute is False

    async def _an(msgs):
        return _a()

    async def _find(*a, **k):
        return _found(_ticket())
    monkeypatch.setattr(refunds, "analyze", _an)
    monkeypatch.setattr(xt, "find_request_tickets", _find)
    await refunds.process_sender("cliente@example.com", [_msg()])
    assert refunds.LABEL_DONE not in flow["labels"]


# --- comandi staff --------------------------------------------------------------------------

def _ctx(**kw):
    base = {"channel": "refund", "lingua": "it", "evento": "PERREO XL", "booking_ids": ["24556189"],
            "gmail_ids": ["m1", "m2"], "sent_by": "",
            "mail": {"from_email": "cliente@example.com", "subject": "Rimborso",
                     "message_id_header": "<a@b>", "references": "", "thread_id": "t1"}}
    base.update(kw)
    return base


@pytest.fixture
def staff_gmail(monkeypatch):
    calls = []

    async def _send(mail, text, label="", reply_to=""):
        calls.append(("send", text, reply_to))

    async def _label(mid, lab):
        calls.append(("label", mid, lab))

    monkeypatch.setattr(gm, "configured", lambda: True)
    monkeypatch.setattr(gm, "send_reply", _send)
    monkeypatch.setattr(gm, "add_label", _label)
    return calls


async def test_fatto_avvisa_il_cliente_e_chiude(staff_gmail):
    ctx = _ctx()
    out = await staff.refund_action("!fatto", ctx, "George")
    assert out.startswith("✅")
    assert any(c[0] == "send" and "è stato effettuato" in c[1] for c in staff_gmail)
    assert ("label", "m2", refunds.LABEL_DONE) in staff_gmail
    assert ctx["sent_by"] == "George"


async def test_mai_due_esiti(staff_gmail):
    ctx = _ctx()
    await staff.refund_action("!fatto", ctx, "George")
    staff_gmail.clear()
    out = await staff.refund_action("!rifiuta", ctx, "Marco")
    assert "già chiusa" in out and staff_gmail == []


async def test_rimborsa_senza_mcp_spiega_cosa_fare(staff_gmail):
    out = await staff.refund_action("!rimborsa", _ctx(), "George")
    assert "!fatto" in out and staff_gmail == []


async def test_rifiuta_con_nota(staff_gmail):
    await staff.refund_action("!rifiuta Il biglietto risulta usato all'ingresso.", _ctx(), "George")
    text = [c[1] for c in staff_gmail if c[0] == "send"][0]
    assert "non rientra nei casi rimborsabili" in text and "risulta usato" in text


async def test_altri_comandi_ignorati(staff_gmail):
    assert await staff.refund_action("!ok", _ctx(), "George") is None


# --- rimborso su Xceed (MCP booking_refund) -----------------------------------------------

async def test_rimborsa_chiama_booking_refund_e_avvisa(staff_gmail, monkeypatch):
    from mail import xceed_mcp
    chiamate = []

    async def _call(name, args):
        chiamate.append((name, args))
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(xceed_mcp, "configured", lambda: True)
    monkeypatch.setattr(xceed_mcp, "call_tool", _call)
    ctx = _ctx(booking_ids=["24556189"])
    out = await staff.refund_action("!rimborsa", ctx, "George")
    assert out.startswith("✅")
    name, args = chiamate[0]
    assert name == "booking_refund"
    assert args["legacyId"] == 24556189 and args["isFullRefund"] is True
    assert any(c[0] == "send" and "commissioni comprese" in c[1] for c in staff_gmail)


async def test_rimborso_rifiutato_da_xceed_non_avvisa_il_cliente(staff_gmail, monkeypatch):
    from mail import xceed_mcp

    async def _call(name, args):
        return {"isError": True, "content": [{"type": "text", "text": "already refunded"}]}

    monkeypatch.setattr(xceed_mcp, "configured", lambda: True)
    monkeypatch.setattr(xceed_mcp, "call_tool", _call)
    ctx = _ctx()
    out = await staff.refund_action("!rimborsa", ctx, "George")
    assert out.startswith("❌") and "already refunded" in out
    assert staff_gmail == [] and ctx["sent_by"] == ""


async def test_riprova_non_rimborsa_due_volte(staff_gmail, monkeypatch):
    """Due prenotazioni, la seconda fallisce: al nuovo !rimborsa la prima non si ripete."""
    from mail import xceed_mcp
    chiamate = []

    async def _call(name, args):
        chiamate.append(args["legacyId"])
        if args["legacyId"] == 2 and chiamate.count(2) == 1:
            return {"isError": True, "content": [{"type": "text", "text": "timeout"}]}
        return {"content": []}

    monkeypatch.setattr(xceed_mcp, "configured", lambda: True)
    monkeypatch.setattr(xceed_mcp, "call_tool", _call)
    ctx = _ctx(booking_ids=["1", "2"])
    await staff.refund_action("!rimborsa", ctx, "George")
    await staff.refund_action("!rimborsa", ctx, "George")
    assert chiamate == [1, 2, 2]


# --- ricerca via MCP (bookings_search) -----------------------------------------------------

@pytest.mark.parametrize("data", [
    {"data": [{"legacyId": 24698564, "buyer": {"email": "g@x.it", "firstName": "Geraldo"},
               "isRefunded": False, "hasCheckedIn": False, "offer": {"name": "Men General Admission"},
               "price": {"amount": "17.60", "currency": "EUR"}}]},
    [{"id": 24698564, "email": "g@x.it", "refunded": False, "checkedIn": False}],
    {"result": {"items": [{"legacyId": 24698564, "status": "paid", "checkedInCount": 0}]}},
])
def test_formati_prenotazione_mcp(data):
    t = xt.booking_to_ticket(xt._as_list(data)[0])
    assert t["booking"]["bookingId"] == 24698564
    assert t["hasCheckedIn"] is False and t["isActive"] is True


def test_prenotazione_rimborsata_o_entrata():
    t = xt.booking_to_ticket({"legacyId": 1, "isRefunded": True, "hasCheckedIn": True})
    assert t["isActive"] is False and t["hasCheckedIn"] is True
    t = xt.booking_to_ticket({"legacyId": 1, "status": "REFUNDED"})
    assert t["isActive"] is False


async def test_mcp_cerca_per_ordine_prima_che_per_email(monkeypatch):
    from mail import xceed_mcp
    cercati = []

    async def _tool(name, args):
        cercati.append(args["search"])
        return {"data": [{"legacyId": 24698564, "hasCheckedIn": False}]} if args["search"] == "24698564" else {"data": []}

    monkeypatch.setattr(xceed_mcp, "tool_json", _tool)
    out = await xt.find_via_mcp([_EVENT], {"g@x.it"}, {"24698564"}, set())
    assert out["match"] == "codice" and cercati[0] == "24698564"
    assert out["tickets"][0]["booking"]["bookingId"] == 24698564


def test_mask_oscura_i_dati_personali():
    m = xt.mask({"buyer": {"email": "g@x.it", "firstName": "Geraldo"}, "legacyId": 5, "note": "a@b.c"})
    assert m["buyer"]["email"] == "***" and m["buyer"]["firstName"] == "***"
    assert m["legacyId"] == 5 and m["note"] == "***"
