"""Drinklist condivisa tra canali: WhatsApp allega il PDF, Instagram (che non può
allegare documenti) invia il LINK come testo."""
from notifications.drinklist import should_send_drinklist, drinklist_link_message, DRINKLISTS


def test_link_message_contains_url_for_known_venues():
    for venue in ("gate_milano", "gate_sardinia"):
        msg = drinklist_link_message(venue)
        assert msg and DRINKLISTS[venue][0] in msg


def test_link_message_none_for_unknown_venue():
    assert drinklist_link_message("gate_unknown") is None


def test_should_send_explicit_request_always():
    assert should_send_drinklist("gate_sardinia", "mi giri il listino bottiglie?", "", already_sent=True)


def test_should_send_implicit_trigger_once():
    assert should_send_drinklist("gate_sardinia", "vorrei un tavolo", "", already_sent=False)
    assert not should_send_drinklist("gate_sardinia", "vorrei un tavolo", "", already_sent=True)


def test_should_send_unrelated_no():
    assert not should_send_drinklist("gate_sardinia", "a che ora aprite?", "alle 22", already_sent=False)


def test_should_send_unknown_venue_no():
    assert not should_send_drinklist("gate_unknown", "mandami il listino", "", already_sent=False)
