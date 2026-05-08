"""VIP table prices — single source of truth (prompt + calculations)."""

PERREO_TABLES = {
    "f_standard": {
        "label": "Zona F standard (F5-F21, F17bis)",
        "min_spend": 300,
        "max_people": 8,
        "extra_per_person": 35,
    },
    "f_premium": {
        "label": "Zona F premium (F1-F4)",
        "min_spend": 500,
        "max_people": 10,
        "extra_per_person": 50,
    },
    "b_balcony": {
        "label": "Zona B Balcony (B1-B5)",
        "min_spend": 300,
        "max_people": 8,
        "extra_per_person": 35,
    },
    "c_console": {
        "label": "Zona C Console (C1-C3)",
        "min_spend": 500,
        "max_people": 10,
        "extra_per_person": 50,
    },
}


def build_prices_text() -> str:
    lines = ['PREZZI TAVOLI (fissi — rispondi SEMPRE con questi valori, non dire mai che "varia"):']
    for t in PERREO_TABLES.values():
        lines.append(
            f"  {t['label']}: minimo €{t['min_spend']} per {t['max_people']} persone, "
            f"€{t['extra_per_person']} per ogni persona extra"
        )
    lines.append("  Ingresso INCLUSO nel tavolo. Il minimo è consumazione (bottiglie/drink).")
    return "\n".join(lines)
