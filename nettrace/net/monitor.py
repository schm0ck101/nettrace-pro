"""Gestionnaire des surveillances simultanées.

Crée les sessions à partir des paramètres, les relie à l'historique SQLite
et au bus d'événements, et applique les changements de réglages en direct.
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, Signal

from ..config import Settings
from ..events import NetEvent
from .tracer import TraceOptions, TraceSession

log = logging.getLogger(__name__)


class Monitor(QObject):
    sessions_changed = Signal()

    def __init__(self, settings: Settings, history, bus):
        super().__init__()
        self.settings = settings
        self.history = history          # HistoryDB ou None
        self.bus = bus
        self.sessions: list[TraceSession] = []

    # ---------------------------------------------------------------- API
    def options_for(self, host: str, **over) -> TraceOptions:
        m = self.settings.section("measure")
        opts = TraceOptions(
            host=host,
            interval=float(m["interval"]),
            timeout_ms=int(m["timeout_ms"]),
            packet_size=int(m["packet_size"]),
            max_hops=int(m["max_hops"]),
            family=m["family"],
            mode=m["mode"],
            reverse_dns=bool(m["reverse_dns"]),
            memory_samples=int(self.settings.get("history.memory_samples", 43200)),
        )
        for k, v in over.items():
            if v is not None and hasattr(opts, k):
                setattr(opts, k, v)
        return opts

    def start(self, host: str, **over) -> TraceSession:
        opts = self.options_for(host, **over)
        s = TraceSession(opts, self.settings.section("thresholds"),
                         on_event=self._on_event, on_sample=self._on_sample,
                         on_hop=self._on_hop)
        if self.history is not None and self.settings.get("history.record", True):
            try:
                s.db_id = self.history.create_session(
                    s.name, opts.host, None, opts.mode, opts.interval,
                    opts.max_hops, time.time())
                s.alerts.session_id = s.db_id
            except Exception:
                log.exception("Création de la session en historique")
        self.sessions.append(s)
        s.start()
        self.settings.add_recent(opts.host)
        self.sessions_changed.emit()
        return s

    def restart(self, s: TraceSession) -> TraceSession:
        """Relance une session arrêtée avec les mêmes options (nouvelle
        entrée d'historique)."""
        self.remove(s)
        o = s.opt
        return self.start(o.host, name=s.name, interval=o.interval, mode=o.mode,
                          family=o.family, timeout_ms=o.timeout_ms,
                          packet_size=o.packet_size, max_hops=o.max_hops, count=o.count)

    def stop(self, s: TraceSession) -> None:
        s.stop()
        if s.db_id is not None and self.history is not None:
            self.history.finalize(s.db_id)
        self.sessions_changed.emit()

    def remove(self, s: TraceSession) -> None:
        if s.state not in ("stopped", "finished") or s._thread and s._thread.is_alive():
            self.stop(s)
        if s in self.sessions:
            self.sessions.remove(s)
        self.sessions_changed.emit()

    def find(self, host: str) -> TraceSession | None:
        h = host.strip().lower()
        for s in self.sessions:
            if s.opt.host.lower() == h and s.state in ("running", "paused", "resolving", "error"):
                return s
        return None

    def apply_settings(self) -> None:
        th = self.settings.section("thresholds")
        for s in self.sessions:
            s.set_thresholds(th)
            s.opt.reverse_dns = bool(self.settings.get("measure.reverse_dns", True))

    def pause_all(self) -> None:
        for s in self.sessions:
            s.pause()

    def resume_all(self) -> None:
        for s in self.sessions:
            s.resume()

    def shutdown(self) -> None:
        for s in self.sessions:
            s.stop()
        for s in self.sessions:
            s.stop(wait=True)
            if s.db_id is not None and self.history is not None:
                self.history.finalize(s.db_id)

    # ---------------------------------------------------------- callbacks
    def _on_event(self, ev: NetEvent) -> None:
        if self.history is not None and self.settings.get("history.record", True):
            self.history.add_event(ev)
        self.bus.emit_event(ev)

    def _on_sample(self, s: TraceSession, ts, dest, hops) -> None:
        if s.db_id is None or self.history is None:
            return
        if not getattr(s, "_db_ip_saved", False):
            s._db_ip_saved = True
            self.history.update_ip(s.db_id, s.target_ip)
        self.history.add_sample(s.db_id, ts, dest, hops)

    def _on_hop(self, s: TraceSession, hop) -> None:
        if s.db_id is not None and self.history is not None:
            self.history.set_hop(s.db_id, hop.ttl, hop.ip, hop.hostname)
