"""VIP table prices — single source of truth (prompt + calculations)."""

PERREO_TABLES = {
    "f_standard": {
        "label": "VIP Face standard (F6-F21)",
        "min_spend": 300,
        "max_people": 8,
        "extra_per_person": 35,
    },
    "f_premium": {
        "label": "VIP Face premium (F1-F5)",
        "min_spend": 600,
        "max_people": 10,
        "extra_per_person": 50,
    },
    "b_balcony": {
        "label": "VIP Balcony (B1-B5)",
        "min_spend": 300,
        "max_people": 8,
        "extra_per_person": 35,
    },
    "c_console": {
        "label": "Console (C1-C3)",
        "min_spend": 500,
        "max_people": 10,
        "extra_per_person": 50,
    },
}


def build_prices_text() -> str:
    lines = [
        'PREZZI TAVOLI (riferimento indicativo). La FONTE DI VERITÀ su prezzo e '
        'disponibilità è la mappa tavoli della singola serata: se nel contesto c\'è un '
        'blocco "TAVOLI VIP DISPONIBILI" con prezzi e link "Prenota:", USA QUEI prezzi '
        '(battono questa lista). Usa i valori qui sotto solo come riferimento generico '
        'quando NON hai la disponibilità live della serata nel contesto:'
    ]
    for t in PERREO_TABLES.values():
        lines.append(
            f"  {t['label']}: minimo €{t['min_spend']} per {t['max_people']} persone, "
            f"€{t['extra_per_person']} per ogni persona extra"
        )
    lines.append("  Ingresso INCLUSO nel tavolo. Il minimo è di bottiglie (non drink singoli).")
    lines.append("  Persone extra oltre il base: preferibile comunicarle in prenotazione (così il tavolo è preparato per il numero giusto); in alternativa il supplemento si paga al momento all'arrivo. Entrambe le vie valide.")
    return "\n".join(lines)
