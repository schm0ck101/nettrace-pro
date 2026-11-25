"""Tableau de bord : état de la connexion, cartes des destinations surveillées,
démarrage rapide et derniers événements."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..events import LEVEL_LABEL, NetEvent
from ..net.stats import series_stats
from . import theme
from .charts import Sparkline
from .hop_table import fmt_ms
from .target_view import StatusDot

EVENT_LEVEL_NUM = {"error": 3, "warning": 2, "success": 0, "info": -1}


class TargetCard(QFrame):
    open_requested = Signal(object)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 10)
        top = QHBoxLayout()
        self.dot = StatusDot(12)
        top.addWidget(self.dot)
        self.name = QLabel(session.name)
        self.name.setObjectName("SectionTitle")
        top.addWidget(self.name, 1)
        self.state = QLabel("")
        self.state.setObjectName("Muted")
        top.addWidget(self.state)
        lay.addLayout(top)
        self.host = QLabel("")
        self.host.setObjectName("Muted")
        lay.addWidget(self.host)
        self.spark = Sparkline()
        lay.addWidget(self.spark)
        row = QHBoxLayout()
        self.vals = {}
        for key, title in (("cur", "Actuel"), ("avg", "Moyenne"), ("jit", "Jitter"), ("loss", "Pertes")):
            box = QVBoxLayout()
            box.setSpacing(0)
            t = QLabel(title)
            t.setObjectName("StatTitle")
            v = QLabel("—")
            v.setStyleSheet("font-size: 12pt; font-weight: 600;")
            box.addWidget(t)
            box.addWidget(v)
            row.addLayout(box)
            self.vals[key] = v
        lay.addLayout(row)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.open_requested.emit(self.session)

    def refresh(self, th):
        s = self.session
        p = theme.palette()
        col = theme.level_color(s.level) if s.state == "running" else (
            p.crit if s.state == "error" else p.muted)
        self.dot.set_color(col)
        txt = s.status_text
        self.state.setToolTip(txt)
        self.state.setText(txt if len(txt) <= 34 else txt[:32] + "…")
        self.state.setStyleSheet(f"color:{col};")
        self.host.setText(f"{s.opt.host} → {s.target_ip or '…'}")
        ts, dest, _ = s.buffer.arrays(last=300)
        st = series_stats(dest[-100:])
        self.spark.update_data(ts, dest, col if s.state == "running" else p.accent, s.opt.interval)
        self.vals["cur"].setText("perdu" if st.last_lost else (fmt_ms(st.current) + " ms" if st.sent else "—"))
        self.vals["avg"].setText(fmt_ms(st.avg) + " ms" if st.received else "—")
        self.vals["jit"].setText(fmt_ms(st.jitter) + " ms" if st.received > 1 else "—")
        self.vals["loss"].setText(f"{st.loss_pct:.1f} %" if st.sent else "—")


class Dashboard(QWidget):
    open_session = Signal(object)
    quick_start = Signal(str, str)     # hôte, nom
    start_all_favorites = Signal()

    def __init__(self, monitor, settings, parent=None):
        super().__init__(parent)
        self.monitor = monitor
        self.settings = settings
        self.cards: dict[int, TargetCard] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        t = QLabel("Tableau de bord")
        t.setObjectName("PageTitle")
        root.addWidget(t)

        # Bandeau d'état global
        self.banner = QFrame()
        self.banner.setObjectName("Panel")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(16, 12, 16, 12)
        self.g_dot = StatusDot(18)
        bl.addWidget(self.g_dot)
        bb = QVBoxLayout()
        bb.setSpacing(0)
        self.g_title = QLabel("Aucune surveillance en cours")
        self.g_title.setObjectName("SectionTitle")
        self.g_sub = QLabel("Démarrez une surveillance ci‑dessous ou depuis la page Surveillance.")
        self.g_sub.setObjectName("Muted")
        bb.addWidget(self.g_title)
        bb.addWidget(self.g_sub)
        bl.addLayout(bb, 1)
        self.net_lbl = QLabel("")
        self.net_lbl.setObjectName("Muted")
        self.net_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bl.addWidget(self.net_lbl)
        root.addWidget(self.banner)

        # Démarrage rapide
        qs = QHBoxLayout()
        lab = QLabel("Démarrage rapide :")
        lab.setObjectName("Muted")
        qs.addWidget(lab)
        self.quick_box = QHBoxLayout()
        qs.addLayout(self.quick_box)
        qs.addStretch(1)
        allb = QPushButton("Surveiller tous les favoris")
        allb.setObjectName("Primary")
        allb.clicked.connect(self.start_all_favorites.emit)
        qs.addWidget(allb)
        root.addLayout(qs)
        self.rebuild_quick()

        # Grille des destinations
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(12)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.grid_host)
        self.empty = QLabel("Les destinations surveillées apparaîtront ici, chacune avec son graphique.")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty)
        root.addWidget(self.scroll, 3)

        # Derniers événements
        et = QLabel("Derniers événements")
        et.setObjectName("SectionTitle")
        root.addWidget(et)
        self.events = QListWidget()
        self.events.setMaximumHeight(170)
        root.addWidget(self.events, 1)

    def rebuild_quick(self):
        while self.quick_box.count():
            w = self.quick_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for fav in self.settings.get("favorites", [])[:8]:
            b = QPushButton(f"{fav['name']}  ·  {fav['host']}")
            b.setObjectName("Chip")
            b.clicked.connect(lambda _=False, f=fav: self.quick_start.emit(f["host"], f["name"]))
            self.quick_box.addWidget(b)

    def set_netinfo(self, text: str):
        self.net_lbl.setText(text)

    def add_event(self, ev: NetEvent):
        if ev.category == "session" and ev.level == "info":
            return
        col = theme.level_color(EVENT_LEVEL_NUM.get(ev.level, -1))
        it = QListWidgetItem(f"{time.strftime('%H:%M:%S', time.localtime(ev.ts))}   "
                             f"{LEVEL_LABEL.get(ev.level, ev.level):<13}  {ev.target} — {ev.message}")
        it.setForeground(QColor(col))
        self.events.insertItem(0, it)
        while self.events.count() > 200:
            self.events.takeItem(self.events.count() - 1)

    def sync_cards(self):
        sessions = list(self.monitor.sessions)
        ids = {s.uid for s in sessions}
        for uid in list(self.cards):
            if uid not in ids:
                self.cards.pop(uid).deleteLater()
        for s in sessions:
            if s.uid not in self.cards:
                c = TargetCard(s)
                c.open_requested.connect(self.open_session.emit)
                self.cards[s.uid] = c
        for i in reversed(range(self.grid.count())):
            self.grid.takeAt(i)
        cols = 3 if self.width() > 1300 else 2
        for i, s in enumerate(sessions):
            self.grid.addWidget(self.cards[s.uid], i // cols, i % cols)
        self.grid.setRowStretch(len(sessions) // cols + 1, 1)
        self.empty.setVisible(not sessions)

    def refresh(self):
        th = self.settings.section("thresholds")
        for c in self.cards.values():
            c.refresh(th)
        running = [s for s in self.monitor.sessions if s.state in ("running", "paused", "error", "resolving")]
        p = theme.palette()
        if not running:
            self.g_dot.set_color(p.muted)
            self.g_title.setText("Aucune surveillance en cours")
            self.g_sub.setText("Démarrez une surveillance ci‑dessous ou depuis la page Surveillance.")
            return
        worst = max(running, key=lambda s: s.level)
        lvl = worst.level
        self.g_dot.set_color(theme.level_color(lvl))
        if lvl == 0:
            self.g_title.setText("Connexion normale")
            self.g_sub.setText(f"{len(running)} destination(s) surveillée(s), aucune anomalie.")
        else:
            bad = [s for s in running if s.level > 0]
            self.g_title.setText(f"{worst.status_text} — {worst.name}")
            self.g_sub.setText(f"{len(bad)} destination(s) sur {len(running)} présentent une anomalie.")
