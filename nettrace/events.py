"""Événements réseau (indépendant de Qt : utilisable par le moteur)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# Niveaux (gravité croissante)
INFO, SUCCESS, WARNING, ERROR = "info", "success", "warning", "error"
LEVEL_LABEL = {INFO: "Information", SUCCESS: "Rétabli",
               WARNING: "Avertissement", ERROR: "Critique"}

# Catégories
CATEGORY_LABEL = {
    "session": "Session",
    "outage": "Interruption",
    "latency": "Latence",
    "loss": "Pertes",
    "jitter": "Jitter",
    "route": "Route",
    "dns": "DNS",
    "system": "Système",
}


@dataclass(slots=True)
class NetEvent:
    level: str
    category: str
    message: str
    target: str = ""
    session_id: int | None = None   # identifiant en base (historique)
    ts: float = field(default_factory=time.time)
