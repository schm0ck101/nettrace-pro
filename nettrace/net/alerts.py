"""Détection d'anomalies sur la destination d'une session.

Machine à états par type d'anomalie (interruption, latence, pertes, jitter) :
un événement n'est émis qu'au CHANGEMENT d'état, avec une hystérésis de 10 %
pour éviter les rafales d'alertes autour d'un seuil.
"""
from __future__ import annotations

import time

import numpy as np

from ..events import ERROR, INFO, SUCCESS, WARNING, NetEvent
from .stats import consecutive_losses, series_stats

# Niveaux d'état d'une connexion (utilisés par les couleurs de l'interface)
LEVEL_OK, LEVEL_WARN, LEVEL_BAD, LEVEL_CRIT = 0, 1, 2, 3
LEVEL_NAMES = {LEVEL_OK: "Normal", LEVEL_WARN: "Latence élevée",
               LEVEL_BAD: "Forte dégradation", LEVEL_CRIT: "Problème important"}


def latency_level(ms: float, th: dict) -> int:
    if ms is None or np.isnan(ms):
        return LEVEL_OK
    if ms >= th["latency_crit"]:
        return LEVEL_CRIT
    if ms >= th["latency_bad"]:
        return LEVEL_BAD
    if ms >= th["latency_warn"]:
        return LEVEL_WARN
    return LEVEL_OK


def loss_level(pct: float, th: dict) -> int:
    if pct >= th["loss_crit"]:
        return LEVEL_CRIT
    if pct >= th["loss_warn"]:
        return LEVEL_BAD
    if pct > 0:
        return LEVEL_WARN
    return LEVEL_OK


class AlertMonitor:
    def __init__(self, thresholds: dict, emit, target: str):
        self.th = dict(thresholds)
        self.emit = emit
        self.target = target
        self.session_id: int | None = None
        self.in_outage = False
        self.outage_start = 0.0
        self.lat_level = LEVEL_OK
        self.loss_level = LEVEL_OK
        self.jitter_high = False
        self.level = LEVEL_OK         # niveau global courant
        self.status_text = "En attente"

    def set_thresholds(self, th: dict) -> None:
        self.th = dict(th)

    def _ev(self, level, cat, msg):
        self.emit(NetEvent(level, cat, msg, self.target, self.session_id))

    def update(self, ts: np.ndarray, dest: np.ndarray, interval: float) -> None:
        """``ts``/``dest`` : derniers échantillons de la destination."""
        th = self.th
        win = max(3, int(th.get("alert_window", 10)))
        consec = consecutive_losses(dest)

        # --- Interruption de connexion --------------------------------
        if consec >= int(th.get("outage_count", 3)):
            if not self.in_outage:
                self.in_outage = True
                self.outage_start = float(ts[-consec]) if consec <= len(ts) else time.time()
                self._ev(ERROR, "outage",
                         f"Interruption de connexion : {consec} paquets consécutifs perdus")
        elif self.in_outage and consec == 0:
            self.in_outage = False
            dur = max(0.0, float(ts[-1]) - self.outage_start)
            self._ev(SUCCESS, "outage", f"Connexion rétablie après {dur:.0f} s d'interruption")

        recent = dest[-win:]
        st = series_stats(recent)

        if not self.in_outage:
            # --- Latence (moyenne glissante) --------------------------
            lvl = latency_level(st.avg, th)
            if lvl < self.lat_level:
                # hystérésis : il faut repasser 10 % sous le seuil
                keys = {LEVEL_WARN: "latency_warn", LEVEL_BAD: "latency_bad",
                        LEVEL_CRIT: "latency_crit"}
                if not np.isnan(st.avg) and st.avg > th[keys[self.lat_level]] * 0.9:
                    lvl = self.lat_level
            if lvl != self.lat_level:
                if lvl > self.lat_level:
                    self._ev(WARNING if lvl < LEVEL_CRIT else ERROR, "latency",
                             f"{LEVEL_NAMES[lvl]} : latence moyenne {st.avg:.0f} ms")
                else:
                    self._ev(SUCCESS if lvl == LEVEL_OK else INFO, "latency",
                             f"Latence revenue à {st.avg:.0f} ms ({LEVEL_NAMES[lvl].lower()})")
                self.lat_level = lvl

            # --- Pertes (fenêtre plus longue, >= 2 pertes) -------------
            lwin = dest[-max(win * 3, 30):]
            lst = series_stats(lwin)
            pct = lst.loss_pct if (lst.lost >= 2 and lst.sent >= 10) else 0.0
            llvl = loss_level(pct, th)
            llvl = LEVEL_OK if llvl == LEVEL_WARN else llvl  # perte isolée : pas d'alerte
            if llvl != self.loss_level:
                if llvl > self.loss_level:
                    self._ev(WARNING if llvl < LEVEL_CRIT else ERROR, "loss",
                             f"Pertes de paquets : {pct:.1f} % sur les {lst.sent} derniers échantillons")
                else:
                    self._ev(SUCCESS, "loss", "Pertes de paquets revenues à la normale")
                self.loss_level = llvl

            # --- Jitter -------------------------------------------------
            jw = float(th.get("jitter_warn", 30))
            if not np.isnan(st.jitter):
                if not self.jitter_high and st.jitter >= jw:
                    self.jitter_high = True
                    self._ev(WARNING, "jitter", f"Jitter élevé : {st.jitter:.1f} ms")
                elif self.jitter_high and st.jitter < jw * 0.9:
                    self.jitter_high = False
                    self._ev(SUCCESS, "jitter", f"Jitter revenu à {st.jitter:.1f} ms")

        # --- Niveau global ----------------------------------------------
        if self.in_outage:
            self.level, self.status_text = LEVEL_CRIT, "Interruption"
        else:
            lvl = max(self.lat_level, self.loss_level,
                      LEVEL_WARN if self.jitter_high else LEVEL_OK)
            if lvl == LEVEL_OK and st.received == 0 and st.sent:
                lvl = LEVEL_CRIT
            self.level = lvl
            if lvl == LEVEL_OK:
                self.status_text = "Connexion normale"
            elif self.loss_level >= lvl and self.loss_level > LEVEL_OK:
                self.status_text = "Pertes de paquets"
            elif self.lat_level >= lvl and self.lat_level > LEVEL_OK:
                self.status_text = LEVEL_NAMES[self.lat_level]
            elif self.jitter_high:
                self.status_text = "Jitter élevé"
            else:
                self.status_text = LEVEL_NAMES[lvl]
