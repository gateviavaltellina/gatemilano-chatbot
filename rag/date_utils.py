from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta, date as _date
from zoneinfo import ZoneInfo

_ROME = ZoneInfo("Europe/Rome")

# Le serate vanno fino alle 05:00: tra mezzanotte e le 06:00 la notte del club è
# ancora quella iniziata la sera prima. Per date/eventi consideriamo quindi il
# "giorno di servizio", che ruota alle 06:00 e non a mezzanotte.
CLUB_NIGHT_ROLLOVER_HOUR = 6


def business_now(now: datetime | None = None) -> datetime:
    """Ora 'di servizio': prima delle 06:00 restituisce il giorno precedente,
    così la serata ancora in corso (es. iniziata alle 23 di ieri) conta come oggi."""
    now = now or datetime.now(_ROME)
    return now - timedelta(hours=CLUB_NIGHT_ROLLOVER_HOUR)


def format_current_datetime(now: datetime | None = None) -> str:
    """Stringa "DATA E ORA ATTUALE" per il prompt. Dopo mezzanotte e prima delle 06:00
    aggiunge la NOTTE CLUB IN CORSO (= la serata di ieri sera, ancora quella corrente):
    senza questo il bot, vedendo l'orologio già al giorno dopo, tratta l'evento di
    stanotte come "già passato" (caso reale: alle 00:08 diceva che il Perreo del sabato
    era finito, mentre si va avanti fino alle 03:00)."""
    now = now or datetime.now(_ROME)
    s = now.strftime("%A %-d %B %Y, %H:%M (Europe/Rome)")
    if now.hour < CLUB_NIGHT_ROLLOVER_HOUR:
        night = business_now(now)
        s += (
            f". NOTTE CLUB IN CORSO: la serata attuale è quella di "
            f"{night.strftime('%A %-d %B %Y')} — le notti del club ruotano alle 06:00, "
            f"non a mezzanotte. Un evento datato {night.strftime('%-d %B')} è la serata di "
            f"STANOTTE (ancora in corso o appena conclusa), NON 'già passato': controlla la "
            f"sua riga 'Orari:' per dire se è aperto proprio adesso."
        )
    return s


_TODAY_TERMS = ["stasera", "stanotte", "oggi", "questa sera", "questa notte", "tonight", "hoy", "esta noche"]
_TOMORROW_TERMS = ["domani", "domani sera", "domani notte", "tomorrow", "mañana", "manana"]
_WEEKEND_TERMS = ["weekend", "fine settimana", "fin de semana"]

_WEEKDAY_TERMS = {
    # Italian
    "lunedì": 0, "lunedi": 0,
    "martedì": 1, "martedi": 1,
    "mercoledì": 2, "mercoledi": 2,
    "giovedì": 3, "giovedi": 3,
    "venerdì": 4, "venerdi": 4,
    "sabato": 5,
    "domenica": 6,
    # English
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    # Spanish
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}

_IT_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

# Output italiano dei nomi di giorno/mese, INDIPENDENTE dal locale del server:
# strftime("%A"/"%B") darebbe l'inglese ("Sunday"/"July") sui container senza locale
# it_IT, con l'effetto che il bot indovinava il giorno della settimana e sbagliava.
_IT_WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_IT_MONTHS_BY_NUM = {num: name for name, num in _IT_MONTHS.items()}


def italian_weekday(d) -> str:
    """Nome italiano del giorno della settimana per una date/datetime."""
    return _IT_WEEKDAYS[d.weekday()]


def format_italian_date(d, with_year: bool = True) -> str:
    """Data in italiano col giorno della settimana, es. 'domenica 19 luglio 2026'.
    Il giorno è calcolato da Python (corretto), così il modello non deve dedurlo."""
    s = f"{italian_weekday(d)} {d.day} {_IT_MONTHS_BY_NUM[d.month]}"
    return f"{s} {d.year}" if with_year else s


def italian_month_year(year: int, month: int) -> str:
    """Es. 'luglio 2026' — per le intestazioni di mese, sempre in italiano."""
    return f"{_IT_MONTHS_BY_NUM[month]} {year}"


_EN_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
    # "may" escluso di proposito: troppo ambiguo ("you may", ecc.)
}
_ES_MONTHS = {
    "enero": 1, "febrero": 2, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    # marzo/agosto già in _IT_MONTHS (uguali in spagnolo)
}
_MONTH_NAMES = {**_IT_MONTHS, **_EN_MONTHS, **_ES_MONTHS}


def _edit_distance_le(a: str, b: str, max_d: int) -> bool:
    """True se la distanza di Levenshtein tra a e b è <= max_d (con early-exit)."""
    if abs(len(a) - len(b)) > max_d:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(v)
            row_best = min(row_best, v)
        if row_best > max_d:
            return False
        prev = cur
    return prev[-1] <= max_d


def extract_query_months(text: str, now: datetime | None = None) -> list[tuple[int, int]]:
    """Mesi citati per NOME nel messaggio (es. "e ad agosto?", "eventi a settembre",
    "in August"), come lista (anno, mese). Serve a mostrare gli eventi di un intero
    mese quando l'utente non dà un giorno preciso: senza questo il bot vede solo la
    finestra breve e dice "non ho eventi per agosto" pur avendo tutto il mese in Sanity.
    Tollera i typo di 1-2 lettere ("agosti", "settembr"). Anno: quest'anno se il mese
    non è già passato, altrimenti l'anno prossimo."""
    now = business_now(now)
    lower = text.lower()

    def _year(mnum: int) -> int:
        return now.year if mnum >= now.month else now.year + 1

    found: list[tuple[int, int]] = []
    for name, mnum in _MONTH_NAMES.items():
        if re.search(rf"\b{name}\b", lower):
            found.append((_year(mnum), mnum))
    # Pass fuzzy: token del messaggio a distanza 1-2 da un nome di mese (>=5 lettere).
    # Cattura i refusi comuni senza allargare troppo (soglia bassa, guardia di lunghezza).
    if not found:
        tokens = {t for t in re.findall(r"[a-zàèéìòù]+", lower) if len(t) >= 5}
        for name, mnum in _MONTH_NAMES.items():
            if len(name) < 5:
                continue
            max_d = 2 if len(name) >= 6 else 1
            if any(abs(len(t) - len(name)) <= 2 and _edit_distance_le(t, name, max_d)
                   for t in tokens):
                found.append((_year(mnum), mnum))
    return list(dict.fromkeys(found))


def _next_weekday(now: datetime, target_weekday: int, force_next: bool = False) -> datetime:
    days = (target_weekday - now.weekday()) % 7
    if force_next and days == 0:
        days = 7
    return now + timedelta(days=days)


def extract_query_dates(text: str, now: datetime | None = None) -> list[str]:
    now = business_now(now)
    lower = text.lower()
    dates = []

    if any(t in lower for t in _TODAY_TERMS):
        dates.append(now.strftime("%Y-%m-%d"))
    if any(t in lower for t in _TOMORROW_TERMS):
        dates.append((now + timedelta(days=1)).strftime("%Y-%m-%d"))

    # "prossimo X" / "next X" → salta alla settimana successiva se oggi è già quel giorno
    force_next = bool(re.search(r'\b(prossim\w*|next)\b', lower))

    # Weekend → sabato + domenica
    if any(t in lower for t in _WEEKEND_TERMS):
        sat = _next_weekday(now, 5, force_next)
        dates.append(sat.strftime("%Y-%m-%d"))
        dates.append((sat + timedelta(days=1)).strftime("%Y-%m-%d"))

    # Giorni della settimana specifici
    for term, weekday in _WEEKDAY_TERMS.items():
        if term in lower:
            dates.append(_next_weekday(now, weekday, force_next).strftime("%Y-%m-%d"))

    # Date esplicite italiane: "15 maggio", "il 15 maggio 2026"
    for month_name, month_num in _IT_MONTHS.items():
        pattern = rf"\b(\d{{1,2}})\s+{month_name}(?:\s+(\d{{4}}))?"
        for m in re.finditer(pattern, lower):
            day = int(m.group(1))
            year = int(m.group(2)) if m.group(2) else now.year
            try:
                d = _date(year, month_num, day)
                if d < now.date() and not m.group(2):
                    d = _date(year + 1, month_num, day)
                dates.append(d.strftime("%Y-%m-%d"))
            except ValueError:
                pass

    return list(dict.fromkeys(dates))
