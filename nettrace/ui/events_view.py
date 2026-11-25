"""Journal des événements (interruptions, dégradations, retours à la normale…)."""
from __future__ import annotations

import csv
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..events import CATEGORY_LABEL, LEVEL_LABEL, NetEvent
from . import theme

LEVEL_NUM = {"error": 3, "warning": 2, "success": 0, "info": -1}


class EventsView(QWidget):
    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.history = history
        self.events: list[NetEvent] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        t = QLabel("Journal des événements")
        t.setObjectName("PageTitle")
        root.addWidget(t)
        f = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrer par destination ou message…")
        self.search.textChanged.connect(self._apply_filter)
        self.level = QComboBox()
        self.level.addItem("Tous les niveaux", None)
        for k in ("error", "warning", "success", "info"):
            self.level.addItem(LEVEL_LABEL[k], k)
        self.level.currentIndexChanged.connect(self._apply_filter)
        f.addWidget(self.search, 1)
        f.addWidget(self.level)
        b_exp = QPushButton("Exporter CSV")
        b_exp.clicked.connect(self.export)
        b_clr = QPushButton("Vider le journal")
        b_clr.setObjectName("Danger")
        b_clr.clicked.connect(self.clear)
        f.addWidget(b_exp)
        f.addWidget(b_clr)
        root.addLayout(f)
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Date", "Niveau", "Catégorie", "Destination", "Message"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        for c, w in enumerate((150, 110, 110, 180)):
            self.table.setColumnWidth(c, w)
        root.addWidget(self.table, 1)

    def reload(self):
        self.events = self.history.list_events(limit=5000) if self.history else []
        self._apply_filter()

    def add_event(self, ev: NetEvent):
        self.events.insert(0, ev)
        del self.events[5000:]
        if self.isVisible() and self._match(ev):
            self.table.insertRow(0)
            self._fill(0, ev)

    def _match(self, ev) -> bool:
        lvl = self.level.currentData()
        txt = self.search.text().strip().lower()
        if lvl and ev.level != lvl:
            return False
        return not txt or txt in ev.message.lower() or txt in (ev.target or "").lower()

    def _fill(self, i, ev):
        col = QColor(theme.level_color(LEVEL_NUM.get(ev.level, -1)))
        items = [time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ev.ts)),
                 LEVEL_LABEL.get(ev.level, ev.level), CATEGORY_LABEL.get(ev.category, ev.category),
                 ev.target or "", ev.message]
        for c, txt in enumerate(items):
            it = QTableWidgetItem(txt)
            if c == 1:
                it.setForeground(col)
            self.table.setItem(i, c, it)

    def _apply_filter(self):
        rows = [e for e in self.events if self._match(e)]
        self.table.setRowCount(len(rows))
        for i, ev in enumerate(rows):
            self._fill(i, ev)

    def showEvent(self, e):
        self._apply_filter()
        super().showEvent(e)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter le journal",
            str(Path.home() / "Documents" / f"NetTrace_evenements_{time.strftime('%Y%m%d_%H%M')}.csv"),
            "Fichiers CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Date", "Niveau", "Catégorie", "Destination", "Message"])
            for ev in self.events:
                if self._match(ev):
                    w.writerow([time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ev.ts)),
                                LEVEL_LABEL.get(ev.level, ev.level),
                                CATEGORY_LABEL.get(ev.category, ev.category), ev.target, ev.message])
        QMessageBox.information(self, "Export", f"Journal exporté :\n{path}")

    def clear(self):
        if QMessageBox.question(self, "Vider le journal",
                                "Supprimer tous les événements enregistrés ?") != QMessageBox.Yes:
            return
        if self.history:
            self.history.clear_events()
        self.events = []
        self._apply_filter()
