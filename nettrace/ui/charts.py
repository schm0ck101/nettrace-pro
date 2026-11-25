"""Graphiques temps réel (PyQtGraph).

PyQtGraph est choisi plutôt que Matplotlib : rendu OpenGL/Qt natif, des
dizaines de milliers de points rafraîchis plusieurs fois par seconde sans
bloquer l'interface, zoom/défilement à la souris intégrés.

* ``TimelineChart`` : latence dans le temps, couleur selon les seuils
  (vert/jaune/orange/rouge via un dégradé indexé sur la valeur), barres
  rouges pour les pertes, navigation dans l'historique.
* ``LossChart``     : pourcentage de pertes par tranche de temps.
* ``JitterChart``   : jitter glissant.
* ``HopCompareChart`` : latence moyenne / min / max et pertes par saut.
* ``Sparkline``     : mini‑graphique du tableau de bord.
"""
from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..net.alerts import latency_level, loss_level
from ..net.stats import NOT_PROBED, bucket_loss, rolling_jitter
from . import theme

pg.setConfigOptions(antialias=True, useNumba=False, foreground="#8a96a8")

MAX_POINTS = 4000    # au‑delà, décimation min/max (le rendu reste fidèle aux pics)


def _qc(hex_: str, alpha: int = 255) -> QColor:
    c = QColor(hex_)
    c.setAlpha(alpha)
    return c


def _style_plot(plot: pg.PlotItem, p: theme.Palette) -> None:
    for name in ("left", "bottom", "right"):
        ax = plot.getAxis(name)
        ax.setPen(pg.mkPen(p.border))
        ax.setTextPen(pg.mkPen(p.muted))
        f = QFont("Segoe UI")
        f.setPointSizeF(8.5)
        ax.setStyle(tickFont=f)
    plot.showGrid(x=True, y=True, alpha=0.25 if p.name == "dark" else 0.5)
    for name in ("left", "right"):
        plot.getAxis(name).enableAutoSIPrefix(False)


def set_bars(item: pg.BarGraphItem, x, height, width, colors) -> None:
    """Met à jour des barres colorées individuellement. Contourne un bug de
    pyqtgraph (UnboundLocalError) quand les listes de couleurs sont vides."""
    if len(colors) == 0:
        item.setOpts(x=[], height=[], width=width, brushes=None, pens=None,
                     brush=pg.mkBrush(None), pen=pg.mkPen(None))
        return
    item.setOpts(x=x, height=height, width=width, brushes=colors,
                 pens=[pg.mkPen(None)] * len(colors))


def decimate(x: np.ndarray, y: np.ndarray, max_points: int = MAX_POINTS):
    """Réduit le nombre de points en gardant min et max de chaque tranche ;
    les pertes (NaN) sont conservées comme coupures de ligne."""
    n = x.size
    if n <= max_points:
        return x, y
    b = int(np.ceil(n / (max_points / 2)))
    m = (n // b) * b
    xs = x[:m].reshape(-1, b)
    ys = y[:m].reshape(-1, b)
    with np.errstate(all="ignore"):
        allnan = np.isnan(ys).all(axis=1)
        ymin = np.where(allnan, np.nan, np.nanmin(np.where(np.isnan(ys), np.inf, ys), axis=1))
        ymax = np.where(allnan, np.nan, np.nanmax(np.where(np.isnan(ys), -np.inf, ys), axis=1))
    xo = np.repeat(xs[:, 0], 2)
    xo[1::2] = xs[:, -1]
    yo = np.empty(xo.size)
    yo[0::2], yo[1::2] = ymin, ymax
    return np.concatenate([xo, x[m:]]), np.concatenate([yo, y[m:]])


class _Base(QWidget):
    def __init__(self, parent=None, date_axis=True):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        axes = {"bottom": pg.DateAxisItem()} if date_axis else {}  # heure locale
        self.pw = pg.PlotWidget(axisItems=axes)
        self.pw.setMenuEnabled(False)
        self.plot: pg.PlotItem = self.pw.getPlotItem()
        self.plot.hideButtons()
        lay.addWidget(self.pw)
        self.th: dict = {}
        self.pal = theme.palette()

    def apply_palette(self, p: theme.Palette) -> None:
        self.pal = p
        self.pw.setBackground(p.chart_bg)
        _style_plot(self.plot, p)

    def set_thresholds(self, th: dict) -> None:
        self.th = dict(th)

    def grab_image(self, width=1600, height=420):
        """Image du graphique (rapport PDF)."""
        self.resize(width, height)
        self.pw.resize(width, height)
        return self.pw.grab().toImage()


class TimelineChart(_Base):
    """Latence dans le temps avec navigation. Signaux :
    ``clicked(ts)`` clic sur un instant ; ``follow_changed(bool)`` mode direct."""

    clicked = Signal(float)
    follow_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.follow = True
        self.window_s = 600
        self.scale_ms = 0
        self._ts = np.empty(0)
        self._val = np.empty(0)
        self._updating = False
        self._interval = 1.0
        self._loss_x = np.empty(0)
        self.plot.setLabel("left", "ms")
        vb = self.plot.getViewBox()
        vb.setMouseEnabled(x=True, y=False)
        vb.disableAutoRange()
        vb.sigRangeChangedManually.connect(self._manual)
        self.loss_bars = pg.BarGraphItem(x=[], height=[], width=1, y0=0)
        self.plot.addItem(self.loss_bars)
        self.curve = pg.PlotDataItem(connect="finite")
        self.plot.addItem(self.curve)
        self.thr_lines = []
        self.focus_line = pg.InfiniteLine(angle=90, movable=False)
        self.focus_line.setVisible(False)
        self.plot.addItem(self.focus_line)
        self.label = pg.TextItem(anchor=(0, 0))
        self.label.setZValue(50)
        self.plot.addItem(self.label, ignoreBounds=True)
        self.hover = pg.InfiniteLine(angle=90, movable=False)
        self.hover.setVisible(False)
        self.plot.addItem(self.hover, ignoreBounds=True)
        self.pw.scene().sigMouseClicked.connect(self._click)
        self._proxy = pg.SignalProxy(self.pw.scene().sigMouseMoved, rateLimit=30,
                                     slot=self._move)
        self.apply_palette(theme.palette())

    # -------------------------------------------------------------- style
    def apply_palette(self, p):
        super().apply_palette(p)
        self.loss_bars.setOpts(brush=_qc(p.crit, 110), pen=pg.mkPen(None))
        self.focus_line.setPen(pg.mkPen(p.accent, width=2, style=Qt.DashLine))
        self.hover.setPen(pg.mkPen(p.muted, width=1, style=Qt.DotLine))
        self.label.setColor(p.text)
        self._apply_colormap()

    def set_thresholds(self, th):
        super().set_thresholds(th)
        self._apply_colormap()

    def _apply_colormap(self):
        p, th = self.pal, self.th
        if not th:
            return
        w, b, c = th["latency_warn"], th["latency_bad"], th["latency_crit"]
        s = c * 1.25
        e = 1e-4
        pos = [0, w / s - e, w / s, b / s - e, b / s, c / s - e, c / s, 1.0]
        cols = [p.ok, p.ok, p.warn, p.warn, p.bad, p.bad, p.crit, p.crit]
        cm = pg.ColorMap(pos, [_qc(x) for x in cols])
        cma = pg.ColorMap(pos, [_qc(x, 70) for x in cols])
        self.curve.setPen(cm.getPen(span=(0, s), width=1.6, orientation="vertical"))
        self.curve.setFillLevel(0)
        self.curve.setBrush(cma.getBrush(span=(0, s), orientation="vertical"))
        for ln in self.thr_lines:
            self.plot.removeItem(ln)
        self.thr_lines = []
        for v, col in ((w, p.warn), (b, p.bad), (c, p.crit)):
            ln = pg.InfiniteLine(pos=v, angle=0, movable=False,
                                 pen=pg.mkPen(_qc(col, 90), width=1, style=Qt.DashLine))
            self.plot.addItem(ln, ignoreBounds=True)
            self.thr_lines.append(ln)

    # ------------------------------------------------------------ données
    def set_window(self, seconds: int) -> None:
        self.window_s = seconds
        self.set_follow(True)

    def set_follow(self, on: bool) -> None:
        if on != self.follow:
            self.follow = on
            self.follow_changed.emit(on)
        if on:
            self._apply_range()

    def set_focus_time(self, ts: float | None) -> None:
        self.focus_line.setVisible(ts is not None)
        if ts is not None:
            self.focus_line.setValue(ts)

    def update_data(self, ts: np.ndarray, values: np.ndarray, interval: float) -> None:
        v = np.asarray(values, dtype=np.float64)
        keep = v != NOT_PROBED
        ts, v = ts[keep], v[keep]
        self._ts, self._val = ts, v
        if not ts.size:
            self.curve.setData([], [])
            self.loss_bars.setOpts(x=[], height=[])
            return
        # coupures sur les pauses (écart > 3 intervalles)
        gaps = np.flatnonzero(np.diff(ts) > max(3 * interval, interval + 2))
        x, y = ts, v
        if gaps.size:
            x = np.insert(ts, gaps + 1, ts[gaps] + interval)
            y = np.insert(v, gaps + 1, np.nan)
        x, y = decimate(x, y)
        self.curve.setData(x, y, connect="finite")
        lost = ts[np.isnan(v)]
        if lost.size > 5000:
            lost = lost[-5000:]
        self._loss_x = lost
        self._interval = interval
        self.loss_bars.setOpts(x=lost, width=max(interval * 0.9, 0.3), height=[1] * lost.size)
        self._apply_range()

    def _visible_max(self, x0, x1) -> float:
        ts, v = self._ts, self._val
        if not ts.size:
            return 50.0
        i0, i1 = np.searchsorted(ts, [x0, x1])
        seg = v[i0:i1]
        seg = seg[~np.isnan(seg)]
        if not seg.size:
            return 50.0
        return float(np.percentile(seg, 99.5)) if seg.size > 200 else float(seg.max())

    def _apply_range(self) -> None:
        if not self._ts.size:
            return
        self._updating = True
        try:
            vb = self.plot.getViewBox()
            if self.follow:
                first, last = float(self._ts[0]), float(self._ts[-1])
                if self.window_s == 0:
                    t0, t1 = first, last + max(self._interval, 0.5)
                elif last - first < self.window_s:
                    t0, t1 = first, first + self.window_s
                else:
                    t1 = last + max(self._interval, 0.5)
                    t0 = t1 - self.window_s
                vb.setXRange(t0, t1, padding=0)
            (x0, x1), _ = vb.viewRange()
            top = self.scale_ms or max(self._visible_max(x0, x1) * 1.2, 5.0)
            vb.setYRange(0, top, padding=0)
            self.loss_bars.setOpts(height=np.full(self._loss_x.size, top))
        finally:
            self._updating = False

    # ----------------------------------------------------------- souris
    def _manual(self, *_):
        if not self._updating:
            self.set_follow(False)
            self._apply_range()

    def _click(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        vb = self.plot.getViewBox()
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        x = vb.mapSceneToView(ev.scenePos()).x()
        if ev.double():
            self.set_follow(True)
            self.clicked.emit(-1.0)
        else:
            self.clicked.emit(float(x))

    def _move(self, evt):
        pos = evt[0]
        vb = self.plot.getViewBox()
        if not vb.sceneBoundingRect().contains(pos) or not self._ts.size:
            self.hover.setVisible(False)
            self.label.setText("")
            return
        x = vb.mapSceneToView(pos).x()
        i = int(np.clip(np.searchsorted(self._ts, x), 0, self._ts.size - 1))
        if i > 0 and abs(self._ts[i - 1] - x) < abs(self._ts[i] - x):
            i -= 1
        t, v = self._ts[i], self._val[i]
        self.hover.setValue(t)
        self.hover.setVisible(True)
        txt = time.strftime("%d/%m %H:%M:%S", time.localtime(t))
        txt += "   PERDU" if np.isnan(v) else f"   {v:.1f} ms"
        self.label.setText(txt)
        (x0, _x1), (_y0, y1) = vb.viewRange()
        self.label.setPos(x0, y1)


class LossChart(_Base):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.plot.setLabel("left", "% pertes")
        self.plot.getViewBox().setMouseEnabled(x=True, y=False)
        self.bars = pg.BarGraphItem(x=[], height=[], width=1, y0=0)
        self.plot.addItem(self.bars)
        self.apply_palette(theme.palette())

    def update_data(self, ts, values, interval, x_range=None):
        if x_range:
            i0, i1 = np.searchsorted(ts, x_range)
            ts, values = ts[i0:i1], values[i0:i1]
        span = (ts[-1] - ts[0]) if ts.size > 1 else 60
        bucket = max(interval * 5, span / 150.0, 1.0)
        x, pct = bucket_loss(ts, values, bucket)
        th = self.th or {"loss_warn": 5, "loss_crit": 15}
        p = self.pal
        cols = [_qc({0: p.ok, 1: p.warn, 2: p.bad, 3: p.crit}[loss_level(v, th)], 200)
                for v in pct]
        set_bars(self.bars, x, pct, bucket * 0.85, cols)
        top = max(10.0, float(pct.max()) * 1.2) if pct.size else 10.0
        self.plot.getViewBox().setYRange(0, min(top, 105), padding=0)


class JitterChart(_Base):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.plot.setLabel("left", "jitter (ms)")
        self.plot.getViewBox().setMouseEnabled(x=True, y=False)
        self.curve = pg.PlotDataItem(connect="finite")
        self.plot.addItem(self.curve)
        self.thr = pg.InfiniteLine(angle=0, movable=False)
        self.plot.addItem(self.thr, ignoreBounds=True)
        self.apply_palette(theme.palette())

    def apply_palette(self, p):
        super().apply_palette(p)
        self.curve.setPen(pg.mkPen(p.accent, width=1.5))
        self.curve.setFillLevel(0)
        self.curve.setBrush(_qc(p.accent, 50))
        self.thr.setPen(pg.mkPen(_qc(p.warn, 140), style=Qt.DashLine))

    def set_thresholds(self, th):
        super().set_thresholds(th)
        self.thr.setValue(th.get("jitter_warn", 30))

    def update_data(self, ts, values, interval, x_range=None):
        v = np.asarray(values, dtype=np.float64)
        keep = v != NOT_PROBED
        ts, v = ts[keep], v[keep]
        j = rolling_jitter(v)
        mask = ~np.isnan(j)
        x, y = decimate(ts[mask], j[mask])
        self.curve.setData(x, y)
        if x_range:
            i0, i1 = np.searchsorted(x, x_range)
            seg = y[i0:i1]
        else:
            seg = y
        seg = seg[~np.isnan(seg)] if seg.size else seg
        top = max(float(seg.max()) * 1.2 if seg.size else 5.0,
                  self.th.get("jitter_warn", 30) * 1.15)
        self.plot.getViewBox().setYRange(0, top, padding=0)


class HopCompareChart(QWidget):
    """Comparaison des sauts : latence (moy. + plage min‑max) et pertes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.glw = pg.GraphicsLayoutWidget()
        lay.addWidget(self.glw)
        self.lat = self.glw.addPlot(row=0, col=0)
        self.loss = self.glw.addPlot(row=1, col=0)
        self.glw.ci.layout.setRowStretchFactor(0, 3)
        self.glw.ci.layout.setRowStretchFactor(1, 1)
        self.loss.setXLink(self.lat)
        self.lat.setLabel("left", "latence (ms)")
        self.loss.setLabel("left", "% pertes")
        self.loss.setLabel("bottom", "saut")
        for pl in (self.lat, self.loss):
            pl.setMenuEnabled(False)
            pl.hideButtons()
            pl.getViewBox().setMouseEnabled(x=False, y=False)
        self.bars = pg.BarGraphItem(x=[], height=[], width=0.6)
        self.err = pg.ErrorBarItem(x=np.array([]), y=np.array([]), beam=0.25)
        self.lbars = pg.BarGraphItem(x=[], height=[], width=0.6)
        self.lat.addItem(self.bars)
        self.lat.addItem(self.err)
        self.loss.addItem(self.lbars)
        self.th: dict = {}
        self.pal = theme.palette()
        self.apply_palette(self.pal)

    def apply_palette(self, p):
        self.pal = p
        self.glw.setBackground(p.chart_bg)
        _style_plot(self.lat, p)
        _style_plot(self.loss, p)
        self.err.setData(pen=pg.mkPen(p.text, width=1.2))

    def set_thresholds(self, th):
        self.th = dict(th)

    def update_rows(self, rows) -> None:
        """``rows`` : liste de HopRow (analysis.py)."""
        p, th = self.pal, self.th
        x = np.array([r.ttl for r in rows], dtype=float)
        avg = np.array([r.stats.avg for r in rows], dtype=float)
        mn = np.array([r.stats.min for r in rows], dtype=float)
        mx = np.array([r.stats.max for r in rows], dtype=float)
        loss = np.array([r.stats.loss_pct if r.stats.sent else 0 for r in rows], dtype=float)
        ok = ~np.isnan(avg)
        info = np.array([r.level < 0 for r in rows])
        cols = [_qc(p.info if inf else theme.level_color(latency_level(a, th) if th else 0), 210)
                for a, inf in zip(avg[ok], info[ok])]
        set_bars(self.bars, x[ok], avg[ok], 0.6, cols)
        self.err.setData(x=x[ok], y=avg[ok], top=(mx - avg)[ok], bottom=(avg - mn)[ok])
        lcols = [_qc(p.info if inf else theme.level_color(loss_level(v, th) if th else 0), 210)
                 for v, inf in zip(loss, info)]
        set_bars(self.lbars, x, loss, 0.6, lcols)
        n = max(len(rows), 1)
        self.lat.setXRange(0.4, n + 0.6, padding=0)
        sig = ok & ~info if (ok & ~info).any() else ok
        top = float(np.nanmax(mx[sig])) * 1.15 if sig.any() else 10
        self.lat.setYRange(0, max(top, 5), padding=0)
        self.loss.setYRange(0, max(10, float(loss.max()) * 1.2 if loss.size else 10), padding=0)
        ax = self.loss.getAxis("bottom")
        ax.setTicks([[(t, str(int(t))) for t in x]])
        self.lat.getAxis("bottom").setTicks([[(t, str(int(t))) for t in x]])

    def grab_image(self, width=1600, height=520):
        self.resize(width, height)
        self.glw.resize(width, height)
        return self.glw.grab().toImage()


class Sparkline(pg.PlotWidget):
    """Mini‑graphique de latence (tableau de bord)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMenuEnabled(False)
        pl = self.getPlotItem()
        pl.hideAxis("left")
        pl.hideAxis("bottom")
        pl.hideButtons()
        pl.setMouseEnabled(x=False, y=False)
        self.setFixedHeight(70)
        self.curve = pl.plot(connect="finite")
        self.loss = pg.BarGraphItem(x=[], height=[], width=1, y0=0)
        pl.addItem(self.loss)
        self.setBackground(theme.palette().surface)

    def update_data(self, ts, values, color: str, interval: float):
        p = theme.palette()
        self.setBackground(p.surface)
        v = np.asarray(values, dtype=np.float64)
        keep = v != NOT_PROBED
        ts, v = ts[keep], v[keep]
        self.curve.setPen(pg.mkPen(color, width=1.6))
        self.curve.setFillLevel(0)
        self.curve.setBrush(_qc(color, 45))
        self.curve.setData(ts, v)
        top = float(np.nanmax(v)) * 1.2 if np.isfinite(v).any() else 10.0
        lost = ts[np.isnan(v)]
        self.loss.setOpts(x=lost, height=np.full(lost.size, top), width=max(interval, 0.3),
                          brush=_qc(p.crit, 120), pen=pg.mkPen(None))
        if ts.size:
            self.getPlotItem().setXRange(ts[0], ts[-1], padding=0.01)
            self.getPlotItem().setYRange(0, top, padding=0)
