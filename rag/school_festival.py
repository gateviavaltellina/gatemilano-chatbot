"""Contesto temporale per l'unico evento MSF autorizzato; nessun lookup Gate."""
from datetime import datetime
from zoneinfo import ZoneInfo

_ROME = ZoneInfo("Europe/Rome")
_START = datetime(2026, 12, 22, 22, tzinfo=_ROME)
# Termine prudenziale per non promuovere una serata passata. NON è un orario
# di chiusura dichiarato al pubblico (non ancora verificato).
_ARCHIVE_AT = datetime(2026, 12, 23, 12, tzinfo=_ROME)


def build_context(now: datetime | None = None) -> str:
    now = (now or datetime.now(_ROME)).astimezone(_ROME)
    if now < _START:
        state = "Evento futuro: usa la data assoluta 22 dicembre 2026."
    elif now < _ARCHIVE_AT:
        state = "Siamo nella notte dell'evento: non garantire ingresso, biglietti o orario di chiusura."
    else:
        state = "Evento trascorso: non promuovere acquisti e non inventare prossime edizioni."
    return (
        "UNICO EVENTO: MILANO SCHOOL FESTIVAL – XMAS EDITION | PUCHO, "
        "22 dicembre 2026, apertura 22:00, Alcatraz Milano.\n" + state +
        "\nUsa la scheda dedicata. Disponibilità biglietti non verificata in tempo reale."
    )
