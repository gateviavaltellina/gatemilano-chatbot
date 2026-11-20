from ai.claude_client import _sales_redirect_links


def test_sales_question_upsell_uses_our_site_not_provider():
    reply = "Non condividiamo dati di vendita. Biglietti: https://xceed.me/event/123"
    result = _sales_redirect_links(reply, "Quanti biglietti avete venduto?", "gate_milano")
    assert "xceed" not in result.lower()
    assert "https://gatemilano.it" in result


def test_purchase_question_keeps_direct_checkout():
    reply = "Prenota: https://booking-plugin.xceed.me/gate-milano/offer/123"
    assert _sales_redirect_links(reply, "Come compro due biglietti?", "gate_milano") == reply


def test_sales_report_does_not_mix_venue_sites():
    reply = "Eventi: https://xceed.me/event/123"
    assert _sales_redirect_links(reply, "Dati di vendita e incassi?", "gate_sardinia") == "Eventi: https://gatesardinia.it"


def test_sold_out_question_is_public_availability():
    reply = "Controlla qui: https://xceed.me/event/123"
    assert _sales_redirect_links(reply, "Avete già venduto tutti i biglietti, è sold out?", "gate_milano") == reply
