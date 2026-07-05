"""Drinklist condivisa tra canali: WhatsApp allega il PDF, Instagram (che non può
allegare documenti) invia il LINK come testo."""
from notifications.drinklist import (
    should_send_drinklist, drinklist_link_message, DRINKLISTS,
    should_send_drink_menu, drink_menu_link_message, DRINK_MENUS,
)


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


# --- Carta drink à la carte (prezzi singoli drink) ---

def test_drink_menu_link_contains_url():
    msg = drink_menu_link_message("gate_sardinia")
    assert msg and DRINK_MENUS["gate_sardinia"][0] in msg


def test_drink_menu_triggers_on_price_questions():
    for q in ["quanto costa un drink?", "mi mandi la carta drink?", "prezzi drink",
              "quanto costa un mojito", "avete un drink menu?"]:
        assert should_send_drink_menu("gate_sardinia", q.lower()), q


def test_drink_menu_only_sardinia():
    # per ora la carta drink è solo Sardegna
    assert not should_send_drink_menu("gate_milano", "quanto costa un drink")
    assert not should_send_drink_menu("gate_unknown", "carta drink")


def test_drink_menu_not_triggered_by_table_talk():
    assert not should_send_drink_menu("gate_sardinia", "vorrei un tavolo vip")


def test_price_question_does_not_trigger_bottle_list():
    # "quanto costa un drink" → carta drink, NON la bottle list dei tavoli
    assert should_send_drink_menu("gate_sardinia", "quanto costa un drink")
    assert not should_send_drinklist("gate_sardinia", "quanto costa un drink", "", already_sent=False)


def test_bottle_request_does_not_trigger_drink_menu():
    # richiesta bottiglie → bottle list, NON la carta drink
    assert should_send_drinklist("gate_sardinia", "mi giri il listino bottiglie?", "", already_sent=True)
    assert not should_send_drink_menu("gate_sardinia", "mi giri il listino bottiglie?")
