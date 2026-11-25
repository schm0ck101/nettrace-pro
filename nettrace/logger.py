"""Journalisation technique (fichier) et diffusion des événements réseau.

* ``setup_logging`` : fichier tournant ``nettrace.log`` dans le dossier données.
* ``EventBus`` : diffuse les événements (interruption, dégradation, retour à
  la normale…) vers l'interface (signal Qt, thread‑safe).
"""
from __future__ import annotations

import logging
import logging.handlers

from PySide6.QtCore import QObject, Signal

from .config import data_dir
from .events import ERROR, WARNING, NetEvent


def setup_logging(debug: bool = False) -> None:
    root = logging.getLogger()
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(
        data_dir() / "nettrace.log", maxBytes=2_000_000, backupCount=3,
        encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(threadName)s %(name)s : %(message)s"))
    root.addHandler(fh)


class EventBus(QObject):
    """Point central de diffusion. ``emit_event`` peut être appelé depuis
    n'importe quel thread : Qt achemine le signal vers le thread de l'UI."""

    event = Signal(object)  # NetEvent

    def __init__(self):
        super().__init__()
        self._log = logging.getLogger("events")

    def emit_event(self, ev: NetEvent) -> None:
        lvl = {ERROR: logging.ERROR, WARNING: logging.WARNING}.get(ev.level, logging.INFO)
        self._log.log(lvl, "[%s] %s : %s", ev.target, ev.category, ev.message)
        self.event.emit(ev)
