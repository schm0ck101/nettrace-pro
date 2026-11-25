"""Diagnostic saut par saut, avec les règles d'interprétation de PingPlotter :

* Un routeur peut ignorer ou ralentir les réponses ICMP qui lui sont
  adressées (limitation de débit, faible priorité du plan de contrôle).
  Une perte ou une latence élevée sur un saut N'EST UN PROBLÈME QUE si elle
  se répercute sur les sauts suivants et sur la destination.
* Le saut où le problème APPARAÎT et se maintient jusqu'à la destination est
  celui qu'il faut signaler.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .alerts import LEVEL_BAD, LEVEL_CRIT, LEVEL_OK, LEVEL_WARN, latency_level, loss_level
from .stats import SeriesStats, series_stats

LEVEL_INFO = -1   # particularité sans impact (affichée en gris)


@dataclass
class HopRow:
    ttl: int
    ip: str | None
    hostname: str | None
    stats: SeriesStats
    level: int = LEVEL_OK
    state: str = ""
    is_destination: bool = False


def _nan(x: float) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def analyze(hops, focus: np.ndarray, long: np.ndarray | None, th: dict) -> list[HopRow]:
    """``hops`` : liste de HopInfo ; ``focus`` / ``long`` : matrices
    (échantillons × sauts) sur la fenêtre d'analyse et sur une fenêtre longue
    (détection des dégradations persistantes)."""
    rows: list[HopRow] = []
    stats = [series_stats(focus[:, h.ttl - 1]) if focus.size else SeriesStats() for h in hops]
    lstats = ([series_stats(long[:, h.ttl - 1]) for h in hops]
              if long is not None and long.size else None)

    for i, h in enumerate(hops):
        st = stats[i]
        row = HopRow(h.ttl, h.ip, h.hostname, st, is_destination=h.is_destination)
        down = [s for s in stats[i + 1:] if s.received > 0]
        last = i == len(hops) - 1

        if st.sent == 0:
            row.state = "En attente"
            rows.append(row)
            continue

        if st.received == 0:
            if down:
                row.level, row.state = LEVEL_INFO, "Ne répond pas à l'ICMP (sans impact)"
            else:
                row.level = LEVEL_CRIT
                row.state = "Saut qui ne répond plus" if h.ip else "Aucune réponse"
            rows.append(row)
            continue

        problems: list[tuple[int, str]] = []
        infos: list[str] = []

        # --- Pertes ---------------------------------------------------
        if st.loss_pct > 0 and st.lost >= 1:
            min_down = min((s.loss_pct for s in down), default=None)
            if min_down is not None and min_down < st.loss_pct * 0.5:
                infos.append(f"pertes ICMP non significatives ({st.loss_pct:.0f} %)")
            else:
                lvl = loss_level(st.loss_pct, th)
                problems.append((max(lvl, LEVEL_WARN), f"Perte de paquets {st.loss_pct:.1f} %"))

        # --- Latence --------------------------------------------------
        lvl = latency_level(st.avg, th)
        prev = next((s for s in reversed(stats[:i]) if s.received > 0), None)
        dest_avg = next((s.avg for s in reversed(stats) if s.received > 0), st.avg)
        persists = last or (not _nan(dest_avg) and dest_avg >= st.avg * 0.8)
        jump = (prev is not None and not _nan(prev.avg)
                and st.avg - prev.avg > max(30.0, prev.avg * 0.5))
        if lvl > LEVEL_OK or jump:
            if persists:
                if jump:
                    problems.append((max(lvl, LEVEL_WARN),
                                     f"Hausse de latence (+{st.avg - prev.avg:.0f} ms)"))
                else:
                    problems.append((lvl, "Latence élevée" if lvl < LEVEL_BAD else "Forte latence"))
            else:
                infos.append("réponse ICMP lente (sans impact)")

        # --- Jitter ---------------------------------------------------
        jw = float(th.get("jitter_warn", 30))
        if not _nan(st.jitter) and st.jitter >= jw:
            dest_jit = next((s.jitter for s in reversed(stats) if s.received > 1), st.jitter)
            if last or dest_jit >= jw * 0.7:
                problems.append((LEVEL_WARN, f"Jitter important ({st.jitter:.0f} ms)"))
            else:
                infos.append("jitter ICMP (sans impact)")

        if st.last_lost and h.is_destination:
            problems.append((LEVEL_BAD, "Dernière réponse perdue"))

        if problems:
            problems.sort(key=lambda p: -p[0])
            row.level = problems[0][0]
            row.state = " · ".join(p[1] for p in problems)
            # dégradation persistante : le problème existe aussi sur la longue durée
            if lstats is not None:
                ls = lstats[i]
                if (ls.sent >= 30 and (loss_level(ls.loss_pct, th) >= LEVEL_BAD
                                       or latency_level(ls.avg, th) >= LEVEL_WARN)):
                    row.state += " — dégradation persistante"
        elif infos:
            row.level, row.state = LEVEL_INFO, infos[0][0].upper() + infos[0][1:]
        else:
            row.state = "Destination OK" if h.is_destination else "OK"
        rows.append(row)
    return rows
