"""Risponditore email: protezioni e decisione invia/abbozza.

Il valore di questa funzione non sta nel fatto che risponde, ma nel fatto che NON
risponde quando non deve. I test coprono soprattutto quello:
- a chi non si scrive mai (automatismi, newsletter, noi stessi);
- quando una risposta corretta va comunque fatta rileggere (soldi, disabilità,
  reclami, allegati, classificazione incerta);
- che non si risponda due volte allo stesso thread, né sopra a una risposta che lo
  staff ha già dato a mano.
"""
import pytest

from config import settings
from mail import triage


def _msg(**kw):
    base = {
        "id": "m1", "thread_id": "t1", "label_ids": ["INBOX", "UNREAD"],
        "from": "Sofia <sofia@example.com>", "from_email": "sofia@example.com",
        "to": "info@gatemilano.com", "subject": "Informazioni",
        "message_id_header": "<abc@mail>", "references": "",
        "list_unsubscribe": "", "precedence": "", "auto_submitted": "",
        "body": "A che ora aprite sabato?", "has_attachments": False,
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _live_mode(monkeypatch):
    # I test sulla decisione hanno senso solo con la modalità solo-bozze SPENTA:
    # altrimenti la risposta sarebbe sempre "bozza" e non proverebbero nulla.
    monkeypatch.setattr(settings, "mail_draft_only", False)


# --- a chi non si risponde mai ------------------------------------------------

@pytest.mark.parametrize("sender", [
    "no-reply@eventbrite.com",
    "noreply@xceed.me",
    "mailer-daemon@googlemail.com",
    "postmaster@example.com",
    "notifications@instagram.com",
])
def test_mittenti_automatici_mai(sender):
    assert triage.hard_skip(_msg(from_email=sender, **{"from": sender}))


def test_noi_stessi_mai():
    # Una risposta a un nostro indirizzo è il modo classico di innescare un loop.
    assert triage.hard_skip(_msg(from_email="george@gatemilano.com"))
    assert triage.hard_skip(_msg(from_email="vip@gatesardinia.it"))


def test_newsletter_e_liste_mai():
    assert triage.hard_skip(_msg(list_unsubscribe="<https://x.test/unsub>"))
    assert triage.hard_skip(_msg(precedence="bulk"))


def test_autorisposte_mai():
    # Un "sono in ferie" automatico non va ri-risposto.
    assert triage.hard_skip(_msg(auto_submitted="auto-replied"))


def test_corpo_vuoto_mai():
    assert triage.hard_skip(_msg(body="   "))


def test_cliente_normale_passa():
    assert triage.hard_skip(_msg()) == ""


# --- cosa può partire da solo -------------------------------------------------

@pytest.mark.parametrize("categoria", sorted(triage.AUTOSEND_CATEGORIES))
def test_categorie_fattuali_possono_partire(categoria):
    ok, _ = triage.can_autosend(_msg(), {"categoria": categoria, "confidenza": "alta"})
    assert ok


@pytest.mark.parametrize("categoria", sorted(triage.REVIEW_CATEGORIES))
def test_categorie_delicate_sempre_in_bozza(categoria):
    ok, motivo = triage.can_autosend(_msg(), {"categoria": categoria, "confidenza": "alta"})
    assert not ok and motivo


def test_classificazione_incerta_va_in_bozza():
    ok, motivo = triage.can_autosend(_msg(), {"categoria": "orari", "confidenza": "bassa"})
    assert not ok
    assert "incerta" in motivo


def test_allegati_vanno_in_bozza():
    # Un allegato significa quasi sempre un documento da guardare (biglietto,
    # certificato, fattura): non è roba da automatismo.
    ok, motivo = triage.can_autosend(
        _msg(has_attachments=True), {"categoria": "orari", "confidenza": "alta"})
    assert not ok
    assert "allegati" in motivo


@pytest.mark.parametrize("testo", [
    "vorrei un rimborso del biglietto",
    "mi serve la fattura",
    "ho la L.104, come funziona?",
    "mia figlia è in carrozzina",
    "vi mando l'avvocato",
    "sono minorenne, posso entrare?",
])
def test_parole_sensibili_forzano_la_revisione(testo):
    """Rete di sicurezza: scatta anche se la categoria sembrava innocua."""
    ok, motivo = triage.can_autosend(
        _msg(body=testo), {"categoria": "orari", "confidenza": "alta"})
    assert not ok and motivo


def test_interruttore_solo_bozze_vince_su_tutto(monkeypatch):
    monkeypatch.setattr(settings, "mail_draft_only", True)
    ok, motivo = triage.can_autosend(_msg(), {"categoria": "orari", "confidenza": "alta"})
    assert not ok
    assert "solo-bozze" in motivo


# --- interruttore generale ----------------------------------------------------

async def test_job_spento_non_fa_nulla(monkeypatch):
    from mail import responder
    monkeypatch.setattr(settings, "mail_autoresponder_enabled", False)
    assert await responder.run_once() == {"skipped": "disattivato"}


async def test_senza_credenziali_non_parte(monkeypatch):
    from mail import responder
    monkeypatch.setattr(settings, "mail_autoresponder_enabled", True)
    monkeypatch.setattr("mail.gmail_client.configured", lambda: False)
    out = await responder.run_once()
    assert out == {"skipped": "credenziali mancanti"}


# --- anti-doppione ------------------------------------------------------------

async def test_thread_gia_gestito_viene_saltato(monkeypatch):
    """Se abbiamo già scritto nel thread non ci si torna: vale sia contro i loop
    sia contro il rischio di scavalcare una risposta data a mano dallo staff."""
    from mail import responder
    etichette = []

    async def _get(_id):
        return _msg()

    async def _has_reply(_tid):
        return True

    async def _label(mid, name):
        etichette.append(name)

    monkeypatch.setattr("mail.gmail_client.get_message", _get)
    monkeypatch.setattr("mail.gmail_client.thread_has_our_reply", _has_reply)
    monkeypatch.setattr("mail.gmail_client.add_label", _label)
    esito = await responder.process_one("m1")
    assert "thread già gestito" in esito
    assert etichette == [responder.LABEL_SKIPPED]


async def test_mittente_automatico_etichettato_e_basta(monkeypatch):
    from mail import responder
    etichette = []

    async def _get(_id):
        return _msg(from_email="no-reply@xceed.me")

    async def _label(mid, name):
        etichette.append(name)

    async def _boom(*a, **k):
        raise AssertionError("non deve nemmeno controllare il thread")

    monkeypatch.setattr("mail.gmail_client.get_message", _get)
    monkeypatch.setattr("mail.gmail_client.add_label", _label)
    monkeypatch.setattr("mail.gmail_client.thread_has_our_reply", _boom)
    esito = await responder.process_one("m1")
    assert "ignorata" in esito
    assert etichette == [responder.LABEL_SKIPPED]


# --- casella Gmail personale: si tocca solo la posta di info@ -------------------
#
# info@gatemilano.com è su Aruba e viene inoltrata su una Gmail PERSONALE, che
# riceve anche altro. Il bot deve vedere solo ciò che è passato da info@.

def test_posta_personale_mai():
    personale = _msg(to="gateviavaltellina@gmail.com", cc="",
                     delivered_to=["gateviavaltellina@gmail.com"])
    assert triage.hard_skip(personale) == "non indirizzata alla casella del bot"


def test_posta_inoltrata_da_info_passa():
    # Come arriva davvero: To può essere info@, il Delivered-To lo conferma.
    inoltrata = _msg(to="info@gatemilano.com",
                     delivered_to=["gateviavaltellina@gmail.com", "info@gatemilano.com"])
    assert triage.hard_skip(inoltrata) == ""


def test_info_in_ccn_riconosciuta_dal_delivered_to():
    # In Ccn info@ non compare in To/Cc: resta il Delivered-To.
    ccn = _msg(to="altro@example.com", cc="",
               delivered_to=["gateviavaltellina@gmail.com", "info@gatemilano.com"])
    assert triage.hard_skip(ccn) == ""


def test_query_filtra_sulla_casella():
    from mail import responder
    q = responder._query()
    assert f"deliveredto:{settings.mail_from_address}" in q
    assert "is:unread" in q
    # Niente arretrato: le non lette vecchie restano allo staff.
    assert f"newer_than:{settings.mail_max_age_days}d" in q
    assert settings.mail_max_age_days <= 3


async def test_senza_alias_invia_come_si_fa_bozza(monkeypatch):
    """Senza l'alias "Invia come" Gmail spedirebbe dall'indirizzo personale."""
    from mail import responder
    azioni = []

    async def _get(_id):
        return _msg()

    async def _no(*a, **k):
        return False

    async def _verdict(_m):
        return {"categoria": "orari", "confidenza": "alta", "domanda": "orari sabato"}

    async def _compose(_m, _v):
        return "Apriamo alle 23:00."

    async def _send(*a, **k):
        azioni.append("send")

    async def _draft(*a, **k):
        azioni.append("draft")

    async def _label(*a, **k):
        pass

    async def _notify(*a, **k):
        pass

    monkeypatch.setattr("mail.gmail_client.get_message", _get)
    monkeypatch.setattr("mail.gmail_client.thread_has_our_reply", _no)
    monkeypatch.setattr("mail.gmail_client.can_send_as", _no)
    monkeypatch.setattr("mail.gmail_client.send_reply", _send)
    monkeypatch.setattr("mail.gmail_client.create_draft", _draft)
    monkeypatch.setattr("mail.gmail_client.add_label", _label)
    monkeypatch.setattr(triage, "classify", _verdict)
    monkeypatch.setattr(responder, "_compose", _compose)
    monkeypatch.setattr(responder, "_notify", _notify)
    esito = await responder.process_one("m1")
    assert azioni == ["draft"]
    assert "Invia come" in esito


def test_risposta_parte_da_george_non_da_info():
    """Si legge info@, si risponde da george@ (alias già verificato in Gmail)."""
    import base64
    from mail import gmail_client as gm
    raw = gm._build_raw("sofia@example.com", "Info", "ciao", "<abc@mail>", "")
    headers = base64.urlsafe_b64decode(raw).decode()
    assert f"From: Gate Milano <{settings.mail_reply_as}>" in headers
    assert settings.mail_reply_as == "george@gatemilano.com"
    assert settings.mail_from_address == "info@gatemilano.com"


@pytest.mark.parametrize("categoria", [
    "CATEGORY_UPDATES", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS",
])
def test_schede_automatiche_gmail_mai(categoria):
    """Caso reale: Aruba scrive ogni giorno a info@ "casella quasi piena" (Aggiornamenti)."""
    avviso = _msg(from_email="comunicazioni@staff.aruba.it",
                  label_ids=["UNREAD", "IMPORTANT", categoria, "INBOX"])
    assert triage.hard_skip(avviso).startswith("scheda Gmail")


def test_scheda_principale_passa():
    assert triage.hard_skip(_msg(label_ids=["UNREAD", "IMPORTANT", "INBOX"])) == ""


# --- lingua -------------------------------------------------------------------
#
# Il prompt dell'email contiene istruzioni in italiano: la lingua va rilevata sulla
# mail del cliente, altrimenti chi scrive in inglese rischia una risposta in italiano.

async def test_lingua_rilevata_sulla_mail_del_cliente(monkeypatch):
    from mail import responder
    import ai.claude_client as cc
    import rag.context_builder as cb
    preso = {}

    async def _ctx(*a, **k):
        return "", []

    async def _gen(**kw):
        preso.update(kw)
        return "ok"

    monkeypatch.setattr(cb, "build_rag_context", _ctx)
    monkeypatch.setattr(cc, "generate_response", _gen)
    msg = _msg(subject="Opening hours",
               body="Hi, what time do you open on Saturday and can I buy tickets at the door?")
    await responder._compose(msg, {"domanda": "opening hours"})
    assert "what time do you open" in preso["language_text"]
    assert "STESSA lingua" in preso["user_message"]
    from ai.language import language_directive
    assert "INGLESE" in language_directive(preso["language_text"])


def test_nota_automatica_nella_lingua_del_cliente():
    from mail import responder
    en = _msg(subject="Tables", body="Hello, how much is a table for 6 people on Friday?")
    it = _msg(subject="Tavoli", body="Ciao, quanto costa un tavolo per 6 persone venerdì?")
    assert "sent automatically" in responder._footer(en)
    assert "inviata automaticamente" in responder._footer(it)


# --- non rimandare a info@ chi ha scritto a info@ ------------------------------

async def test_istruzione_niente_rimando_a_info(monkeypatch):
    from mail import responder
    import ai.claude_client as cc
    import rag.context_builder as cb
    preso = {}

    async def _ctx(*a, **k):
        return "", []

    async def _gen(**kw):
        preso.update(kw)
        return "ok"

    monkeypatch.setattr(cb, "build_rag_context", _ctx)
    monkeypatch.setattr(cc, "generate_response", _gen)
    await responder._compose(_msg(), {"domanda": "rimborso"})
    assert "NON dire MAI di scrivere a info@gatemilano.com" in preso["user_message"]
    assert "rispondi a questa email" in preso["user_message"]


async def test_risposta_che_rimanda_a_info_va_in_bozza(monkeypatch):
    from mail import responder
    azioni = []

    async def _get(_id):
        return _msg()

    async def _no(*a, **k):
        return False

    async def _yes(*a, **k):
        return True

    async def _verdict(_m):
        return {"categoria": "orari", "confidenza": "alta", "domanda": "orari"}

    async def _compose(_m, _v):
        return "Per questo scrivi a info@gatemilano.com, ti rispondiamo lì."

    async def _send(*a, **k):
        azioni.append("send")

    async def _draft(*a, **k):
        azioni.append("draft")
        return "d1"

    async def _nop(*a, **k):
        pass

    monkeypatch.setattr("mail.gmail_client.get_message", _get)
    monkeypatch.setattr("mail.gmail_client.thread_has_our_reply", _no)
    monkeypatch.setattr("mail.gmail_client.can_send_as", _yes)
    monkeypatch.setattr("mail.gmail_client.send_reply", _send)
    monkeypatch.setattr("mail.gmail_client.create_draft", _draft)
    monkeypatch.setattr("mail.gmail_client.add_label", _nop)
    monkeypatch.setattr(triage, "classify", _verdict)
    monkeypatch.setattr(responder, "_compose", _compose)
    monkeypatch.setattr(responder, "_notify", _nop)
    esito = await responder.process_one("m1")
    assert azioni == ["draft"]
    assert "rimanda a info@" in esito


# --- proposte B2B: risposta fissa approvata (13/10) ---------------------------

def test_template_proposte_nella_lingua_del_mittente():
    from mail.templates import proposal_reply
    en = proposal_reply("Hi guys, Timo Maas is available for bookings on October 30 "
                        "and 31, with some flexibility on the fee for these two dates.")
    it = proposal_reply("Ciao, siamo una band e ci piacerebbe venire a suonare da voi "
                        "da gennaio in poi, quando avete date libere?")
    assert "programming" in en and "programmazione" not in en
    assert "programmazione del Gate" in it
    # Professionale ma fredda: nessuna promessa, nessuna richiesta di materiale.
    for t in (en, it):
        assert "!" not in t


def test_template_lingua_incerta_doppio():
    from mail.templates import proposal_reply
    t = proposal_reply("Techno set 2027")
    assert "programmazione" in t and "programming" in t


@pytest.mark.parametrize("confidenza,body,parte", [
    ("alta", "Vi propongo il mio artista per una data", True),
    ("bassa", "Vi propongo il mio artista per una data", False),
    ("alta", "Vi propongo il mio artista, allego fattura", False),
])
def test_proposte_quando_parte_da_sola(confidenza, body, parte):
    ok, _ = triage.can_send_proposal_reply(
        _msg(body=body), {"categoria": "proposte", "confidenza": confidenza})
    assert ok is parte


def test_proposte_partono_anche_in_solo_bozze(monkeypatch):
    # Il testo è fisso e approvato: solo-bozze riguarda le risposte generate.
    monkeypatch.setattr(settings, "mail_draft_only", True)
    ok, _ = triage.can_send_proposal_reply(
        _msg(body="booking proposal for our DJ"), {"categoria": "proposte", "confidenza": "alta"})
    assert ok


async def _run_process(monkeypatch, verdict, body="Apriamo alle 23."):
    from mail import responder
    azioni = []

    async def _get(_id):
        return _msg(body="Hi, our DJ is available for bookings in 2027, are you interested?")

    async def _no(*a, **k):
        return False

    async def _yes(*a, **k):
        return True

    async def _verdict(_m):
        return verdict

    async def _compose(_m, _v):
        return body

    async def _send(msg, text):
        azioni.append(("send", text))

    async def _draft(msg, text):
        azioni.append(("draft", text))
        return "d1"

    async def _nop(*a, **k):
        pass

    monkeypatch.setattr("mail.gmail_client.get_message", _get)
    monkeypatch.setattr("mail.gmail_client.thread_has_our_reply", _no)
    monkeypatch.setattr("mail.gmail_client.can_send_as", _yes)
    monkeypatch.setattr("mail.gmail_client.send_reply", _send)
    monkeypatch.setattr("mail.gmail_client.create_draft", _draft)
    monkeypatch.setattr("mail.gmail_client.add_label", _nop)
    monkeypatch.setattr(triage, "classify", _verdict)
    monkeypatch.setattr(responder, "_compose", _compose)
    monkeypatch.setattr(responder, "_notify", _nop)
    esito = await responder.process_one("m1")
    return esito, azioni


async def test_proposta_inviata_con_testo_fisso(monkeypatch):
    esito, azioni = await _run_process(
        monkeypatch, {"categoria": "proposte", "confidenza": "alta", "domanda": "booking"})
    assert esito == "inviata (proposta)"
    assert azioni[0][0] == "send" and "programming" in azioni[0][1]


def _decode(raw):
    import base64
    import email
    from email import policy
    return email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)


def test_firma_gmail_in_fondo_alla_risposta():
    """La firma è quella impostata in Gmail per george@: le API non la mettono da sole."""
    from mail import gmail_client as gm
    sig = '<div><b>George</b><br>Gate Milano<br><a href="https://gatemilano.it">gatemilano.it</a></div>'
    m = _decode(gm._build_raw("a@b.c", "Orari", "Ciao,\napriamo alle 23.", "<x@y>", "", sig))
    html_part = m.get_body(preferencelist=("html",)).get_content()
    text_part = m.get_body(preferencelist=("plain",)).get_content()
    assert sig in html_part                      # firma intatta, con link
    assert "apriamo alle 23." in html_part
    assert "George" in text_part and "<b>" not in text_part
    assert text_part.index("apriamo") < text_part.index("George")


def test_senza_firma_gmail_chiude_col_nome():
    from mail import gmail_client as gm
    m = _decode(gm._build_raw("a@b.c", "Orari", "Ciao", "<x@y>", ""))
    assert m.get_content().rstrip().endswith("Gate Milano")


def test_testo_del_cliente_non_diventa_html():
    from mail import gmail_client as gm
    m = _decode(gm._build_raw("a@b.c", "x", "<script>x</script>", "", "", "<p>Sig</p>"))
    assert "<script>" not in m.get_body(preferencelist=("html",)).get_content()


def test_template_senza_firma_propria():
    # Niente "Gate Milano" nel testo fisso: la firma arriva da Gmail, non va doppia.
    from mail.templates import _PROPOSAL
    for t in _PROPOSAL.values():
        assert "Gate Milano" not in t
        assert not t.rstrip().endswith(("saluti", "regards", "saludo", "Cordialement", "Grüßen"))
