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
    st = {"replies": [], "labels": [], "notes": [], "existing": set(), "staff": False,
          "bot_sent": []}

    async def _list(q, n):
        if q.startswith("in:sent"):
            return list(st["bot_sent"])
        for lab in list(st["existing"]):
            if f'label:"{lab}"' in q:
                return ["x"]
        return [] if "label:" in q.replace("-label:", "") else ["m1"]

    async def _get(i):
        if i.startswith("sent"):
            return {**_msg(i), "date_ms": int(i.split(":")[1])}
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
    # Di default dentro la finestra Xceed (la serata di prova è del 10/10/2026).
    monkeypatch.setattr(refunds, "past_xceed_cutoff", lambda found, now=None: False)
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


async def test_staff_ha_risposto_a_mano_il_bot_propone_senza_scrivere(flow, monkeypatch):
    """Caso reale 25/10: rimandato a mano a rimborsi@, poi richiesta completa nello stesso thread."""
    flow["staff"] = True

    async def _an(msgs):
        return _a()

    async def _find(*a, **k):
        return _found(_ticket())
    monkeypatch.setattr(refunds, "analyze", _an)
    monkeypatch.setattr(xt, "find_request_tickets", _find)
    monkeypatch.setattr(refunds.settings, "refund_auto_execute", True)
    assert (await refunds.process_sender("cliente@example.com", [_msg()])) == "proposta (staff)"
    assert flow["replies"] == [] and refunds.LABEL_DONE not in flow["labels"]
    status, note = flow["notes"][-1]
    assert status == "ok" and "`!rimborsa`" in note and "a mano" in note


async def test_staff_ha_risposto_oltre_finestra_propone_inoltra(flow, monkeypatch):
    flow["staff"] = True

    async def _an(msgs):
        return _a()

    async def _find(*a, **k):
        return _found(_ticket())
    monkeypatch.setattr(refunds, "analyze", _an)
    monkeypatch.setattr(xt, "find_request_tickets", _find)
    monkeypatch.setattr(refunds, "past_xceed_cutoff", lambda found, now=None: True)
    await refunds.process_sender("cliente@example.com", [_msg()])
    assert flow["replies"] == []
    assert "`!inoltra`" in flow["notes"][-1][1]


async def test_staff_ha_risposto_incompleta_propone_manca(flow, monkeypatch):
    flow["staff"] = True

    async def _an(msgs):
        return _a(prova_acquisto=False)
    monkeypatch.setattr(refunds, "analyze", _an)
    await refunds.process_sender("cliente@example.com", [_msg()])
    assert flow["replies"] == []
    assert flow["notes"][-1][0] == "incompleta" and "`!manca`" in flow["notes"][-1][1]


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
    base.setdefault("refund_plan", [{"booking_id": b, "full": True, "codes": [], "tickets": 1}
                                    for b in base["booking_ids"]])
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


async def test_nessun_doppio_messaggio_senza_nuove_mail(flow, monkeypatch):
    """Caso reale 13/10: stessa richiesta della motivazione mandata due volte in 3 minuti."""
    async def _an(msgs):
        return _a(motivazione="", respinto_alla_porta=False)
    monkeypatch.setattr(refunds, "analyze", _an)
    # Il bot ha già scritto DOPO l'ultima mail del cliente.
    flow["bot_sent"] = [f"sent:{int(_IN_TIME.timestamp() * 1000) + 60_000}"]
    esito = await refunds.process_sender("cliente@example.com", [_msg()])
    assert esito == "già risposto" and flow["replies"] == []


async def test_nuova_mail_dopo_la_nostra_si_rivaluta(flow, monkeypatch):
    async def _an(msgs):
        return _a(biglietti=False)
    monkeypatch.setattr(refunds, "analyze", _an)
    flow["bot_sent"] = [f"sent:{int(_IN_TIME.timestamp() * 1000) - 60_000}"]
    assert await refunds.process_sender("cliente@example.com", [_msg()]) == "incompleta"
    assert flow["replies"]


def test_tutte_le_mail_del_cliente_segnate_come_lette():
    import inspect
    src = inspect.getsource(refunds.process_sender)
    assert "new_msgs = msgs" in src


@pytest.mark.parametrize("testo", [
    templates.refund_ack("it"), templates.refund_ack("en"),
    templates.refund_missing("it", ["biglietti"]), templates.refund_done("it", "X"),
    templates.refund_refused("it"), templates.refund_refused("en", "nota"),
])
def test_niente_formula_di_chiusura(testo):
    """La firma Gmail di george@ apre già con "Best,": "Un saluto" sarebbe doppio."""
    for chiusura in ("Un saluto", "Kind regards", "Cordiali saluti"):
        assert chiusura not in testo


def test_firma_testo_con_a_capo():
    sig = '<div dir="ltr"><div>Best,</div><div>George<div>Gate Milano | Gate Sardinia</div></div></div>'
    assert "George\nGate Milano" in gm._html_to_text(sig).replace("\n\n", "\n")


async def test_eventi_recenti_anche_se_l_api_parte_dal_2018(monkeypatch):
    """Caso reale 13/10: la Partner API ignora il filtro data e parte dal 2018."""
    import time as _t
    now = int(_t.time())
    pagine = [
        [{"id": i, "uuid": f"old{i}", "name": "vecchio", "startingTime": 1519400000 + i} for i in range(200)],
        [{"id": 999, "uuid": "perreo", "name": "PERREO XL", "startingTime": now - 3 * 86400},
         {"id": 1000, "uuid": "futuro", "name": "futuro", "startingTime": now + 5 * 86400}],
        [],
    ]
    chiamate = []

    class _R:
        def __init__(self, data):
            self._d = data

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": self._d}

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            chiamate.append(params["offset"])
            return _R(pagine[len(chiamate) - 1])

    monkeypatch.setattr(xt, "_events_cache", {"events": [], "exp": 0.0})
    monkeypatch.setattr(xt.httpx, "AsyncClient", lambda *a, **k: _C())
    eventi = await xt.recent_events(14)
    assert [e["name"] for e in eventi] == ["PERREO XL"]
    assert chiamate == [0, 200, 202]


def test_serata_del_biglietto_cercata_per_prima():
    """Data, nome e numero evento sono scritti sul biglietto: si parte da lì."""
    ev_a = {"id": 1, "name": "RAW", "startingTime": _EVENT["startingTime"] - 86400}
    ev_b = {**_EVENT, "id": 240981}
    ev_c = {"id": 3, "name": "PERREO XL", "startingTime": _EVENT["startingTime"] - 7 * 86400}
    evs = [ev_a, ev_c, ev_b]
    assert sorted(evs, key=lambda e: xt._event_rank(e, "", "240981", ""))[0] is ev_b
    assert sorted(evs, key=lambda e: xt._event_rank(e, "2026-10-10", "", ""))[0] is ev_b
    assert sorted(evs, key=lambda e: xt._event_rank(e, "", "", "Raw"))[0] is ev_a


async def test_verifica_rifa_i_controlli_senza_scrivere(staff_gmail, monkeypatch):
    chiamato = []

    async def _recheck(sender):
        chiamato.append(sender)
        return "ok"

    monkeypatch.setattr(refunds, "recheck", _recheck)
    out = await staff.refund_action("!verifica", _ctx(), "George")
    assert chiamato == ["cliente@example.com"] and "rifatta" in out
    assert staff_gmail == []           # nessuna mail al cliente


async def test_verifica_funziona_anche_su_scheda_chiusa(staff_gmail, monkeypatch):
    async def _recheck(sender):
        return "ok"
    monkeypatch.setattr(refunds, "recheck", _recheck)
    out = await staff.refund_action("!verifica", _ctx(sent_by="George"), "Marco")
    assert "rifatta" in out


async def test_riverifica_tutte_le_aperte_senza_scrivere(monkeypatch):
    rifatti, scritte = [], []

    async def _senders():
        return ["a@x.it", "b@x.it", "c@x.it"]

    async def _has(sender, label):
        return sender == "b@x.it" and label == refunds.LABEL_DONE   # b già rimborsato

    async def _recheck(s, forward_old=False):
        rifatti.append(s)
        return "ok"

    async def _send(*a, **k):
        scritte.append(a)

    monkeypatch.setattr(refunds, "open_senders", _senders)
    monkeypatch.setattr(refunds, "_has_label", _has)
    monkeypatch.setattr(refunds, "recheck", _recheck)
    monkeypatch.setattr(gm, "send_reply", _send)
    out = await refunds.recheck_all()
    assert rifatti == ["a@x.it", "c@x.it"] and out["esiti"] == {"ok": 2}
    assert scritte == []


def test_prezzo_in_formato_mcp_leggibile():
    t = {"hasCheckedIn": False, "isActive": True, "booking": {"bookingId": 1},
         "offer": {"name": "Men General Admission",
                   "price": {"amount": {"onlinePrice": 15, "offlinePrice": 0, "currency": "EUR"}}}}
    assert xt.summarize([t])["price"] == "€15"



# --- finestra Xceed e inoltro a Bianca (staff 13/10) -----------------------------------------

def test_finestra_xceed_lunedi_mattina():
    c = refunds.xceed_cutoff("2026-10-10")
    assert c.strftime("%Y-%m-%d %H:%M") == f"2026-10-12 {settings.refund_xceed_cutoff_hour:02d}:00"
    found = _found(_ticket())
    assert not refunds.past_xceed_cutoff(found, now=datetime(2026, 10, 11, 20, tzinfo=ROME))
    assert refunds.past_xceed_cutoff(found, now=datetime(2026, 10, 12, 15, tzinfo=ROME))


def test_scheda_dice_fino_a_quando_si_rimborsa():
    v = refunds.assess(_a(), _found(_ticket()), _IN_TIME)
    assert v["status"] == "ok"                      # l'informazione non cambia l'esito
    assert any("fino a lunedì 12/10" in t for _, t in v["checks"])


async def test_oltre_finestra_si_inoltra_a_bianca(flow, monkeypatch):
    inviate = []

    async def _an(msgs):
        return _a()

    async def _find(*a, **k):
        return _found(_ticket())

    async def _new(to, subject, body, attachments=None):
        inviate.append((to, subject, body))

    monkeypatch.setattr(refunds, "analyze", _an)
    monkeypatch.setattr(xt, "find_request_tickets", _find)
    monkeypatch.setattr(refunds, "past_xceed_cutoff", lambda found, now=None: True)
    monkeypatch.setattr(gm, "send_new", _new)

    async def _no(sender):
        return False
    monkeypatch.setattr(refunds, "already_forwarded", _no)
    esito = await refunds.process_sender("cliente@example.com", [_msg()])
    assert esito == "inoltrata"
    to, subject, body = inviate[0]
    assert to == "bianca@xceed.me"
    assert "24556189" in body and "PERREO XL" in body and "commissioni incluse" in body
    # Il cliente riceve il messaggio dei 14 giorni, una volta sola.
    assert len(flow["replies"]) == 1 and "14 giorni" in flow["replies"][0][0]
    assert refunds.LABEL_FORWARDED in flow["labels"]


async def test_richiesta_inoltrata_non_riceve_altri_messaggi(flow, monkeypatch):
    flow["existing"].add(refunds.LABEL_FORWARDED)

    async def _boom(msgs):
        raise AssertionError("non deve rianalizzare")
    monkeypatch.setattr(refunds, "analyze", _boom)
    esito = await refunds.process_sender("cliente@example.com", [_msg()])
    assert esito.startswith("nota") and flow["replies"] == []


async def test_comando_inoltra(staff_gmail, monkeypatch):
    chiamato = []

    async def _fwd(sender, note=""):
        chiamato.append((sender, note))
        return "Inoltrata a bianca@xceed.me, cliente avvisato (14 giorni)"

    monkeypatch.setattr(refunds, "forward_by_staff", _fwd)
    ctx = _ctx()
    out = await staff.refund_action("!inoltra serata del 3/10", ctx, "George")
    assert chiamato == [("cliente@example.com", "serata del 3/10")]
    assert "Inoltrata" in out and ctx["sent_by"] == "George"


async def test_rimborsa_rifiutato_suggerisce_inoltra(staff_gmail, monkeypatch):
    from mail import xceed_mcp

    async def _call(name, args):
        return {"isError": True, "content": [{"type": "text", "text": "Participant can not be refunded"}]}

    monkeypatch.setattr(xceed_mcp, "configured", lambda: True)
    monkeypatch.setattr(xceed_mcp, "call_tool", _call)
    out = await staff.refund_action("!rimborsa", _ctx(), "George")
    assert "!inoltra" in out



async def test_gia_inoltrato_a_mano_non_si_reinoltra(flow, monkeypatch):
    """Caso reale: thezuberi inoltrato a mano a Bianca il 6/10 da un thread di info@."""
    inviate = []

    async def _new(*a, **k):
        inviate.append(a)

    async def _yes(sender):
        return True

    monkeypatch.setattr(gm, "send_new", _new)
    monkeypatch.setattr(refunds, "already_forwarded", _yes)
    await refunds.forward_to_xceed("cliente@example.com", [_msg()], _a(), _found(_ticket()))
    assert inviate == [] and flow["replies"] == []
    assert refunds.LABEL_FORWARDED in flow["labels"]



async def test_arretrato_incompleto_riceve_cosa_manca(flow, monkeypatch):
    async def _an(msgs):
        return _a(motivazione="", respinto_alla_porta=False)
    monkeypatch.setattr(refunds, "analyze", _an)
    esito = await refunds.recheck("cliente@example.com", forward_old=True)
    assert esito == "incompleta"
    assert "cosa è successo all'ingresso" in flow["replies"][0][0]


async def test_riverifica_semplice_non_scrive(flow, monkeypatch):
    async def _an(msgs):
        return _a(motivazione="", respinto_alla_porta=False)
    monkeypatch.setattr(refunds, "analyze", _an)
    await refunds.recheck("cliente@example.com")
    assert flow["replies"] == []


# --- rimborso parziale (staff 17/10: in un gruppo si rimborsano solo i non entrati) -------

def test_piano_parziale_solo_i_non_entrati():
    tickets = [_ticket(qr="A", checked=True), _ticket(qr="B", checked=True),
               _ticket(qr="C"), _ticket(qr="D")]
    plan = xt.refund_plan(tickets)
    assert plan == [{"booking_id": "24556189", "full": False, "codes": ["C", "D"],
                     "tickets": 2, "missing_codes": 0}]


def test_piano_totale_e_prenotazioni_separate():
    """Caso momo 17/10 se i 4 biglietti sono 4 prenotazioni: totale solo sulle non entrate."""
    tickets = [_ticket(bid=1, checked=True), _ticket(bid=2, checked=True), _ticket(bid=3), _ticket(bid=4)]
    plan = xt.refund_plan(tickets)
    assert [(p["booking_id"], p["full"]) for p in plan] == [("3", True), ("4", True)]


def test_piano_esclude_gia_rimborsati_e_tutti_entrati():
    assert xt.refund_plan([_ticket(checked=True), _ticket(qr="B", checked=True)]) == []
    plan = xt.refund_plan([_ticket(qr="A", active=False), _ticket(qr="B")])
    assert plan[0]["full"] is False and plan[0]["codes"] == ["B"]


def test_verifica_parziale_e_verde():
    tickets = [_ticket(qr="A", checked=True), _ticket(qr="B", checked=True),
               _ticket(qr="C"), _ticket(qr="D")]
    v = refunds.assess(_a(), _found(*tickets), _IN_TIME)
    assert v["status"] == "ok"
    assert any("PARZIALE" in t and "2 biglietti non usati" in t for _, t in v["checks"])


def test_verifica_parziale_senza_codici_da_verificare():
    tickets = [_ticket(qr="", checked=True), _ticket(qr="")]
    v = refunds.assess(_a(), _found(*tickets), _IN_TIME)
    assert v["status"] == "da_verificare"
    assert any("dashboard" in t for _, t in v["checks"])


def test_dettaglio_mcp_un_biglietto_per_pass():
    b = {"legacyId": 77, "buyer": {"email": "m@x.it"}, "hasCheckedIn": True}
    detail = {"data": {"passes": [
        {"qrCode": "Q1", "hasCheckedIn": True, "isActive": True},
        {"qrCode": "Q2", "hasCheckedIn": False, "isActive": True}]}}
    tickets = xt.booking_tickets(b, detail)
    assert [t["qrCode"] for t in tickets] == ["Q1", "Q2"]
    assert xt.refund_plan(tickets)[0]["codes"] == ["Q2"]
    assert len(xt.booking_tickets(b, None)) == 1


async def test_rimborsa_parziale_un_biglietto_alla_volta(staff_gmail, monkeypatch):
    from mail import xceed_mcp
    chiamate = []

    async def _call(name, args):
        chiamate.append(args)
        return {"content": []}

    monkeypatch.setattr(xceed_mcp, "configured", lambda: True)
    monkeypatch.setattr(xceed_mcp, "call_tool", _call)
    ctx = _ctx(refund_partial=True, refund_plan=[
        {"booking_id": "24556189", "full": False, "codes": ["C", "D"], "tickets": 2, "missing_codes": 0}])
    out = await staff.refund_action("!rimborsa", ctx, "George")
    assert out.startswith("✅") and "2 biglietti" in out
    assert [(a["legacyId"], a["isFullRefund"], a.get("code")) for a in chiamate] == \
        [(24556189, False, "C"), (24556189, False, "D")]
    text = [c[1] for c in staff_gmail if c[0] == "send"][0]
    assert "dei 2 biglietti non utilizzati" in text and "commissioni" not in text


async def test_rimborsa_parziale_senza_codici_non_tocca_xceed(staff_gmail, monkeypatch):
    from mail import xceed_mcp
    chiamate = []

    async def _call(name, args):
        chiamate.append(args)
        return {"content": []}

    monkeypatch.setattr(xceed_mcp, "configured", lambda: True)
    monkeypatch.setattr(xceed_mcp, "call_tool", _call)
    ctx = _ctx(refund_partial=True, refund_plan=[
        {"booking_id": "1", "full": True, "codes": [], "tickets": 1},
        {"booking_id": "2", "full": False, "codes": [], "tickets": 1, "missing_codes": 1}])
    out = await staff.refund_action("!rimborsa", ctx, "George")
    assert out.startswith("❌") and chiamate == [] and staff_gmail == []


async def test_scheda_vecchia_da_verificare_chiede_verifica(staff_gmail, monkeypatch):
    """Le schede di prima avevano solo booking_ids: rimborsarle intere toccherebbe chi è entrato."""
    from mail import xceed_mcp
    monkeypatch.setattr(xceed_mcp, "configured", lambda: True)
    ctx = _ctx(status="da_verificare")
    del ctx["refund_plan"]
    out = await staff.refund_action("!rimborsa", ctx, "George")
    assert "!verifica" in out and staff_gmail == []


def test_inoltro_parziale_indica_i_codici():
    tickets = [_ticket(qr="A", checked=True), _ticket(qr="C")]
    body = refunds._forward_body("c@x.it", _a(), _found(*tickets))
    assert "PARZIALE" in body and "codici C" in body and "NON vanno rimborsati" in body


# --- biglietti "non trovati" che c'erano (caso reale 24/10, biglietti backstage) ----------

async def test_mcp_trova_per_cognome_se_il_nome_intero_non_basta(monkeypatch):
    from mail import xceed_mcp

    async def _tool(name, args):
        if name != "bookings_search":
            raise RuntimeError("no detail")
        if args["search"] == "arafeh":
            return {"data": [
                {"legacyId": 9, "buyer": {"firstName": "Basel", "lastName": "Arafeh"}},
                {"legacyId": 8, "buyer": {"firstName": "Omar", "lastName": "Arafeh"}}]}
        return {"data": []}

    monkeypatch.setattr(xceed_mcp, "tool_json", _tool)
    out = await xt.find_via_mcp([_EVENT], {"b@yahoo.com"}, set(), {"Basel Arafeh"})
    assert out["match"] == "nome"
    assert [t["booking"]["bookingId"] for t in out["tickets"]] == [9]


def test_partner_nome_in_ordine_diverso():
    t = _ticket(first="Basel", last="Arafeh")
    found, how = xt.match_tickets([t], set(), set(), {"ARAFEH Basel"})
    assert how == "nome" and found == [t]


async def test_errori_della_ricerca_non_diventano_non_trovati(monkeypatch):
    from mail import xceed_mcp

    async def _tool(name, args):
        raise RuntimeError("timeout")

    async def _events(days):
        return [_EVENT]

    async def _tickets(ev):
        raise RuntimeError("502 Bad Gateway")

    monkeypatch.setattr(xt.settings, "xceed_api_key", "k")
    monkeypatch.setattr(xceed_mcp, "configured", lambda: True)
    monkeypatch.setattr(xceed_mcp, "tool_json", _tool)
    monkeypatch.setattr(xt, "recent_events", _events)
    monkeypatch.setattr(xt, "event_tickets", _tickets)
    found = await xt.find_request_tickets({"b@yahoo.com"}, set(), {"Basel Arafeh"})
    assert found["errors"] and not found.get("tickets")
    v = refunds.assess(_a(), found, _IN_TIME)
    assert v["status"] == "da_verificare"
    assert any(i == "⚠️" and "errori" in t for i, t in v["checks"])
    assert not any("NON trovati" in t for _, t in v["checks"])


def test_scheda_dice_cosa_ha_cercato():
    from notifications import discord as d
    out = d._searched("b@yahoo.com", {"codici": ["123"], "nomi": ["Basel Arafeh"], "data_evento": "2026-10-23"})
    assert "b@yahoo.com" in out and "Basel Arafeh" in out and "2026-10-23" in out


async def test_manca_scrive_cosa_manca_senza_chiudere(staff_gmail):
    ctx = _ctx(missing=["prova_acquisto", "biglietti"])
    out = await staff.refund_action("!manca", ctx, "George")
    assert out.startswith("✅") and ctx["sent_by"] == ""
    text = [c[1] for c in staff_gmail if c[0] == "send"][0]
    assert "prova d'acquisto" in text
    assert not any(c[0] == "label" for c in staff_gmail)


async def test_manca_senza_niente_da_chiedere(staff_gmail):
    out = await staff.refund_action("!manca", _ctx(missing=[]), "George")
    assert out.startswith("❌") and staff_gmail == []
