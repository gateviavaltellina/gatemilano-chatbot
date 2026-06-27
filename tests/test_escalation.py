"""Detector temi sensibili → alert staff. Copre in particolare il GAP self-harm
emerso dalle chat reali (caso wh-41: 'I wish I might not be alive on Friday' non
generava alcun alert perché non c'era nessuna keyword di autolesionismo)."""
from notifications.escalation import detect_sensitive


def _has(cats, needle):
    return any(needle in c for c in cats)


def test_selfharm_english_real_case():
    # caso reale dalle chat WhatsApp (wh-41): NON deve passare inosservato
    assert _has(detect_sensitive("Today I wish I might not be alive on Friday"), "Autoles")


def test_selfharm_english_variants():
    for t in [
        "I want to kill myself",
        "I want to die",
        "thinking about suicide",
        "I might hurt myself",
        "I don't want to live anymore",
    ]:
        assert detect_sensitive(t), f"non rilevato: {t!r}"


def test_selfharm_italian_variants():
    for t in [
        "a volte penso di farla finita",
        "non voglio più vivere",
        "voglio uccidermi",
        "ho pensieri di suicidio",
    ]:
        assert _has(detect_sensitive(t), "Autoles"), f"non rilevato: {t!r}"


def test_benign_message_not_flagged():
    assert detect_sensitive("a che ora aprite stasera?") == []
    assert detect_sensitive("posso pagare in contanti al tavolo?") == []


def test_existing_categories_still_work():
    assert _has(detect_sensitive("vorrei chiedere un rimborso"), "Rimbors")
    assert _has(detect_sensitive("vengo in sedia a rotelle, è accessibile?"), "Accessibil")
    assert _has(detect_sensitive("ho una forte allergia alle arachidi"), "Salute")
