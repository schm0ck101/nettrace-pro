"""Historique des sessions en SQLite (fichier ``history.db``).

* Les écritures passent par une file et un thread dédié qui les regroupe
  en transactions (quelques écritures par seconde quel que soit le nombre
  de cibles) : les mesures ne sont jamais ralenties par le disque.
* Mode WAL : l'interface peut lire l'historique pendant les écritures.
* Un échantillon = une ligne (horodatage, latence destination, BLOB des
  latences de chaque saut en float32).
"""
from __future__ import annotations

import contextlib
import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config import data_dir
from ..events import NetEvent
from ..net.stats import NOT_PROBED, SampleBuffer, series_stats

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, host TEXT, ip TEXT, mode TEXT, interval REAL, max_hops INTEGER,
    started REAL, ended REAL,
    samples INTEGER DEFAULT 0, avg REAL, min REAL, max REAL, loss REAL, jitter REAL,
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS samples(
    session_id INTEGER NOT NULL, ts REAL NOT NULL, dest REAL, hops BLOB
);
CREATE INDEX IF NOT EXISTS ix_samples ON samples(session_id, ts);
CREATE TABLE IF NOT EXISTS hops(
    session_id INTEGER NOT NULL, ttl INTEGER NOT NULL, ip TEXT, hostname TEXT,
    PRIMARY KEY(session_id, ttl)
);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, ts REAL,
    level TEXT, category TEXT, target TEXT, message TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
"""


@dataclass
class SessionRecord:
    id: int
    name: str
    host: str
    ip: str | None
    mode: str
    interval: float
    max_hops: int
    started: float
    ended: float | None
    samples: int
    avg: float | None
    min: float | None
    max: float | None
    loss: float | None
    jitter: float | None
    notes: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, (self.ended or time.time()) - self.started)


def _connect(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(path), timeout=15, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


class HistoryDB:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "history.db")
        self._w = _connect(self.path)
        self._w.executescript(SCHEMA)
        self._w.commit()
        self._wlock = threading.Lock()
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._writer, name="history-writer",
                                        daemon=True)
        self._thread.start()

    # ----------------------------------------------------------- écriture
    def create_session(self, name, host, ip, mode, interval, max_hops, started) -> int:
        with self._wlock:
            cur = self._w.execute(
                "INSERT INTO sessions(name,host,ip,mode,interval,max_hops,started) "
                "VALUES(?,?,?,?,?,?,?)", (name, host, ip, mode, interval, max_hops, started))
            self._w.commit()
            return int(cur.lastrowid)

    def add_sample(self, sid: int, ts: float, dest: float, hops: np.ndarray) -> None:
        h = np.asarray(hops, dtype=np.float32)
        valid = np.flatnonzero(h != NOT_PROBED)
        h = h[: valid[-1] + 1] if valid.size else h[:0]
        d = None if (dest is None or np.isnan(dest)) else float(dest)
        self._q.put(("sample", (sid, ts, d, h.tobytes())))

    def set_hop(self, sid: int, ttl: int, ip: str | None, hostname: str | None) -> None:
        self._q.put(("hop", (sid, ttl, ip, hostname)))

    def add_event(self, ev: NetEvent) -> None:
        self._q.put(("event", (ev.session_id, ev.ts, ev.level, ev.category,
                               ev.target, ev.message)))

    def update_ip(self, sid: int, ip: str) -> None:
        self._q.put(("ip", (ip, sid)))

    def finalize(self, sid: int, ended: float | None = None) -> None:
        self._q.put(("final", (sid, ended or time.time())))

    def flush(self, timeout: float = 5.0) -> None:
        """Attend que toutes les écritures en file soient sur disque."""
        done = threading.Event()
        self._q.put(("sync", done))
        done.wait(timeout)

    def _writer(self) -> None:
        while not self._stop.is_set() or not self._q.empty():
            try:
                first = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            batch = [first]
            t_end = time.monotonic() + 1.0      # regroupe ~1 s d'écritures
            while time.monotonic() < t_end and len(batch) < 5000:
                try:
                    batch.append(self._q.get(timeout=max(0.0, t_end - time.monotonic())))
                except queue.Empty:
                    break
                if batch[-1][0] == "sync":
                    break
            try:
                self._apply(batch)
            except Exception:
                log.exception("Écriture historique")

    def _apply(self, batch) -> None:
        samples = [b[1] for b in batch if b[0] == "sample"]
        syncs = []
        with self._wlock:
            if samples:
                self._w.executemany("INSERT INTO samples VALUES(?,?,?,?)", samples)
            for kind, args in batch:
                if kind == "hop":
                    self._w.execute("INSERT OR REPLACE INTO hops VALUES(?,?,?,?)", args)
                elif kind == "event":
                    self._w.execute("INSERT INTO events(session_id,ts,level,category,target,message)"
                                    " VALUES(?,?,?,?,?,?)", args)
                elif kind == "ip":
                    self._w.execute("UPDATE sessions SET ip=? WHERE id=?", args)
                elif kind == "final":
                    self._w.commit()
                    self._finalize(*args)
                elif kind == "sync":
                    syncs.append(args)
            self._w.commit()
        for ev in syncs:
            ev.set()

    def _finalize(self, sid: int, ended: float) -> None:
        rows = self._w.execute("SELECT dest FROM samples WHERE session_id=? ORDER BY ts",
                               (sid,)).fetchall()
        d = np.array([np.nan if r[0] is None else r[0] for r in rows], dtype=np.float64)
        st = series_stats(d)
        f = lambda x: None if x is None or np.isnan(x) else float(x)  # noqa: E731
        self._w.execute(
            "UPDATE sessions SET ended=?, samples=?, avg=?, min=?, max=?, loss=?, jitter=? "
            "WHERE id=?", (ended, st.sent, f(st.avg), f(st.min), f(st.max),
                           st.loss_pct, f(st.jitter), sid))

    # -------------------------------------------------------------- lecture
    @contextlib.contextmanager
    def _r(self):
        """Connexion de lecture éphémère (fermée en sortie de bloc)."""
        c = _connect(self.path)
        try:
            yield c
        finally:
            c.close()

    def list_sessions(self, text: str = "", since: float | None = None,
                      until: float | None = None) -> list[SessionRecord]:
        sql = "SELECT * FROM sessions WHERE 1=1"
        args: list = []
        if text:
            sql += " AND (name LIKE ? OR host LIKE ? OR ip LIKE ? OR notes LIKE ?)"
            args += [f"%{text}%"] * 4
        if since:
            sql += " AND COALESCE(ended, 1e18) >= ?"
            args.append(since)
        if until:
            sql += " AND started <= ?"
            args.append(until)
        sql += " ORDER BY started DESC"
        with self._r() as c:
            return [SessionRecord(*r) for r in c.execute(sql, args).fetchall()]

    def get_session(self, sid: int) -> SessionRecord | None:
        with self._r() as c:
            r = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return SessionRecord(*r) if r else None

    def load_samples(self, sid: int, t0: float | None = None, t1: float | None = None,
                     columns: int | None = None) -> SampleBuffer:
        sql = "SELECT ts, dest, hops FROM samples WHERE session_id=?"
        args: list = [sid]
        if t0 is not None:
            sql += " AND ts>=?"
            args.append(t0)
        if t1 is not None:
            sql += " AND ts<=?"
            args.append(t1)
        sql += " ORDER BY ts"
        with self._r() as c:
            rows = c.execute(sql, args).fetchall()
        n = len(rows)
        blobs = [np.frombuffer(r[2] or b"", dtype=np.float32) for r in rows]
        cols = max([columns or 1] + [b.size for b in blobs])
        ts = np.fromiter((r[0] for r in rows), dtype=np.float64, count=n)
        dest = np.fromiter((np.nan if r[1] is None else r[1] for r in rows),
                           dtype=np.float32, count=n)
        hops = np.full((n, cols), NOT_PROBED, dtype=np.float32)
        for i, b in enumerate(blobs):
            hops[i, :b.size] = b
        return SampleBuffer.from_arrays(ts, dest, hops)

    def load_hops(self, sid: int) -> list[tuple[int, str | None, str | None]]:
        with self._r() as c:
            return c.execute("SELECT ttl, ip, hostname FROM hops WHERE session_id=? ORDER BY ttl",
                             (sid,)).fetchall()

    def list_events(self, session_id: int | None = None, limit: int = 2000,
                    level: str | None = None, text: str = "") -> list[NetEvent]:
        sql = "SELECT level, category, message, target, session_id, ts FROM events WHERE 1=1"
        args: list = []
        if session_id is not None:
            sql += " AND session_id=?"
            args.append(session_id)
        if level:
            sql += " AND level=?"
            args.append(level)
        if text:
            sql += " AND (message LIKE ? OR target LIKE ?)"
            args += [f"%{text}%"] * 2
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._r() as c:
            return [NetEvent(*r) for r in c.execute(sql, args).fetchall()]

    def rename_session(self, sid: int, name: str, notes: str | None = None) -> None:
        with self._wlock:
            self._w.execute("UPDATE sessions SET name=? WHERE id=?", (name, sid))
            if notes is not None:
                self._w.execute("UPDATE sessions SET notes=? WHERE id=?", (notes, sid))
            self._w.commit()

    def delete_sessions(self, ids: list[int]) -> None:
        self.flush()
        with self._wlock:
            for sid in ids:
                for t in ("samples", "hops", "events"):
                    self._w.execute(f"DELETE FROM {t} WHERE session_id=?", (sid,))
                self._w.execute("DELETE FROM sessions WHERE id=?", (sid,))
            self._w.commit()

    def clear_events(self) -> None:
        with self._wlock:
            self._w.execute("DELETE FROM events")
            self._w.commit()

    def maintenance(self, retention_days: int) -> None:
        """Purge des vieilles sessions et réparation des sessions non
        clôturées (fermeture brutale de l'application)."""
        try:
            with self._wlock:
                open_ids = [r[0] for r in self._w.execute(
                    "SELECT id FROM sessions WHERE ended IS NULL").fetchall()]
            for sid in open_ids:
                with self._wlock:
                    last = self._w.execute("SELECT MAX(ts) FROM samples WHERE session_id=?",
                                           (sid,)).fetchone()[0]
                self.finalize(sid, last or time.time())
            if retention_days and retention_days > 0:
                limit = time.time() - retention_days * 86400
                with self._wlock:
                    old = [r[0] for r in self._w.execute(
                        "SELECT id FROM sessions WHERE COALESCE(ended, started) < ?",
                        (limit,)).fetchall()]
                    self._w.execute("DELETE FROM events WHERE ts < ?", (limit,))
                    self._w.commit()
                if old:
                    self.delete_sessions(old)
                    log.info("%d anciennes sessions purgées", len(old))
        except Exception:
            log.exception("Maintenance historique")

    def db_size(self) -> int:
        return sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*")
                   if p.exists())

    def close(self) -> None:
        self.flush()
        self._stop.set()
        self._thread.join(timeout=5)
        with self._wlock:
            self._w.close()
