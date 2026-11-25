"""Stockage en mémoire des mesures et calculs statistiques (numpy).

Chaque « échantillon » (cycle) contient, pour un instant donné, le temps de
réponse de chaque saut et celui de la destination. Codage des valeurs :

    >= 0   temps de réponse en ms
    NaN    sonde envoyée mais perdue (pas de réponse)
    -1     saut non sondé lors de ce cycle (au‑delà de la destination, pause…)

Le tampon a une capacité bornée (``capacity``) : la mémoire reste constante
même après des heures de surveillance ; l'historique complet est en SQLite.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

NOT_PROBED = -1.0


class SampleBuffer:
    """Tampon circulaire « double capacité » : les données restent toujours
    contiguës (vues numpy sans copie) avec un coût d'ajout amorti O(1)."""

    def __init__(self, capacity: int, columns: int):
        self.capacity = max(100, int(capacity))
        self.columns = int(columns)
        self._ts = np.zeros(2 * self.capacity, dtype=np.float64)
        self._dest = np.full(2 * self.capacity, np.nan, dtype=np.float32)
        self._hops = np.full((2 * self.capacity, self.columns), NOT_PROBED, dtype=np.float32)
        self._start = 0
        self._end = 0
        self.total = 0           # nombre total d'échantillons depuis le début
        self.lock = threading.RLock()

    def __len__(self) -> int:
        return self._end - self._start

    def append(self, ts: float, dest: float, hops: np.ndarray) -> None:
        with self.lock:
            if self._end >= 2 * self.capacity:
                # on ramène les `capacity - 1` derniers échantillons au début
                keep = self.capacity - 1
                s = self._end - keep
                self._ts[:keep] = self._ts[s:self._end]
                self._dest[:keep] = self._dest[s:self._end]
                self._hops[:keep] = self._hops[s:self._end]
                self._start, self._end = 0, keep
            i = self._end
            self._ts[i] = ts
            self._dest[i] = dest
            n = min(len(hops), self.columns)
            self._hops[i, :] = NOT_PROBED
            self._hops[i, :n] = hops[:n]
            self._end += 1
            if self._end - self._start > self.capacity:
                self._start = self._end - self.capacity
            self.total += 1

    def arrays(self, last: int = 0, copy: bool = True):
        """Renvoie (ts, dest, hops) pour les ``last`` derniers échantillons
        (0 = tout). Copie par défaut pour pouvoir lâcher le verrou."""
        with self.lock:
            s = self._start if last <= 0 else max(self._start, self._end - last)
            sl = slice(s, self._end)
            ts, dest, hops = self._ts[sl], self._dest[sl], self._hops[sl]
            if copy:
                return ts.copy(), dest.copy(), hops.copy()
            return ts, dest, hops

    def since(self, t0: float):
        """Échantillons dont l'horodatage est >= t0."""
        with self.lock:
            ts = self._ts[self._start:self._end]
            i = int(np.searchsorted(ts, t0, side="left"))
            if i >= len(ts):
                return (np.empty(0), np.empty(0, np.float32),
                        np.empty((0, self.columns), np.float32))
            return self.arrays(last=len(ts) - i)

    @classmethod
    def from_arrays(cls, ts, dest, hops) -> "SampleBuffer":
        n = len(ts)
        cols = hops.shape[1] if hops.ndim == 2 and hops.shape[1] else 1
        b = cls(max(n, 100), cols)
        b._ts[:n] = ts
        b._dest[:n] = dest
        if n:
            b._hops[:n, :hops.shape[1]] = hops
        b._end = n
        b.total = n
        return b


@dataclass(slots=True)
class SeriesStats:
    """Statistiques d'une série de temps de réponse."""

    sent: int = 0
    received: int = 0
    min: float = float("nan")
    max: float = float("nan")
    avg: float = float("nan")
    jitter: float = float("nan")
    current: float = float("nan")   # dernière valeur (NaN si perdue)
    last_lost: bool = False

    @property
    def lost(self) -> int:
        return self.sent - self.received

    @property
    def loss_pct(self) -> float:
        return 100.0 * self.lost / self.sent if self.sent else 0.0


def series_stats(values: np.ndarray) -> SeriesStats:
    """Calcule les statistiques d'une colonne (codage décrit en tête)."""
    v = np.asarray(values, dtype=np.float64)
    probed = v[~(v == NOT_PROBED)]          # NaN != -1 -> conservé (perte)
    st = SeriesStats()
    st.sent = int(probed.size)
    if not st.sent:
        return st
    ok = probed[~np.isnan(probed)]
    st.received = int(ok.size)
    st.last_lost = bool(np.isnan(probed[-1]))
    st.current = float(probed[-1])
    if ok.size:
        st.min = float(ok.min())
        st.max = float(ok.max())
        st.avg = float(ok.mean())
        # Jitter = moyenne des écarts absolus entre réponses successives
        st.jitter = float(np.abs(np.diff(ok)).mean()) if ok.size > 1 else 0.0
    return st


def consecutive_losses(values: np.ndarray) -> int:
    """Nombre de pertes consécutives en fin de série."""
    v = np.asarray(values, dtype=np.float64)
    v = v[~(v == NOT_PROBED)]
    if not v.size:
        return 0
    ok = ~np.isnan(v)
    if ok.all():
        return 0
    idx = np.flatnonzero(ok)
    return int(v.size if not idx.size else v.size - 1 - idx[-1])


def rolling_jitter(values: np.ndarray, span: int = 16) -> np.ndarray:
    """Jitter glissant : moyenne des |écarts| entre réponses successives sur
    les ``span`` dernières réponses (esprit RFC 3550, entièrement vectorisé).
    NaN pour les échantillons perdus."""
    v = np.asarray(values, dtype=np.float64)
    out = np.full(v.shape, np.nan)
    idx = np.flatnonzero(~np.isnan(v) & (v >= 0))
    if idx.size < 2:
        if idx.size:
            out[idx] = 0.0
        return out
    d = np.abs(np.diff(v[idx]))
    c = np.concatenate(([0.0], np.cumsum(d)))
    k = np.arange(1, d.size + 1)
    lo = np.maximum(k - span, 0)
    jit = (c[k] - c[lo]) / (k - lo)
    out[idx[0]] = 0.0
    out[idx[1:]] = jit
    return out


def bucket_loss(ts: np.ndarray, values: np.ndarray, bucket_s: float):
    """Pourcentage de pertes par tranche de ``bucket_s`` secondes.
    Renvoie (centres des tranches, pourcentage)."""
    v = np.asarray(values, dtype=np.float64)
    mask = ~(v == NOT_PROBED)
    ts, v = ts[mask], v[mask]
    if not ts.size:
        return np.empty(0), np.empty(0)
    bucket_s = max(bucket_s, 0.5)
    idx = np.floor((ts - ts[0]) / bucket_s).astype(np.int64)
    n = int(idx[-1]) + 1
    sent = np.bincount(idx, minlength=n).astype(float)
    lost = np.bincount(idx, weights=np.isnan(v).astype(float), minlength=n)
    keep = sent > 0
    centers = ts[0] + (np.arange(n) + 0.5) * bucket_s
    return centers[keep], 100.0 * lost[keep] / sent[keep]
