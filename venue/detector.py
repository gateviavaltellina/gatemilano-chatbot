import re
from typing import Optional

MILANO_KEYWORDS = [
    "milano", "milan", "valtellina", "gate milano", "gate milan",
    "gatemilano", "milano club", "club milano",
]
SARDINIA_KEYWORDS = [
    "sardinia", "sardegna", "sarda", "sardo", "sardinian",
    "budoni", "gate sardinia", "gatesardinia", "sardinia club",
]

class VenueDetector:
    def detect(
        self,
        message: str,
        current_venue: Optional[str],
        history: list[dict],
    ) -> Optional[str]:
        msg_lower = message.lower()

        # Controlla risposta diretta a domanda di venue
        if any(k in msg_lower for k in ["milano", "milan"]):
            return "gate_milano"
        if any(k in msg_lower for k in ["sardinia", "sardegna", "sardinia", "sarda"]):
            return "gate_sardinia"

        # Check keyword più ampie
        for kw in MILANO_KEYWORDS:
            if kw in msg_lower:
                return "gate_milano"
        for kw in SARDINIA_KEYWORDS:
            if kw in msg_lower:
                return "gate_sardinia"

        # Usa venue dalla conversazione corrente
        if current_venue:
            return current_venue

        # Cerca nella history recente
        for turn in reversed(history[-6:]):
            content = turn.get("content", "").lower()
            for kw in MILANO_KEYWORDS:
                if kw in content:
                    return "gate_milano"
            for kw in SARDINIA_KEYWORDS:
                if kw in content:
                    return "gate_sardinia"

        return None  # Ambiguo
