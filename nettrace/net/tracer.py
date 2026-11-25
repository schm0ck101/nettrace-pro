"""Moteur de traceroute continu (principe PingPlotter).

À chaque intervalle, une sonde ICMP est envoyée EN PARALLÈLE pour chaque TTL
de 1 à N (N = longueur connue de la route, ou ``max_hops``). Chaque routeur
intermédiaire répond « TTL expiré », la destination répond « Echo reply ».
On obtient ainsi, à chaque cycle, la latence et les pertes de chaque saut.

* Les cycles se chevauchent si le délai d'attente est plus long que
  l'intervalle : un cycle lent ne retarde jamais les suivants.
* Les résultats sont validés dans l'ordre chronologique.
* Un routeur qui ne répond pas à l'ICMP est simplement marqué « perdu »
  pour ce saut : la mesure continue sur les sauts suivants.
* Mode « ping » : seule la destination est sondée.
"""
from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from ..events import ERROR, INFO, WARNING, NetEvent
from . import icmp
from .alerts import LEVEL_CRIT, LEVEL_OK, AlertMonitor
from .dns import ResolveError, clean_host, ip_version, resolve, reverse_dns
from .stats import NOT_PROBED, SampleBuffer

log = logging.getLogger(__name__)

# Pool partagé par toutes les sessions (les appels ICMP relâchent le GIL).
_POOL = ThreadPoolExecutor(max_workers=256, thread_name_prefix="icmp")
_ids = itertools.count(1)

STATE_LABEL = {"resolving": "Résolution DNS…", "running": "En cours",
               "paused": "En pause", "stopped": "Arrêtée", "error": "Erreur",
               "finished": "Terminée"}


@dataclass
class TraceOptions:
    host: str
    name: str = ""
    interval: float = 1.0
    timeout_ms: int = 2000
    packet_size: int = 32
    max_hops: int = 30
    family: str = "auto"
    mode: str = "trace"          # trace | ping
    reverse_dns: bool = True
    count: int = 0               # 0 = surveillance continue
    memory_samples: int = 43200


@dataclass
class HopInfo:
    ttl: int
    ip: str | None = None
    hostname: str | None = None
    ips_seen: set = field(default_factory=set)
    last_status: int | None = None
    is_destination: bool = False


class _Cycle:
    __slots__ = ("ts", "results", "remaining", "ttls")

    def __init__(self, ts: float, ttls: list[int]):
        self.ts = ts
        self.ttls = ttls
        self.results: list[icmp.ProbeResult | None] = [None] * len(ttls)
        self.remaining = len(ttls)


class TraceSession:
    """Une destination surveillée. Toutes les méthodes publiques sont
    thread‑safe ; les callbacks sont appelés depuis des threads de travail."""

    def __init__(self, options: TraceOptions, thresholds: dict,
                 on_event=None, on_sample=None, on_hop=None):
        self.uid = next(_ids)
        self.opt = options
        self.opt.host = clean_host(options.host)
        self.name = options.name or self.opt.host
        self.on_event = on_event or (lambda ev: None)
        self.on_sample = on_sample or (lambda *a: None)
        self.on_hop = on_hop or (lambda *a: None)
        self.db_id: int | None = None

        self.state = "stopped"
        self.error: str | None = None
        self.target_ip: str | None = None
        self.addresses: list[str] = []
        self.started_at: float | None = None
        self.ended_at: float | None = None

        cols = self.opt.max_hops if self.opt.mode == "trace" else 1
        self.buffer = SampleBuffer(self.opt.memory_samples, cols)
        self.hops: list[HopInfo] = [HopInfo(i + 1) for i in range(cols)]
        self.route_len: int | None = None      # TTL de la destination
        self.max_seen_ttl = 0                  # plus grand TTL ayant répondu
        self.alerts = AlertMonitor(thresholds, self._emit, self.name)

        self._lock = threading.RLock()
        self._pending: deque[_Cycle] = deque()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._paused = False
        self._thread: threading.Thread | None = None
        self._route_events = deque(maxlen=20)  # anti‑rafale changements de route

    # ------------------------------------------------------------------ API
    @property
    def level(self) -> int:
        if self.state == "error":
            return LEVEL_CRIT
        return self.alerts.level if self.state in ("running", "paused") else LEVEL_OK

    @property
    def status_text(self) -> str:
        if self.state == "running":
            return self.alerts.status_text
        if self.state == "error":
            return self.error or "Erreur"
        return STATE_LABEL.get(self.state, self.state)

    @property
    def family_label(self) -> str:
        if not self.target_ip:
            return "—"
        return f"IPv{ip_version(self.target_ip)}"

    def display_hops(self) -> list[HopInfo]:
        """Sauts à afficher : jusqu'à la destination si elle est connue,
        sinon jusqu'au dernier saut ayant répondu (+1 pour montrer « * »)."""
        with self._lock:
            if self.opt.mode == "ping":
                return self.hops[:1]
            n = self.route_len or min(self.max_seen_ttl + 1, self.opt.max_hops)
            return list(self.hops[:max(n, 1)])

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._paused = False
        self.state = "resolving"
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run, name=f"trace-{self.uid}",
                                        daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if self.state == "running":
            self._paused = True
            self.state = "paused"
            self._emit(NetEvent(INFO, "session", "Mesures mises en pause", self.name, self.db_id))

    def resume(self) -> None:
        if self.state == "paused":
            self._paused = False
            self.state = "running"
            self._wake.set()
            self._emit(NetEvent(INFO, "session", "Mesures reprises", self.name, self.db_id))

    def stop(self, wait: bool = False) -> None:
        self._stop.set()
        self._wake.set()
        if wait and self._thread:
            self._thread.join(timeout=3)

    def set_interval(self, interval: float) -> None:
        self.opt.interval = max(0.1, float(interval))
        self._wake.set()

    def set_thresholds(self, th: dict) -> None:
        self.alerts.set_thresholds(th)

    # ------------------------------------------------------------- interne
    def _emit(self, ev: NetEvent) -> None:
        ev.session_id = self.db_id
        try:
            self.on_event(ev)
        except Exception:
            log.exception("Callback événement")

    def _resolve_loop(self) -> bool:
        """Résout la destination, réessaie toutes les 10 s si échec."""
        warned = False
        while not self._stop.is_set():
            try:
                self.addresses = resolve(self.opt.host, self.opt.family)
                self.target_ip = self.addresses[0]
                if self.target_ip != self.opt.host:
                    self._emit(NetEvent(INFO, "dns",
                                        f"{self.opt.host} résolu en {self.target_ip}",
                                        self.name))
                return True
            except ResolveError as exc:
                self.error = str(exc)
                self.state = "error"
                if not warned:
                    self._emit(NetEvent(ERROR, "dns", f"{exc} — nouvel essai toutes les 10 s",
                                        self.name))
                    warned = True
                self._stop.wait(10)
        return False

    def _run(self) -> None:
        try:
            if not self._resolve_loop():
                self._finish("stopped")
                return
            self.error = None
            self.state = "running"
            self._emit(NetEvent(INFO, "session",
                                f"Surveillance démarrée vers {self.target_ip} "
                                f"({'traceroute' if self.opt.mode == 'trace' else 'ping'}, "
                                f"intervalle {self.opt.interval:g} s)", self.name))
            next_t = time.monotonic()
            launched = 0
            while not self._stop.is_set():
                if self._paused:
                    self._wake.wait(0.5)
                    self._wake.clear()
                    next_t = time.monotonic()
                    continue
                # Protection : trop de cycles en attente (réseau saturé)
                max_pending = int(self.opt.timeout_ms / 1000 / self.opt.interval) + 4
                if len(self._pending) <= max_pending:
                    self._launch_cycle()
                    launched += 1
                if self.opt.count and launched >= self.opt.count:
                    self._drain(self.opt.timeout_ms / 1000 + 1)
                    self._finish("finished")
                    return
                next_t += self.opt.interval
                now = time.monotonic()
                if next_t < now:          # pas de rattrapage en rafale
                    next_t = now
                self._wake.wait(next_t - now)
                self._wake.clear()
            self._drain(min(self.opt.timeout_ms / 1000, 2.5))
            self._finish("stopped")
        except Exception as exc:  # ne doit jamais arriver : on journalise
            log.exception("Session %s", self.name)
            self.error = f"Erreur interne : {exc}"
            self._finish("error")

    def _drain(self, max_wait: float) -> None:
        t_end = time.monotonic() + max_wait
        while self._pending and time.monotonic() < t_end:
            time.sleep(0.05)

    def _finish(self, state: str) -> None:
        self.ended_at = time.time()
        self.state = state
        self._emit(NetEvent(INFO, "session",
                            "Session terminée" if state == "finished" else "Surveillance arrêtée",
                            self.name))

    def _launch_cycle(self) -> None:
        ip = self.target_ip
        if self.opt.mode == "ping":
            ttls = [128]
        else:
            ttls = list(range(1, (self.route_len or self.opt.max_hops) + 1))
        cyc = _Cycle(time.time(), ttls)
        with self._lock:
            self._pending.append(cyc)
        to, size = self.opt.timeout_ms, self.opt.packet_size
        for i, ttl in enumerate(ttls):
            try:
                fut = _POOL.submit(icmp.probe, ip, ttl, to, size)
            except RuntimeError:  # pool arrêté (fermeture)
                return
            fut.add_done_callback(lambda f, c=cyc, k=i: self._on_probe(c, k, f))

    def _on_probe(self, cyc: _Cycle, k: int, fut) -> None:
        try:
            res = fut.result()
        except Exception as exc:
            log.debug("Sonde en erreur : %s", exc)
            res = icmp.ProbeResult(icmp.IP_GENERAL_FAILURE, None, None)
        with self._lock:
            cyc.results[k] = res
            cyc.remaining -= 1
            # validation dans l'ordre chronologique
            while self._pending and self._pending[0].remaining == 0:
                self._commit(self._pending.popleft())

    def _commit(self, cyc: _Cycle) -> None:
        """Intègre un cycle complet (appelé avec le verrou)."""
        res = cyc.results
        if self.opt.mode == "ping":
            r = res[0]
            rtt = r.rtt if r.reached else np.nan
            hops = np.array([rtt], dtype=np.float32)
            self._update_hop(0, r, True)
            self.route_len = 1
            dest = rtt
        else:
            dest_idx = next((i for i, r in enumerate(res) if r.reached), None)
            limit = dest_idx + 1 if dest_idx is not None else len(res)
            hops = np.full(self.opt.max_hops, NOT_PROBED, dtype=np.float32)
            for i in range(limit):
                r = res[i]
                hops[i] = r.rtt if r.replied else np.nan
                self._update_hop(i, r, i == dest_idx)
            if dest_idx is not None:
                dest = res[dest_idx].rtt
                if self.route_len != limit:
                    old = self.route_len
                    self.route_len = limit
                    for h in self.hops[limit:]:
                        h.is_destination = False
                    if old is not None:
                        self._route_event(f"Longueur de la route modifiée : {old} → {limit} sauts")
            else:
                dest = np.nan
                # le dernier saut sondé est un routeur : la route s'est allongée
                if self.route_len is not None:
                    last = res[self.route_len - 1]
                    if last.replied and not last.reached:
                        self.route_len = None
        self.buffer.append(cyc.ts, dest, hops)
        try:
            self.on_sample(self, cyc.ts, dest, hops)
        except Exception:
            log.exception("Callback échantillon")
        ts, d, _ = self.buffer.arrays(last=max(60, int(self.alerts.th.get("alert_window", 10)) * 3))
        self.alerts.update(ts, d, self.opt.interval)

    def _update_hop(self, i: int, r: icmp.ProbeResult, is_dest: bool) -> None:
        h = self.hops[i]
        h.last_status = r.status
        h.is_destination = is_dest
        if not r.replied:
            return
        self.max_seen_ttl = max(self.max_seen_ttl, i + 1)
        if r.address != h.ip:
            new = r.address not in h.ips_seen
            if new and h.ips_seen and not is_dest:
                self._route_event(f"Saut {h.ttl} : nouvel équipement {r.address} "
                                  f"(précédent {h.ip})")
            h.ips_seen.add(r.address)
            h.ip = r.address
            h.hostname = None
            try:
                self.on_hop(self, h)
            except Exception:
                log.exception("Callback saut")
        if self.opt.reverse_dns and h.hostname is None:
            h.hostname = reverse_dns.lookup(h.ip)
            if h.hostname:
                try:
                    self.on_hop(self, h)
                except Exception:
                    log.exception("Callback saut")

    def _route_event(self, msg: str) -> None:
        now = time.time()
        while self._route_events and now - self._route_events[0] > 60:
            self._route_events.popleft()
        if len(self._route_events) < 5:        # 5 événements / minute max
            self._route_events.append(now)
            self._emit(NetEvent(WARNING, "route", msg, self.name))


def shutdown_pool() -> None:
    _POOL.shutdown(wait=False, cancel_futures=True)
