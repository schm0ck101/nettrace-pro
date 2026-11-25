"""Historique : liste des sessions enregistrées, ouverture, comparaison de
sessions ou de périodes, exports et suppression."""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..net.stats import series_stats
from . import theme
from .charts import _style_plot
from .hop_table import fmt_ms
from .target_view import HistorySource

SERIES_COLORS = ["#3d9bff", "#ff8c1a", "#2ecc71", "#c678dd", "#f4c430", "#ff4d4f",
                 "#56b6c2", "#e06c75", "#98c379", "#d19a66"]


def _dt(ts):
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts)) if ts else "—"


def _dur(sec: float) -> str:
    sec = int(sec)
    if sec < 3600:
        return f"{sec // 60} min {sec % 60:02d} s"
    return f"{sec // 3600} h {(sec % 3600) // 60:02d} min"


class NumItem(QTableWidgetItem):
    """Cellule triée sur sa valeur numérique."""

    def __init__(self, text: str, value: float):
        super().__init__(text)
        self.value = -1e18 if value is None or (isinstance(value, float) and math.isnan(value)) else value
        self.setTextAlignment(int(Qt.AlignRight | Qt.AlignVCenter))

    def __lt__(self, other):
        if isinstance(other, NumItem):
            return self.value < other.value
        return super().__lt__(other)


class CompareDialog(QDialog):
    """Superpose plusieurs séries (sessions ou périodes d'une session) sur un
    axe de temps relatif et compare leurs statistiques."""

    def __init__(self, history, records, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Comparaison")
        self.resize(1200, 760)
        self.history = history
        self.records = records
        self.data = {r.id: history.load_samples(r.id).arrays() for r in records}
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        title = QLabel("Comparaison de sessions" if len(records) > 1 else
                       f"Comparaison de périodes — {records[0].name}")
        title.setObjectName("PageTitle")
        top.addWidget(title, 1)
        self.split_cb = QComboBox()
        for label, sec in (("Par 15 minutes", 900), ("Par heure", 3600), ("Par 6 heures", 21600),
                           ("Par jour", 86400)):
            self.split_cb.addItem(label, sec)
        self.split_cb.setCurrentIndex(1)
        self.split_cb.currentIndexChanged.connect(self._rebuild)
        if len(records) == 1:
            top.addWidget(QLabel("Découper la session :"))
            top.addWidget(self.split_cb)
        lay.addLayout(top)
        split = QSplitter(Qt.Vertical)
        self.pw = pg.PlotWidget()
        self.pw.setMenuEnabled(False)
        self.plot = self.pw.getPlotItem()
        self.plot.setLabel("left", "latence (ms)")
        self.plot.setLabel("bottom", "minutes depuis le début de la série")
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.legend = self.plot.addLegend(offset=(10, 10))
        p = theme.palette()
        self.pw.setBackground(p.chart_bg)
        _style_plot(self.plot, p)
        split.addWidget(self.pw)
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(["Série", "Début", "Échantillons", "Moyenne", "Min",
                                              "Max", "Jitter", "Pertes", "Évaluation"])
        self.table.verticalHeader().hide()
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        split.addWidget(self.table)
        split.setSizes([480, 260])
        lay.addWidget(split)
        self._rebuild()

    def _series(self):
        out = []
        if len(self.records) > 1:
            for r in self.records:
                ts, d, _ = self.data[r.id]
                out.append((f"{r.name} ({_dt(r.started)})", ts, d))
            return out
        r = self.records[0]
        ts, d, _ = self.data[r.id]
        if not ts.size:
            return out
        step = self.split_cb.currentData()
        start = math.floor(ts[0] / step) * step
        while start <= ts[-1]:
            i0, i1 = np.searchsorted(ts, [start, start + step])
            if i1 > i0:
                lbl = time.strftime("%d/%m %H:%M", time.localtime(start))
                out.append((lbl, ts[i0:i1], d[i0:i1]))
            start += step
        return out

    def _rebuild(self):
        self.plot.clear()
        self.legend.clear()
        series = self._series()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(series))
        stats = [series_stats(d) for _, _, d in series]
        avgs = [s.avg for s in stats if s.received]
        best = min(avgs) if avgs else None
        worst = max(avgs) if avgs else None
        for i, ((label, ts, d), st) in enumerate(zip(series, stats)):
            col = SERIES_COLORS[i % len(SERIES_COLORS)]
            if ts.size:
                x = (ts - ts[0]) / 60.0
                y = d.astype(float)
                gaps = np.flatnonzero(np.diff(ts) > 30)   # pauses : on coupe la ligne
                if gaps.size:
                    x = np.insert(x, gaps + 1, np.nan)
                    y = np.insert(y, gaps + 1, np.nan)
                self.plot.plot(x, y, pen=pg.mkPen(col, width=1.5), name=label, connect="finite")
            vals = [label, _dt(ts[0]) if ts.size else "—"]
            self.table.setItem(i, 0, QTableWidgetItem(f"■ {label}"))
            self.table.item(i, 0).setForeground(pg.mkColor(col))
            self.table.setItem(i, 1, QTableWidgetItem(vals[1]))
            self.table.setItem(i, 2, NumItem(str(st.sent), st.sent))
            self.table.setItem(i, 3, NumItem(fmt_ms(st.avg) + " ms", st.avg))
            self.table.setItem(i, 4, NumItem(fmt_ms(st.min) + " ms", st.min))
            self.table.setItem(i, 5, NumItem(fmt_ms(st.max) + " ms", st.max))
            self.table.setItem(i, 6, NumItem(fmt_ms(st.jitter) + " ms", st.jitter))
            self.table.setItem(i, 7, NumItem(f"{st.loss_pct:.2f} %", st.loss_pct))
            ev = ""
            if len(series) > 1 and st.received:
                ev = "Meilleure latence" if st.avg == best else ("Moins bonne latence" if st.avg == worst else "")
            if st.loss_pct >= 2:
                ev = (ev + " · " if ev else "") + "pertes notables"
            self.table.setItem(i, 8, QTableWidgetItem(ev))
        self.table.setSortingEnabled(True)
        self.plot.enableAutoRange()


class HistoryView(QWidget):
    open_source = Signal(object)    # HistorySource

    def __init__(self, history, settings, parent=None):
        super().__init__(parent)
        self.history = history
        self.settings = settings
        self.records = []
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        t = QLabel("Historique des sessions")
        t.setObjectName("PageTitle")
        root.addWidget(t)
        filt = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher une destination, une IP, un nom…")
        self.search.textChanged.connect(self.reload)
        self.period = QComboBox()
        for label, sec in (("Toutes les dates", 0), ("Dernières 24 h", 86400),
                           ("7 derniers jours", 7 * 86400), ("30 derniers jours", 30 * 86400)):
            self.period.addItem(label, sec)
        self.period.currentIndexChanged.connect(self.reload)
        filt.addWidget(self.search, 1)
        filt.addWidget(self.period)
        rb = QPushButton("Actualiser")
        rb.clicked.connect(self.reload)
        filt.addWidget(rb)
        root.addLayout(filt)

        self.table = QTableWidget()
        cols = ["Nom", "Destination", "Adresse IP", "Début", "Durée", "Échantillons",
                "Moyenne", "Min", "Max", "Jitter", "Pertes", "Mode"]
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self.open_selected)
        self.table.itemSelectionChanged.connect(self._sel_changed)
        root.addWidget(self.table, 1)

        btns = QHBoxLayout()
        self.b_open = QPushButton("Ouvrir")
        self.b_open.setObjectName("Primary")
        self.b_open.clicked.connect(self.open_selected)
        self.b_cmp = QPushButton("Comparer")
        self.b_cmp.setToolTip("1 session : comparer ses périodes · plusieurs sessions : les superposer")
        self.b_cmp.clicked.connect(self.compare_selected)
        self.b_csv = QPushButton("Exporter CSV")
        self.b_csv.clicked.connect(lambda: self._export("csv"))
        self.b_pdf = QPushButton("Rapport PDF")
        self.b_pdf.clicked.connect(lambda: self._export("pdf"))
        self.b_ren = QPushButton("Renommer / notes")
        self.b_ren.clicked.connect(self.rename_selected)
        self.b_del = QPushButton("Supprimer")
        self.b_del.setObjectName("Danger")
        self.b_del.clicked.connect(self.delete_selected)
        for b in (self.b_open, self.b_cmp, self.b_csv, self.b_pdf, self.b_ren):
            btns.addWidget(b)
        btns.addStretch(1)
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        btns.addWidget(self.info)
        btns.addWidget(self.b_del)
        root.addLayout(btns)
        self._sel_changed()

    def selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        ids = [self.table.item(r, 0).data(Qt.UserRole) for r in rows]
        return [r for r in self.records if r.id in ids]

    def _sel_changed(self):
        n = len(self.selected())
        self.b_open.setEnabled(n == 1)
        self.b_cmp.setEnabled(n >= 1)
        self.b_csv.setEnabled(n == 1)
        self.b_pdf.setEnabled(n == 1)
        self.b_ren.setEnabled(n == 1)
        self.b_del.setEnabled(n >= 1)

    def reload(self):
        sec = self.period.currentData()
        self.records = self.history.list_sessions(self.search.text().strip(),
                                                  since=time.time() - sec if sec else None)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.records))
        for i, r in enumerate(self.records):
            it = QTableWidgetItem(r.name + (" (en cours)" if r.ended is None else ""))
            it.setData(Qt.UserRole, r.id)
            if r.notes:
                it.setToolTip(r.notes)
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, QTableWidgetItem(r.host))
            self.table.setItem(i, 2, QTableWidgetItem(r.ip or "—"))
            self.table.setItem(i, 3, NumItem(_dt(r.started), r.started))
            self.table.item(i, 3).setTextAlignment(int(Qt.AlignLeft | Qt.AlignVCenter))
            self.table.setItem(i, 4, NumItem(_dur(r.duration), r.duration))
            self.table.setItem(i, 5, NumItem(str(r.samples or 0), r.samples or 0))
            self.table.setItem(i, 6, NumItem(fmt_ms(r.avg if r.avg is not None else float("nan")) + " ms", r.avg))
            self.table.setItem(i, 7, NumItem(fmt_ms(r.min if r.min is not None else float("nan")) + " ms", r.min))
            self.table.setItem(i, 8, NumItem(fmt_ms(r.max if r.max is not None else float("nan")) + " ms", r.max))
            self.table.setItem(i, 9, NumItem(fmt_ms(r.jitter if r.jitter is not None else float("nan")) + " ms", r.jitter))
            loss = r.loss if r.loss is not None else 0.0
            li = NumItem(f"{loss:.2f} %", loss)
            if loss >= 2:
                li.setForeground(pg.mkColor(theme.palette().crit))
            self.table.setItem(i, 10, li)
            self.table.setItem(i, 11, QTableWidgetItem("Traceroute" if r.mode == "trace" else "Ping"))
        self.table.setSortingEnabled(True)
        size = self.history.db_size() / 1e6
        self.info.setText(f"{len(self.records)} session(s) · base {size:.1f} Mo")
        self._sel_changed()

    def _source(self, rec) -> HistorySource:
        self.history.flush(2)
        rec = self.history.get_session(rec.id) or rec
        buf = self.history.load_samples(rec.id, columns=rec.max_hops)
        return HistorySource(rec, buf, self.history.load_hops(rec.id))

    def open_selected(self, *_):
        sel = self.selected()
        if len(sel) != 1:
            return
        src = self._source(sel[0])
        if not len(src.buffer):
            QMessageBox.information(self, "Historique", "Cette session ne contient aucune mesure.")
            return
        self.open_source.emit(src)

    def compare_selected(self):
        sel = self.selected()
        if not sel:
            return
        self.history.flush(2)
        CompareDialog(self.history, sel, self).exec()

    def rename_selected(self):
        sel = self.selected()
        if len(sel) != 1:
            return
        r = sel[0]
        name, ok = QInputDialog.getText(self, "Renommer", "Nom de la session :", text=r.name)
        if not ok:
            return
        notes, ok2 = QInputDialog.getMultiLineText(self, "Notes", "Notes (facultatif) :", r.notes or "")
        self.history.rename_session(r.id, name.strip() or r.name, notes if ok2 else None)
        self.reload()

    def delete_selected(self):
        sel = self.selected()
        if not sel:
            return
        if QMessageBox.question(self, "Supprimer",
                                f"Supprimer définitivement {len(sel)} session(s) et leurs mesures ?"
                                ) != QMessageBox.Yes:
            return
        self.history.delete_sessions([r.id for r in sel])
        self.reload()

    def _export(self, kind: str):
        sel = self.selected()
        if len(sel) != 1:
            return
        src = self._source(sel[0])
        th = self.settings.section("thresholds")
        name = f"NetTrace_{src.name}_{time.strftime('%Y%m%d_%H%M', time.localtime(sel[0].started))}"
        name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        from ..storage.export import export_csv, export_pdf
        if kind == "csv":
            path, _ = QFileDialog.getSaveFileName(self, "Exporter en CSV",
                                                  str(Path.home() / "Documents" / f"{name}.csv"),
                                                  "Fichiers CSV (*.csv)")
            if path:
                files = export_csv(src, path, th)
                QMessageBox.information(self, "Export CSV", "Fichiers créés :\n" +
                                        "\n".join(map(str, files)))
        else:
            path, _ = QFileDialog.getSaveFileName(self, "Rapport PDF",
                                                  str(Path.home() / "Documents" / f"{name}.pdf"),
                                                  "Documents PDF (*.pdf)")
            if path:
                export_pdf(src, path, th, self.history.list_events(src.db_id),
                           title=f"Diagnostic réseau — {src.name}")
                QMessageBox.information(self, "Rapport PDF", f"Rapport créé :\n{path}")
