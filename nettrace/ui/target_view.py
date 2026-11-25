"""Vue détaillée d'une destination : en‑tête, statistiques, tableau des sauts
et graphiques. Fonctionne pour une session en direct ou pour une session
rechargée depuis l'historique (lecture seule)."""
from __future__ import annotations

import math
import re
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ..config import FOCUS_CHOICES, INTERVALS, TIME_WINDOWS
from ..net.alerts import LEVEL_OK, latency_level, loss_level
from ..net.analysis import analyze
from ..net.stats import series_stats
from ..net.tracer import HopInfo
from . import theme
from .charts import HopCompareChart, JitterChart, LossChart, TimelineChart
from .hop_table import HopTable, fmt_ms

WINDOW_LABELS = {60: "1 min", 300: "5 min", 600: "10 min", 1800: "30 min", 3600: "1 h",
                 6 * 3600: "6 h", 24 * 3600: "24 h", 0: "Tout"}


def interval_label(v: float) -> str:
    return f"{v:g} s".replace(".", ",")


class HistorySource:
    """Session rechargée depuis la base, avec la même interface qu'une
    TraceSession pour la vue."""

    live = False

    def __init__(self, rec, buffer, hop_rows):
        self.rec = rec
        self.db_id = rec.id
        self.name = rec.name or rec.host
        self.opt = SimpleNamespace(host=rec.host, interval=rec.interval or 1.0,
                                   mode=rec.mode or "trace", max_hops=rec.max_hops or 30)
        self.target_ip = rec.ip
        self.buffer = buffer
        self.state = "history"
        known = {t: (ip, hn) for t, ip, hn in hop_rows}
        _ts, _d, hops = buffer.arrays(copy=False)
        probed = np.flatnonzero((hops != -1).any(axis=0)) if hops.size else np.array([])
        n = int(probed[-1]) + 1 if probed.size else max(known or [1])
        self.hops = []
        for t in range(1, n + 1):
            ip, hn = known.get(t, (None, None))
            self.hops.append(HopInfo(t, ip, hn, is_destination=(ip is not None and ip == rec.ip)))
        if self.opt.mode == "ping" and self.hops:
            self.hops = self.hops[:1]
            self.hops[0].ip = rec.ip
            self.hops[0].is_destination = True
        st = series_stats(_d)
        self._level = max(loss_level(st.loss_pct, {"loss_warn": 5, "loss_crit": 15})
                          if st.lost >= 2 else 0, 0)
        self.status_text = (f"Historique — {time.strftime('%d/%m/%Y %H:%M', time.localtime(rec.started))}"
                            f" ({rec.duration / 60:.0f} min)")

    @property
    def level(self):
        return LEVEL_OK

    @property
    def family_label(self):
        return "IPv6" if self.target_ip and ":" in self.target_ip else "IPv4"

    def display_hops(self):
        return self.hops


class StatCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        self.t = QLabel(title.upper())
        self.t.setObjectName("StatTitle")
        self.v = QLabel("—")
        self.v.setObjectName("StatValue")
        self.s = QLabel("")
        self.s.setObjectName("StatSub")
        lay.addWidget(self.t)
        lay.addWidget(self.v)
        lay.addWidget(self.s)

    def set(self, value: str, sub: str = "", color: str | None = None):
        self.v.setText(value)
        self.s.setText(sub)
        self.v.setStyleSheet(f"color: {color};" if color else "")


class StatusDot(QLabel):
    def __init__(self, size=14, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.set_color(theme.palette().muted)

    def set_color(self, color: str):
        self.setStyleSheet(f"background:{color}; border-radius:{self._size // 2}px;")


class TargetView(QWidget):
    """``request_*`` : demandes traitées par la fenêtre principale."""

    request_stop = Signal(object)
    request_restart = Signal(object)

    def __init__(self, source, settings, history=None, parent=None):
        super().__init__(parent)
        self.source = source
        self.settings = settings
        self.history = history
        self.live = getattr(source, "live", True)
        self.focus_ts: float | None = None      # None = direct
        self.sel_ttl: int | None = None         # saut affiché dans les graphiques
        self.th = settings.section("thresholds")
        self._rows = []
        self._build()
        self.apply_theme()
        self.refresh()

    # ------------------------------------------------------------ interface
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(10)

        # En‑tête
        head = QHBoxLayout()
        self.dot = StatusDot(16)
        head.addWidget(self.dot)
        tbox = QVBoxLayout()
        tbox.setSpacing(0)
        self.title = QLabel(self.source.name)
        self.title.setObjectName("TargetTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Muted")
        tbox.addWidget(self.title)
        tbox.addWidget(self.subtitle)
        head.addLayout(tbox, 1)

        self.interval_cb = QComboBox()
        for v in INTERVALS:
            self.interval_cb.addItem(interval_label(v), v)
        cur = self.source.opt.interval
        i = self.interval_cb.findData(cur)
        if i < 0:
            self.interval_cb.addItem(interval_label(cur), cur)
            i = self.interval_cb.count() - 1
        self.interval_cb.setCurrentIndex(i)
        self.interval_cb.setToolTip("Fréquence des mesures")
        self.interval_cb.currentIndexChanged.connect(self._interval_changed)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.stop_btn = QPushButton("Arrêter")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.clicked.connect(self._stop_or_restart)
        self.csv_btn = QPushButton("Export CSV")
        self.csv_btn.clicked.connect(self.export_csv)
        self.pdf_btn = QPushButton("Rapport PDF")
        self.pdf_btn.clicked.connect(self.export_pdf)
        if self.live:
            ilab = QLabel("Intervalle")
            ilab.setObjectName("Muted")
            head.addWidget(ilab)
            head.addWidget(self.interval_cb)
            head.addWidget(self.pause_btn)
            head.addWidget(self.stop_btn)
        head.addWidget(self.csv_btn)
        head.addWidget(self.pdf_btn)
        root.addLayout(head)

        # Cartes de statistiques
        cards = QHBoxLayout()
        cards.setSpacing(8)
        self.c_cur = StatCard("Actuel")
        self.c_avg = StatCard("Moyenne")
        self.c_min = StatCard("Minimum")
        self.c_max = StatCard("Maximum")
        self.c_jit = StatCard("Jitter")
        self.c_loss = StatCard("Pertes")
        self.c_pkt = StatCard("Paquets reçus / envoyés")
        for c in (self.c_cur, self.c_avg, self.c_min, self.c_max, self.c_jit, self.c_loss, self.c_pkt):
            cards.addWidget(c)
        root.addLayout(cards)

        # Barre d'analyse
        bar = QHBoxLayout()
        lab = QLabel("Période d'analyse")
        lab.setObjectName("Muted")
        self.focus_cb = QComboBox()
        for n in FOCUS_CHOICES:
            self.focus_cb.addItem("Tous les échantillons" if n == 0 else f"{n} derniers échantillons", n)
        self.focus_cb.setCurrentIndex(max(0, self.focus_cb.findData(
            int(self.settings.get("measure.focus", 100)) if self.live else 0)))
        self.focus_cb.currentIndexChanged.connect(lambda *_: self.refresh())
        lab2 = QLabel("Graphique")
        lab2.setObjectName("Muted")
        self.window_cb = QComboBox()
        for w in TIME_WINDOWS:
            self.window_cb.addItem(WINDOW_LABELS[w], w)
        default_w = 0 if not self.live else int(self.settings.get("ui.time_window", 600))
        self.window_cb.setCurrentIndex(max(0, self.window_cb.findData(default_w)))
        self.window_cb.currentIndexChanged.connect(self._window_changed)
        self.live_btn = QPushButton("● Direct")
        self.live_btn.setCheckable(True)
        self.live_btn.setChecked(True)
        self.live_btn.setObjectName("Chip")
        self.live_btn.setToolTip("Suivre les dernières mesures (double‑clic sur le graphique)")
        self.live_btn.clicked.connect(self._go_live)
        self.focus_info = QLabel("")
        self.focus_info.setObjectName("Muted")
        self.graph_target = QPushButton("Graphique : destination")
        self.graph_target.setObjectName("Chip")
        self.graph_target.setToolTip("Cliquez sur un saut du tableau pour afficher son graphique ; "
                                     "ce bouton revient à la destination")
        self.graph_target.clicked.connect(lambda: self._select_hop(None))
        bar.addWidget(lab)
        bar.addWidget(self.focus_cb)
        bar.addSpacing(12)
        bar.addWidget(lab2)
        bar.addWidget(self.window_cb)
        bar.addWidget(self.live_btn)
        bar.addSpacing(12)
        bar.addWidget(self.focus_info, 1)
        bar.addWidget(self.graph_target)
        root.addLayout(bar)

        # Tableau + graphiques
        split = QSplitter(Qt.Vertical)
        self.table = HopTable()
        self.table.src.set_thresholds(self.th)
        self.table.src.fixed_scale = int(self.settings.get("ui.graph_scale_ms", 0) or 0)
        self.table.clicked.connect(self._row_clicked)
        split.addWidget(self.table)
        self.tabs = QTabWidget()
        self.timeline = TimelineChart()
        self.timeline.scale_ms = int(self.settings.get("ui.graph_scale_ms", 0) or 0)
        self.timeline.window_s = self.window_cb.currentData()
        self.timeline.clicked.connect(self._timeline_clicked)
        self.timeline.follow_changed.connect(self._follow_changed)
        self.loss = LossChart()
        self.jitter = JitterChart()
        self.compare = HopCompareChart()
        self.loss.plot.setXLink(self.timeline.plot)
        self.jitter.plot.setXLink(self.timeline.plot)
        self.tabs.addTab(self.timeline, "Latence")
        self.tabs.addTab(self.loss, "Pertes de paquets")
        self.tabs.addTab(self.jitter, "Jitter")
        self.tabs.addTab(self.compare, "Comparatif par saut")
        self.tabs.currentChanged.connect(lambda *_: self.refresh())
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 330])
        root.addWidget(split, 1)
        for ch in (self.timeline, self.loss, self.jitter, self.compare):
            ch.set_thresholds(self.th)

    def apply_theme(self):
        p = theme.palette()
        for ch in (self.timeline, self.loss, self.jitter, self.compare):
            ch.apply_palette(p)
        self.table.viewport().update()
        self.refresh()

    def apply_settings(self):
        self.th = self.settings.section("thresholds")
        scale = int(self.settings.get("ui.graph_scale_ms", 0) or 0)
        self.table.src.set_thresholds(self.th)
        self.table.src.fixed_scale = scale
        self.timeline.scale_ms = scale
        for ch in (self.timeline, self.loss, self.jitter, self.compare):
            ch.set_thresholds(self.th)
        self.refresh()

    # ------------------------------------------------------------ actions
    def _interval_changed(self):
        v = self.interval_cb.currentData()
        if self.live and v:
            self.source.set_interval(v)

    def _toggle_pause(self):
        s = self.source
        if s.state == "paused":
            s.resume()
        else:
            s.pause()
        self.refresh()

    def _stop_or_restart(self):
        if self.source.state in ("stopped", "finished"):
            self.request_restart.emit(self)
        else:
            self.request_stop.emit(self)
        self.refresh()

    def _window_changed(self):
        self.timeline.set_window(self.window_cb.currentData())
        self.refresh()

    def _go_live(self):
        self.focus_ts = None
        self.timeline.set_focus_time(None)
        self.timeline.set_follow(True)
        self.refresh()

    def _follow_changed(self, on: bool):
        self.live_btn.setChecked(on)

    def _timeline_clicked(self, ts: float):
        if ts < 0:
            self._go_live()
            return
        self.focus_ts = ts
        self.timeline.set_focus_time(ts)
        self.refresh()

    def _row_clicked(self, _idx):
        r = self.table.selected_row()
        if r is not None:
            self._select_hop(None if (r.is_destination or self.source.opt.mode == "ping") else r.ttl)

    def _select_hop(self, ttl):
        self.sel_ttl = ttl
        if ttl is None:
            self.table.clearSelection()
        self.refresh()

    # ------------------------------------------------------------ exports
    def _default_name(self, ext: str) -> str:
        base = re.sub(r"[^\w.-]+", "_", self.source.name)
        return f"NetTrace_{base}_{time.strftime('%Y%m%d_%H%M')}.{ext}"

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exporter en CSV",
                                              str(Path.home() / "Documents" / self._default_name("csv")),
                                              "Fichiers CSV (*.csv)")
        if not path:
            return
        from ..storage.export import export_csv
        try:
            files = export_csv(self.source, path, self.th)
            QMessageBox.information(self, "Export CSV", "Fichiers créés :\n" +
                                    "\n".join(str(f) for f in files))
        except OSError as exc:
            QMessageBox.warning(self, "Export CSV", f"Impossible d'écrire le fichier :\n{exc}")

    def export_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "Rapport PDF",
                                              str(Path.home() / "Documents" / self._default_name("pdf")),
                                              "Documents PDF (*.pdf)")
        if not path:
            return
        from ..storage.export import export_pdf
        events = None
        if self.history is not None and self.source.db_id is not None:
            self.history.flush(2)
            events = self.history.list_events(self.source.db_id)
        try:
            export_pdf(self.source, path, self.th, events,
                       title=f"Diagnostic réseau — {self.source.name}")
            QMessageBox.information(self, "Rapport PDF", f"Rapport créé :\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "Rapport PDF", f"Impossible d'écrire le fichier :\n{exc}")

    # ---------------------------------------------------------- rafraîchir
    def refresh(self):
        s = self.source
        p = theme.palette()
        ts, dest, hops = s.buffer.arrays()
        end = len(ts) if self.focus_ts is None else int(np.searchsorted(ts, self.focus_ts, side="right"))
        n = self.focus_cb.currentData() or 0
        start = 0 if n == 0 else max(0, end - n)

        # En‑tête
        active = s.state in ("running", "paused", "resolving", "error")
        col = theme.level_color(s.level) if s.state == "running" else (
            p.crit if s.state == "error" else p.muted)
        self.dot.set_color(col)
        ip = s.target_ip or "résolution…"
        mode = "Traceroute" if s.opt.mode == "trace" else "Ping"
        self.subtitle.setText(f"{s.opt.host}  →  {ip}   ·   {s.family_label}   ·   {mode}   ·   "
                              f"<b style='color:{col}'>{s.status_text}</b>")
        self.subtitle.setTextFormat(Qt.RichText)
        if self.live:
            self.pause_btn.setText("Reprendre" if s.state == "paused" else "Pause")
            self.pause_btn.setEnabled(s.state in ("running", "paused"))
            self.stop_btn.setText("Arrêter" if active else "Redémarrer")
            self.interval_cb.setEnabled(active)

        # Statistiques de la destination
        st = series_stats(dest[start:end])
        tot = series_stats(dest[:end])
        th = self.th
        lat_col = lambda v: theme.level_color(latency_level(v, th)) if not math.isnan(v) else None  # noqa
        cur_txt = "perdu" if st.sent and st.last_lost else (fmt_ms(st.current) + " ms" if st.sent else "—")
        self.c_cur.set(cur_txt, "dernière mesure",
                       p.crit if st.last_lost else (lat_col(st.current) if st.sent else None))
        self.c_avg.set(fmt_ms(st.avg) + " ms" if st.received else "—",
                       f"session : {fmt_ms(tot.avg)} ms", lat_col(st.avg) if st.received else None)
        self.c_min.set(fmt_ms(st.min) + " ms" if st.received else "—", f"session : {fmt_ms(tot.min)} ms")
        self.c_max.set(fmt_ms(st.max) + " ms" if st.received else "—", f"session : {fmt_ms(tot.max)} ms")
        jw = th.get("jitter_warn", 30)
        self.c_jit.set(fmt_ms(st.jitter) + " ms" if st.received > 1 else "—",
                       f"session : {fmt_ms(tot.jitter)} ms",
                       p.warn if st.received > 1 and st.jitter >= jw else None)
        lcol = theme.level_color(loss_level(st.loss_pct, th)) if st.lost else None
        self.c_loss.set(f"{st.loss_pct:.1f} %" if st.sent else "—",
                        f"session : {tot.loss_pct:.2f} %", lcol)
        self.c_pkt.set(f"{st.received} / {st.sent}", f"session : {tot.received} / {tot.sent}")

        if self.focus_ts is None:
            self.focus_info.setText(f"Analyse : {'toute la session' if n == 0 else f'{min(n, end)} derniers échantillons'}")
        else:
            self.focus_info.setText(
                f"Analyse jusqu'à {time.strftime('%H:%M:%S', time.localtime(self.focus_ts))} "
                f"— double‑clic sur le graphique ou « Direct » pour revenir")
        if self.focus_ts is not None:
            self.live_btn.setChecked(False)

        # Tableau des sauts
        rows = analyze(s.display_hops(), hops[start:end], hops[max(0, end - 300):end], th)
        self._rows = rows
        self.table.src.set_rows(rows)

        # Graphiques
        if self.sel_ttl is not None and self.sel_ttl <= hops.shape[1]:
            values = hops[:, self.sel_ttl - 1]
            hop = next((h for h in s.display_hops() if h.ttl == self.sel_ttl), None)
            self.graph_target.setText(f"Graphique : saut {self.sel_ttl} ({hop.ip if hop and hop.ip else '*'})  ✕")
        else:
            values = dest
            self.graph_target.setText("Graphique : destination")
        interval = float(s.opt.interval)
        tab = self.tabs.currentWidget()
        self.timeline.update_data(ts, values, interval)
        xr = self.timeline.plot.getViewBox().viewRange()[0]
        if tab is self.loss:
            self.loss.update_data(ts, values, interval, xr)
        elif tab is self.jitter:
            self.jitter.update_data(ts, values, interval, xr)
        elif tab is self.compare:
            self.compare.update_rows(rows)
