"""Configuration persistante de l'application (JSON dans %APPDATA%).

Toutes les valeurs ont une valeur par défaut : un fichier absent, partiel ou
corrompu ne bloque jamais le démarrage.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path

APP_NAME = "NetTrace Pro"
APP_VERSION = "1.0.0"
AUTHOR = "SCHMOCK"
AUTHOR_URL = "https://schmock.fr"

log = logging.getLogger(__name__)


def data_dir() -> Path:
    """Dossier des données utilisateur (paramètres, historique, journaux)."""
    base = os.environ.get("NETTRACE_DATA_DIR") or os.path.join(
        os.environ.get("APPDATA", str(Path.home())), "NetTracePro")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


INTERVALS = [0.5, 1.0, 2.0, 5.0, 10.0]          # secondes
FOCUS_CHOICES = [10, 50, 100, 200, 500, 0]       # 0 = tout
TIME_WINDOWS = [60, 300, 600, 1800, 3600, 6 * 3600, 24 * 3600, 0]  # 0 = tout

DEFAULTS: dict = {
    "measure": {
        "interval": 1.0,          # s entre deux échantillons
        "timeout_ms": 2000,       # délai d'attente d'une sonde
        "packet_size": 32,        # octets de données ICMP
        "max_hops": 30,
        "family": "auto",         # auto | ipv4 | ipv6
        "reverse_dns": True,
        "mode": "trace",          # trace | ping
        "focus": 100,             # échantillons pris en compte dans le tableau
    },
    "thresholds": {
        "latency_warn": 100.0,    # ms -> jaune
        "latency_bad": 200.0,     # ms -> orange
        "latency_crit": 400.0,    # ms -> rouge
        "loss_warn": 5.0,         # % -> orange
        "loss_crit": 15.0,        # % -> rouge
        "jitter_warn": 30.0,      # ms
        "outage_count": 3,        # pertes consécutives = interruption
        "alert_window": 10,       # échantillons pour l'évaluation des alertes
    },
    "ui": {
        "theme": "dark",          # dark | light
        "notifications": True,
        "tray_notifications": True,
        "minimize_to_tray": False,
        "time_window": 600,       # s affichées sur le graphique temporel
        "graph_scale_ms": 0,      # 0 = automatique
        "sound": False,
    },
    "history": {
        "record": True,
        "retention_days": 90,
        "memory_samples": 43200,  # échantillons gardés en mémoire par cible
    },
    "favorites": [
        {"name": "Google DNS", "host": "8.8.8.8"},
        {"name": "Cloudflare", "host": "1.1.1.1"},
        {"name": "Quad9", "host": "9.9.9.9"},
        {"name": "Google", "host": "www.google.com"},
    ],
    "recent": [],
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """Accès thread‑safe aux paramètres : ``settings.get('measure.interval')``."""

    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "settings.json")
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = _merge(DEFAULTS, raw)
        except Exception as exc:  # fichier corrompu : on repart des défauts
            log.warning("Paramètres illisibles (%s), valeurs par défaut", exc)
            self._data = copy.deepcopy(DEFAULTS)

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self.path)

    def get(self, key: str, default=None):
        with self._lock:
            node = self._data
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, key: str, value) -> None:
        with self._lock:
            node = self._data
            parts = key.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

    def section(self, name: str) -> dict:
        return self.get(name, {}) or {}

    def add_recent(self, host: str) -> None:
        with self._lock:
            rec = [h for h in self._data.get("recent", []) if h != host]
            rec.insert(0, host)
            self._data["recent"] = rec[:20]

    def reset(self) -> None:
        with self._lock:
            fav = self._data.get("favorites")
            self._data = copy.deepcopy(DEFAULTS)
            if fav:
                self._data["favorites"] = fav
