"""Drinklist condivisa tra canali: WhatsApp allega il PDF, Instagram (che non può
allegare documenti) invia il LINK come testo."""
from notifications.drinklist import (
    should_send_drinklist, drinklist_link_message, DRINKLISTS,
    should_send_drink_menu, drink_menu_link_message, DRINK_MENUS,
    should_send_food_menu, food_menu_link_message, FOOD_MENUS,
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


def test_drink_menu_triggers_on_bot_reply_offering_it():
    # se è il BOT a offrire la carta drink nella risposta, il link parte comunque
    assert should_send_drink_menu("gate_sardinia", "avete cocktail?", "sì! ti mando la carta drink")
    # ma un reply che parla della bottle list NON deve attivare la carta drink
    assert not should_send_drink_menu("gate_sardinia", "tavoli?", "ti mando la drinklist dei tavoli")


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


# --- Menu Food Court (pizze, panini, ecc.) ---

def test_food_menu_link_contains_url():
    msg = food_menu_link_message("gate_sardinia")
    assert msg and FOOD_MENUS["gate_sardinia"][0] in msg


def test_food_menu_triggers_on_food_questions():
    for q in ["e il cibo?", "c'è da mangiare?", "quanto costa una pizza?",
              "avete panini?", "quanto viene un toast", "fate hot dog?"]:
        assert should_send_food_menu("gate_sardinia", q.lower()), q


def test_food_menu_triggers_on_bot_reply_offering_it():
    assert should_send_food_menu("gate_sardinia", "avete stuzzichini?", "sì, c'è il food court")


def test_food_menu_only_sardinia():
    assert not should_send_food_menu("gate_milano", "quanto costa una pizza")
    assert not should_send_food_menu("gate_unknown", "cibo")


def test_food_menu_not_triggered_by_unrelated():
    assert not should_send_food_menu("gate_sardinia", "a che ora aprite?", "alle 22")


def test_food_and_drink_menus_are_independent():
    # domanda sul cibo → SOLO food menu, non la carta drink né la bottle list
    assert should_send_food_menu("gate_sardinia", "quanto costa una pizza?")
    assert not should_send_drink_menu("gate_sardinia", "quanto costa una pizza?")
    assert not should_send_drinklist("gate_sardinia", "quanto costa una pizza?", "", already_sent=False)
    # domanda sui drink → NON il food menu
    assert not should_send_food_menu("gate_sardinia", "quanto costa un drink?")


# --- Falso positivo: nome evento nella risposta NON deve inviare la drinklist ---

def test_event_name_perreo_in_reply_does_not_send_drinklist():
    # caso reale 01:54: domanda sugli ARTISTI del 27/28/29 → la risposta nomina
    # "Perreo XL presents Bichota" → la drinklist NON deve partire.
    assert not should_send_drinklist(
        "gate_sardinia",
        "potevate mettere qualche artista anche il 27 28 29 luglio",
        "il 27 c'è joe vanditti, il 28 perreo xl presents bichota e il 29 akeem",
        already_sent=False,
    )


def test_vip_ticket_mention_in_reply_does_not_send_drinklist():
    # una risposta che nomina "ticket VIP" (biglietti, non tavoli) non manda la bottle list
    assert not should_send_drinklist(
        "gate_sardinia", "che biglietti ci sono?",
        "ci sono Posto Unico e ticket VIP per le aree Terrace e VIP", already_sent=False,
    )


def test_reply_promising_drinklist_sends_it():
    # se il bot OFFRE/promette la drinklist nella risposta, il link parte davvero
    assert should_send_drinklist(
        "gate_sardinia", "avete posti a sedere?",
        "sì! ti mando la drinklist così scegli le bottiglie", already_sent=False,
    )


def test_boilerplate_closing_does_not_send_drinklist():
    # la chiusura-tipo "Vuoi info su biglietti, tavoli o altro?" NON deve far partire
    # la drinklist (caso reale: allegata a "stasera c'è Fervo Fluxo?" e simili).
    assert not should_send_drinklist(
        "gate_sardinia", "stasera c'è l'evento fervo fluxo?",
        "sì, stasera c'è fervo fluxo dalle 22 alle 03! vuoi info su biglietti, tavoli o altro?",
        already_sent=False,
    )


def test_user_text_tavolo_still_sends_drinklist():
    # invariato: l'utente che nomina "tavolo" riceve la drinklist (una volta)
    assert should_send_drinklist("gate_sardinia", "vorrei un tavolo", "", already_sent=False)
    assert not should_send_drinklist("gate_sardinia", "vorrei un tavolo", "", already_sent=True)
