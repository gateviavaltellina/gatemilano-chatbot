"""Traccia in memoria degli ultimi messaggi in arrivo e del loro esito.

Serve a diagnosticare da browser (/debug/last-messages) SE un messaggio arriva
al bot e cosa succede dopo, senza dover leggere i log o interpretare Discord:
- se il tuo DM di prova NON compare qui → problema di RICEZIONE (webhook Meta)
- se compare con esito 'inviata: NO' → problema di INVIO (token/permessi)
- se compare con 'takeover' → conversazione in mano allo staff (bot in pausa)
"""
from __future__ import annotations

import time
from collections import deque

_events: deque[dict] = deque(maxlen=60)


def record(channel: str, sender: str, text: str, stage: str, **extra) -> None:
    _events.append({
        "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "channel": channel,
        "sender": (sender or "")[-6:],   # solo coda id, per privacy
        "text": (text or "")[:160],
        "stage": stage,
        **extra,
    })


def recent() -> list[dict]:
    return list(reversed(_events))  # più recente in cima
