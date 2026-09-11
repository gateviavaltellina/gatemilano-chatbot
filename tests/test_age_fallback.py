"""Età minima: a Gate Milano la soglia di BASE è 18+, il 16+ è l'eccezione.

Conferma staff 11/9. Prima di questa correzione la knowledge base di Milano riportava
per errore la policy di Gate Sardinia ("ingresso consentito dai 16 anni con documento",
"under 16 ammessi se accompagnati da un genitore"): con il campo minAge vuoto su Sanity
il bot ripiegava su quella regola e diceva ai sedicenni che potevano entrare a Milano —
gente che compra il biglietto, si muove, e viene respinta all'ingresso.

Ora il fallback è scritto nella scheda dell'evento, non dedotto: campo vuoto = 18+.
A Gate Sardinia il comportamento NON cambia, perché là il 16+ è davvero la regola.
"""
from sync.sanity_sync import _build_document


def _doc(minAge=None, venue="Gate Milano"):
    event = {
        "title": "Serata Test",
        "date": "2026-10-10T21:00:00.000Z",
        "slug": {"current": "serata-test"},
    }
    if minAge is not None:
        event["minAge"] = minAge
    doc, _meta = _build_document(event, venue)
    return doc


def test_milano_campo_vuoto_e_18():
    # Il caso che ha causato il problema: nessun minAge su Sanity.
    assert "Età minima: 18+ (documento obbligatorio)" in _doc()


def test_milano_campo_stringa_vuota_e_18():
    assert "Età minima: 18+" in _doc(minAge="")


def test_milano_16_esplicito_vince():
    # Il 16+ resta possibile, ma solo marcato esplicitamente sulla serata.
    doc = _doc(minAge=16)
    assert "Età minima: 16+" in doc
    assert "18+" not in doc.split("Età minima:")[1].split("\n")[0]


def test_milano_18_esplicito_invariato():
    assert "Età minima: 18+" in _doc(minAge=18)


def test_milano_accetta_stringa():
    assert "Età minima: 16+" in _doc(minAge="16+")


def test_sardegna_campo_vuoto_non_forza_18():
    # A Gate Sardinia la regola della casa è 16+: il fallback di Milano non va applicato,
    # altrimenti negheremmo l'ingresso a chi ne ha diritto.
    assert "Età minima:" not in _doc(venue="Gate Sardinia")


def test_sardegna_valore_esplicito_rispettato():
    assert "Età minima: 16+" in _doc(minAge=16, venue="Gate Sardinia")


def test_kb_milano_non_contiene_piu_la_policy_sardegna():
    """La KB di Milano non deve più promettere l'ingresso dai 16 come regola generale."""
    import pathlib
    kb = (pathlib.Path(__file__).resolve().parent.parent
          / "rag" / "knowledge" / "gate_milano.md").read_text(encoding="utf-8")
    assert "ingresso consentito dai 16 anni con documento" not in kb
    assert "Under 16: ammessi solo se accompagnati da un genitore" not in kb
    # e deve dichiarare la soglia di base
    assert "18+ — è la soglia DI BASE" in kb
