"""Il documento/metadata costruito da Sanity deve esporre la lineup (campo `artists`),
non solo il titolo. Senza, gli artisti presenti solo in lineup restano invisibili al
bot (caso reale Sardinia 10/7: titolo "Davide T", in lineup anche Kamelia/Dfifonte/Asci)."""
from datetime import datetime, timezone

from sync.sanity_sync import _build_document


def _ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def test_late_night_event_indexed_on_service_day():
    # Sardegna salva le date come "<giorno>T22:00:00Z" = 00:00 Rome del giorno DOPO.
    # La serata del 4 luglio NON deve finire indicizzata il 5 (caso reale Perreo XL:
    # utente chiede "questo sabato" → 4 luglio e il bot non trovava l'evento).
    event = {"_id": "ev", "title": "Perreo XL", "date": "2026-07-04T22:00:00Z"}
    doc, meta = _build_document(event, "Gate Sardinia")
    assert meta["date_ts"] == _ts(2026, 7, 4)
    # e la data mostrata al bot è il 4 luglio (col giorno della settimana in italiano),
    # non il 5 (né un fuorviante "ore 00:00")
    assert "sabato 4 luglio 2026" in doc
    assert "5 luglio" not in doc
    assert "ore 00:00" not in doc


def test_real_evening_start_keeps_its_day_and_time():
    # Evento con orario serale reale: giorno invariato, ora d'inizio mostrata.
    event = {"_id": "ev", "title": "Show", "date": "2026-07-04T20:00:00Z"}  # 22:00 Rome
    doc, meta = _build_document(event, "Gate Sardinia")
    assert meta["date_ts"] == _ts(2026, 7, 4)
    assert "sabato 4 luglio 2026, ore 22:00" in doc


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
