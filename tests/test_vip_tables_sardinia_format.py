"""Formattazione disponibilità tavoli Sardegna: quando ci sono molti tavoli liberi
il bot non deve ricevere 40 righe identiche, ma un riassunto per zona (conteggio +
fascia prezzi/coperti). Quando ne restano pochi, li elenca per nome così il bot può
indicarli al cliente."""
from rag.vip_tables import _format_sardinia_tables

URL = "https://www.gatesardinia.it/tavoli?event=ev1"


def _t(zona, code, coperti, price, stato="libero"):
    return {"zona": zona, "code": code, "coperti": coperti, "price": price, "stato": stato}


def _all_free_event():
    # 40 tavoli tutti liberi: 20 TERRAZZA + 20 VIP, metà da 6 (€300) metà da 10 (€600)
    out = []
    for zona in ("TERRAZZA", "VIP"):
        for i in range(1, 11):
            out.append(_t(zona, f"T{i}", 10, 600))
        for i in range(11, 21):
            out.append(_t(zona, f"T{i}", 6, 300))
    return out


def test_many_free_summarized_not_dumped():
    out = _format_sardinia_tables(_all_free_event(), URL)
    assert "TERRAZZA: 20 tavoli liberi" in out
    assert "VIP: 20 tavoli liberi" in out
    # fascia prezzi/coperti presente
    assert "€300 (6 persone)" in out
    assert "€600 (10 persone)" in out
    # NON deve elencare i singoli tavoli quando sono tanti
    assert "T1 " not in out and "T15" not in out
    # niente muro di 40 righe
    assert out.count("\n") <= 4
    assert URL in out


def test_few_free_listed_by_name():
    tables = [_t("TERRAZZA", "T3", 6, 300), _t("TERRAZZA", "T7", 10, 600)]
    # resto venduto
    tables += [_t("TERRAZZA", f"T{i}", 6, 300, stato="venduto") for i in range(1, 3)]
    out = _format_sardinia_tables(tables, URL)
    assert "T3" in out and "T7" in out


def test_zone_sold_out_marked():
    tables = [_t("TERRAZZA", "T1", 10, 600, stato="libero")]
    tables += [_t("VIP", f"V{i}", 10, 600, stato="venduto") for i in range(1, 6)]
    out = _format_sardinia_tables(tables, URL)
    assert "VIP: esauriti" in out


def test_all_sold_out():
    tables = [_t("TERRAZZA", f"T{i}", 10, 600, stato="venduto") for i in range(1, 5)]
    out = _format_sardinia_tables(tables, URL)
    assert "esauriti" in out.lower()
    assert URL in out


def test_empty_returns_empty():
    assert _format_sardinia_tables([], URL) == ""
