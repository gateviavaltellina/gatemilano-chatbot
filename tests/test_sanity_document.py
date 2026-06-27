"""Il documento/metadata costruito da Sanity deve esporre la lineup (campo `artists`),
non solo il titolo. Senza, gli artisti presenti solo in lineup restano invisibili al
bot (caso reale Sardinia 10/7: titolo "Davide T", in lineup anche Kamelia/Dfifonte/Asci)."""
from sync.sanity_sync import _build_document


def test_artists_go_into_metadata():
    event = {
        "_id": "ev1", "title": "Davide T", "date": "2026-07-10T22:00:00Z",
        "artists": ["Davide T", "Kamelia", "Dfifonte", "Asci"],
    }
    _, meta = _build_document(event, "Gate Sardinia")
    assert meta["artists"] == ["Davide T", "Kamelia", "Dfifonte", "Asci"]


def test_lineup_appears_in_document():
    event = {
        "_id": "ev1", "title": "Davide T", "date": "2026-07-10T22:00:00Z",
        "artists": ["Davide T", "Kamelia", "Dfifonte", "Asci"],
    }
    doc, _ = _build_document(event, "Gate Sardinia")
    # tutti gli artisti devono comparire nel testo, così il bot può dire chi suona
    for a in ["Davide T", "Kamelia", "Dfifonte", "Asci"]:
        assert a in doc, f"{a!r} mancante nel documento"


def test_no_artists_field_is_safe():
    # eventi senza lineup (es. Milano) non devono rompersi né inquinare i metadata
    event = {"_id": "ev2", "title": "Solo Title", "date": "2026-07-10T22:00:00Z"}
    doc, meta = _build_document(event, "Gate Milano")
    assert "Solo Title" in doc
    assert not meta.get("artists")  # assente o vuoto, mai None spurio
