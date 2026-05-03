import re
from datetime import datetime, timezone, timedelta, date as _date

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


def _next_weekday(now: datetime, target_weekday: int, force_next: bool = False) -> datetime:
    days = (target_weekday - now.weekday()) % 7
    if force_next and days == 0:
        days = 7
    return now + timedelta(days=days)


def extract_query_dates(text: str) -> list[str]:
    now = datetime.now(timezone.utc)
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
