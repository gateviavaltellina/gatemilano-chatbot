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
