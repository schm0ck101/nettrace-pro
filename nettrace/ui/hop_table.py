"""Tableau dynamique des sauts réseau (modèle Qt + graphique intégré)."""
from __future__ import annotations

import math

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle, QStyledItemDelegate, QTableView

from ..net.alerts import LEVEL_OK, latency_level, loss_level
from ..net.analysis import HopRow
from . import theme

COLUMNS = ["Saut", "Pertes", "Adresse IP", "Nom DNS", "Moy.", "Min.", "Max.", "Actuel",
           "Jitter", "Reçus", "Envoyés", "État", "Graphique de latence"]
C_HOP, C_LOSS, C_IP, C_DNS, C_AVG, C_MIN, C_MAX, C_CUR, C_JIT, C_RECV, C_SENT, C_STATE, C_GRAPH = range(13)
SORT_ROLE = Qt.UserRole + 1
ROW_ROLE = Qt.UserRole + 2


def fmt_ms(v: float) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v:.1f}" if v < 100 else f"{v:.0f}"


class HopTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[HopRow] = []
        self.th: dict = {}
        self.scale_ms = 50.0
        self.fixed_scale = 0

    def set_thresholds(self, th: dict) -> None:
        self.th = dict(th)

    def set_rows(self, rows: list[HopRow]) -> None:
        # les sauts dont la lenteur est sans impact (ICMP dépriorisé) ne fixent pas l'échelle
        mx = [r.stats.max for r in rows if r.stats.received and r.level >= 0
              and not math.isnan(r.stats.max)]
        auto = max(mx) * 1.1 if mx else 50.0
        self.scale_ms = float(self.fixed_scale or max(auto, 10.0))
        if len(rows) == len(self.rows):
            self.rows = rows
            if rows:
                self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, C_GRAPH))
        else:
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()
        self.headerDataChanged.emit(Qt.Horizontal, C_GRAPH, C_GRAPH)

    # --------------------------------------------------------------- Qt
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        if role == Qt.DisplayRole:
            if section == C_GRAPH:
                return f"Graphique (0–{self.scale_ms:.0f} ms)"
            return COLUMNS[section]
        if role == Qt.ToolTipRole:
            return {C_LOSS: "Pourcentage de paquets perdus sur la période d'analyse",
                    C_JIT: "Variation moyenne entre deux réponses successives",
                    C_CUR: "Dernière mesure",
                    C_GRAPH: "Barre rouge : pertes · trait : min–max · point : moyenne"}.get(section)
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        st = r.stats
        c = index.column()
        if role == Qt.DisplayRole:
            if c == C_HOP:
                return str(r.ttl)
            if c == C_LOSS:
                return f"{st.loss_pct:.1f} %" if st.sent else "—"
            if c == C_IP:
                return r.ip or "*"
            if c == C_DNS:
                return r.hostname or ("—" if r.ip else "Pas de réponse")
            if c == C_AVG:
                return fmt_ms(st.avg)
            if c == C_MIN:
                return fmt_ms(st.min)
            if c == C_MAX:
                return fmt_ms(st.max)
            if c == C_CUR:
                return "perdu" if st.sent and st.last_lost else fmt_ms(st.current)
            if c == C_JIT:
                return fmt_ms(st.jitter)
            if c == C_RECV:
                return str(st.received)
            if c == C_SENT:
                return str(st.sent)
            if c == C_STATE:
                return r.state
            return None
        if role == SORT_ROLE:
            num = {C_HOP: r.ttl, C_LOSS: st.loss_pct, C_AVG: st.avg, C_MIN: st.min,
                   C_MAX: st.max, C_CUR: st.current, C_JIT: st.jitter, C_RECV: st.received,
                   C_SENT: st.sent, C_STATE: r.level, C_GRAPH: st.avg}
            if c in num:
                v = num[c]
                return -1.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
            return self.data(index, Qt.DisplayRole) or ""
        if role == ROW_ROLE:
            return r
        if role == Qt.TextAlignmentRole:
            if c in (C_IP, C_DNS, C_STATE):
                return int(Qt.AlignLeft | Qt.AlignVCenter)
            return int(Qt.AlignRight | Qt.AlignVCenter) if c != C_HOP else int(Qt.AlignCenter)
        if role == Qt.ForegroundRole:
            if c == C_LOSS and st.sent and st.lost and r.level >= 0:
                return QBrush(QColor(theme.level_color(max(1, loss_level(st.loss_pct, self.th)))))
            if c in (C_AVG, C_CUR) and self.th and r.level >= 0:
                lvl = latency_level(st.avg if c == C_AVG else st.current, self.th)
                if lvl > LEVEL_OK:
                    return QBrush(QColor(theme.level_color(lvl)))
            if c == C_STATE:
                return QBrush(QColor(theme.level_color(r.level)))
            if not r.ip and c in (C_IP, C_DNS):
                return QBrush(QColor(theme.palette().muted))
        if role == Qt.FontRole and r.is_destination:
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ToolTipRole:
            if c in (C_STATE, C_GRAPH, C_LOSS):
                return (f"Saut {r.ttl} — {r.ip or 'pas de réponse'}\n{r.state}\n"
                        f"Moy. {fmt_ms(st.avg)} ms · min {fmt_ms(st.min)} · max {fmt_ms(st.max)}"
                        f" · pertes {st.loss_pct:.1f} %")
            if c == C_DNS and r.hostname:
                return r.hostname
        return None


class GraphDelegate(QStyledItemDelegate):
    """Dessine dans la cellule : pertes (barre rouge), plage min‑max et
    moyenne colorée selon les seuils — comme la colonne graphique de
    PingPlotter."""

    def __init__(self, model: HopTableModel, parent=None):
        super().__init__(parent)
        self.model = model

    def paint(self, painter: QPainter, option, index):
        src = index.model().mapToSource(index) if hasattr(index.model(), "mapToSource") else index
        row: HopRow = self.model.data(src, ROW_ROLE)
        p = theme.palette()
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(p.selection))
        rect = QRectF(option.rect).adjusted(6, 4, -6, -4)
        st = row.stats
        scale = max(self.model.scale_ms, 1.0)
        th = self.model.th
        # seuils
        if th:
            for key, col in (("latency_warn", p.warn), ("latency_bad", p.bad)):
                v = th[key]
                if v < scale:
                    x = rect.left() + rect.width() * v / scale
                    c = QColor(col)
                    c.setAlpha(90)
                    painter.setPen(QPen(c, 1, Qt.DashLine))
                    painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
        # pertes
        if st.sent and st.lost:
            c = QColor(p.crit if row.level >= 0 else p.info)
            c.setAlpha(110)
            w = rect.width() * min(st.loss_pct, 100) / 100
            painter.fillRect(QRectF(rect.left(), rect.top(), w, rect.height()), c)
        if st.received:
            painter.setRenderHint(QPainter.Antialiasing)
            y = rect.center().y()
            x0 = rect.left() + rect.width() * min(st.min / scale, 1)
            x1 = rect.left() + rect.width() * min(st.max / scale, 1)
            xa = rect.left() + rect.width() * min(st.avg / scale, 1)
            painter.setPen(QPen(QColor(p.muted), 2))
            painter.drawLine(int(x0), int(y), int(x1), int(y))
            col = QColor(theme.level_color(latency_level(st.avg, th) if th else 0))
            if row.level < 0:
                col = QColor(p.info)
            painter.setPen(QPen(QColor(p.surface), 1))
            painter.setBrush(col)
            r = min(rect.height() / 2 - 1, 6)
            painter.drawEllipse(QRectF(xa - r, y - r, 2 * r, 2 * r))
            if not math.isnan(st.current) and not st.last_lost:
                xc = rect.left() + rect.width() * min(st.current / scale, 1)
                painter.setPen(QPen(QColor(p.text), 1.5))
                painter.drawLine(int(xc), int(y - r - 1), int(xc), int(y + r + 1))
        painter.restore()


class HopTable(QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.src = HopTableModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.src)
        self.proxy.setSortRole(SORT_ROLE)
        self.proxy.setDynamicSortFilter(True)
        self.setModel(self.proxy)
        self.setItemDelegateForColumn(C_GRAPH, GraphDelegate(self.src, self))
        self.setSortingEnabled(True)
        self.sortByColumn(C_HOP, Qt.AscendingOrder)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(28)
        self.setWordWrap(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.horizontalHeader()
        h.setHighlightSections(False)
        h.setStretchLastSection(True)
        widths = {C_HOP: 50, C_LOSS: 70, C_IP: 135, C_DNS: 220, C_AVG: 62, C_MIN: 62,
                  C_MAX: 62, C_CUR: 62, C_JIT: 62, C_RECV: 62, C_SENT: 70, C_STATE: 250}
        for c, w in widths.items():
            self.setColumnWidth(c, w)
        h.setSectionResizeMode(QHeaderView.Interactive)

    def selected_row(self) -> HopRow | None:
        idx = self.selectionModel().selectedRows()
        if not idx:
            return None
        return self.src.data(self.proxy.mapToSource(idx[0]), ROW_ROLE)

    def select_ttl(self, ttl: int | None) -> None:
        if ttl is None:
            self.clearSelection()
            return
        for i, r in enumerate(self.src.rows):
            if r.ttl == ttl:
                self.selectRow(self.proxy.mapFromSource(self.src.index(i, 0)).row())
                return
