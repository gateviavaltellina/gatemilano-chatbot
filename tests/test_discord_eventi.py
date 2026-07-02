"""Comando staff !eventi: mostra cosa ha il bot IN MEMORIA (store eventi), per
distinguere subito 'il sync non ha l'evento' da 'il bot risponde male'."""
import datetime

from rag import event_store as es
from rag.event_store import _today_start_utc
from notifications.discord_bot import parse_eventi_command, handle_eventi


def setup_function(_):
    es._store.clear()


def _seed(venue, name, days_ahead):
    ts = _today_start_utc() + days_ahead * 86400
    d = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%-d %B %Y")
    es.upsert_event(venue, f"ev-{name}", f"EVENTO: {name}\nData: {d}", {
        "type": "event", "event_name": name, "date_ts": ts,
    })


def test_parse():
    assert parse_eventi_command("!eventi") == ["gate_milano", "gate_sardinia"]
    assert parse_eventi_command("!eventi sardinia") == ["gate_sardinia"]
    assert parse_eventi_command("!eventi sardegna") == ["gate_sardinia"]
    assert parse_eventi_command("!eventi milano") == ["gate_milano"]
    assert parse_eventi_command("!eventi marte") == []
    assert parse_eventi_command("!regole") is None
    assert parse_eventi_command("ciao") is None


def test_handle_lists_events():
    _seed("gate_sardinia", "Perreo XL", 2)
    out = handle_eventi(["gate_sardinia"])
    assert "Gate Sardinia" in out
    assert "1 eventi in memoria" in out
    assert "Perreo XL" in out


def test_handle_empty_store_is_explicit():
    out = handle_eventi(["gate_sardinia"])
    assert "0 eventi in memoria" in out
    assert "nessun evento" in out


def test_handle_unknown_venue():
    assert handle_eventi([]).startswith("❌")


def test_handle_truncates_at_discord_limit():
    for i in range(80):
        _seed("gate_sardinia", f"Serata Lunghissima Numero {i:03d} con nome infinito", 1 + i % 13)
    out = handle_eventi(["gate_sardinia"])
    assert len(out) <= 2000
