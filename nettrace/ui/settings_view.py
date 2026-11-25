"""Page des paramètres (mesures, seuils d'alerte, interface, historique,
favoris)."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..config import APP_NAME, APP_VERSION, AUTHOR, AUTHOR_URL, INTERVALS, TIME_WINDOWS, data_dir
from .target_view import WINDOW_LABELS, interval_label


def _spin(lo, hi, step=1, suffix="", decimals=None):
    if decimals is None:
        s = QSpinBox()
    else:
        s = QDoubleSpinBox()
        s.setDecimals(decimals)
    s.setRange(lo, hi)
    s.setSingleStep(step)
    if suffix:
        s.setSuffix(suffix)
    return s


class SettingsView(QWidget):
    applied = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        t = QLabel("Paramètres")
        t.setObjectName("PageTitle")
        outer.addWidget(t)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # --- Mesures ------------------------------------------------------
        g = QGroupBox("Mesures (nouvelles surveillances)")
        f = QFormLayout(g)
        self.interval = QComboBox()
        for v in INTERVALS:
            self.interval.addItem(interval_label(v), v)
        self.timeout = _spin(200, 10000, 100, " ms")
        self.size = _spin(0, 1472, 8, " octets")
        self.max_hops = _spin(1, 64)
        self.family = QComboBox()
        self.family.addItem("Automatique (IPv4 en priorité)", "auto")
        self.family.addItem("IPv4 uniquement", "ipv4")
        self.family.addItem("IPv6 uniquement", "ipv6")
        self.mode = QComboBox()
        self.mode.addItem("Traceroute continu (tous les sauts)", "trace")
        self.mode.addItem("Ping (destination seule)", "ping")
        self.rdns = QCheckBox("Résoudre les noms DNS des sauts")
        self.focus = QComboBox()
        for n in (10, 50, 100, 200, 500, 0):
            self.focus.addItem("Tous" if n == 0 else f"{n} échantillons", n)
        f.addRow("Intervalle par défaut", self.interval)
        f.addRow("Délai d'attente", self.timeout)
        f.addRow("Taille des paquets", self.size)
        f.addRow("Nombre max. de sauts", self.max_hops)
        f.addRow("Version IP", self.family)
        f.addRow("Mode", self.mode)
        f.addRow("Période d'analyse", self.focus)
        f.addRow("", self.rdns)
        grid.addWidget(g, 0, 0)

        # --- Seuils -------------------------------------------------------
        g = QGroupBox("Seuils d'alerte")
        f = QFormLayout(g)
        self.lat_warn = _spin(1, 10000, 5, " ms")
        self.lat_bad = _spin(1, 10000, 5, " ms")
        self.lat_crit = _spin(1, 20000, 5, " ms")
        self.loss_warn = _spin(0.1, 100, 0.5, " %", 1)
        self.loss_crit = _spin(0.1, 100, 0.5, " %", 1)
        self.jit = _spin(1, 5000, 1, " ms")
        self.outage = _spin(1, 100)
        self.win = _spin(3, 500)
        f.addRow("<span style='color:#f4c430'>■</span> Latence élevée à partir de", self.lat_warn)
        f.addRow("<span style='color:#ff8c1a'>■</span> Forte dégradation à partir de", self.lat_bad)
        f.addRow("<span style='color:#ff4d4f'>■</span> Problème important à partir de", self.lat_crit)
        f.addRow("Pertes : alerte à partir de", self.loss_warn)
        f.addRow("Pertes : critique à partir de", self.loss_crit)
        f.addRow("Jitter important à partir de", self.jit)
        f.addRow("Interruption après (pertes consécutives)", self.outage)
        f.addRow("Fenêtre d'évaluation (échantillons)", self.win)
        grid.addWidget(g, 0, 1)

        # --- Interface ----------------------------------------------------
        g = QGroupBox("Interface et notifications")
        f = QFormLayout(g)
        self.theme = QComboBox()
        self.theme.addItem("Sombre", "dark")
        self.theme.addItem("Clair", "light")
        self.window = QComboBox()
        for w in TIME_WINDOWS:
            self.window.addItem(WINDOW_LABELS[w], w)
        self.scale = _spin(0, 20000, 10, " ms")
        self.scale.setSpecialValueText("Automatique")
        self.notif = QCheckBox("Afficher les notifications dans la fenêtre")
        self.tray_notif = QCheckBox("Notifications Windows quand la fenêtre est en arrière‑plan")
        self.tray_min = QCheckBox("Fermer = réduire dans la zone de notification")
        self.sound = QCheckBox("Signal sonore lors d'une interruption")
        f.addRow("Thème", self.theme)
        f.addRow("Durée affichée sur le graphique", self.window)
        f.addRow("Échelle de latence", self.scale)
        f.addRow("", self.notif)
        f.addRow("", self.tray_notif)
        f.addRow("", self.tray_min)
        f.addRow("", self.sound)
        grid.addWidget(g, 1, 0)

        # --- Historique ---------------------------------------------------
        g = QGroupBox("Historique")
        f = QFormLayout(g)
        self.record = QCheckBox("Enregistrer automatiquement les sessions")
        self.retention = _spin(0, 3650, 1, " jours")
        self.retention.setSpecialValueText("Illimitée")
        self.memory = _spin(1000, 500000, 1000, " échantillons")
        f.addRow("", self.record)
        f.addRow("Conservation", self.retention)
        f.addRow("Mémoire par destination", self.memory)
        b = QPushButton("Ouvrir le dossier des données")
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir()))))
        f.addRow("", b)
        grid.addWidget(g, 1, 1)

        # --- Favoris ------------------------------------------------------
        g = QGroupBox("Destinations favorites (démarrage rapide)")
        v = QVBoxLayout(g)
        self.fav = QTableWidget(0, 2)
        self.fav.setHorizontalHeaderLabels(["Nom", "Adresse ou domaine"])
        self.fav.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.fav.verticalHeader().hide()
        self.fav.setMinimumHeight(170)
        v.addWidget(self.fav)
        hb = QHBoxLayout()
        add = QPushButton("Ajouter")
        add.clicked.connect(lambda: self._add_fav("", ""))
        rem = QPushButton("Retirer")
        rem.clicked.connect(lambda: self.fav.removeRow(self.fav.currentRow()))
        hb.addWidget(add)
        hb.addWidget(rem)
        hb.addStretch(1)
        v.addLayout(hb)
        grid.addWidget(g, 2, 0, 1, 2)

        about = QLabel(f"<b>{APP_NAME}</b> {APP_VERSION} — diagnostic réseau ICMP / traceroute. Créé par <b>{AUTHOR}</b> ({AUTHOR_URL}). "
                       f"Données : {data_dir()}")
        about.setObjectName("Muted")
        about.setWordWrap(True)
        grid.addWidget(about, 3, 0, 1, 2)

        bar = QHBoxLayout()
        reset = QPushButton("Valeurs par défaut")
        reset.clicked.connect(self._reset)
        bar.addWidget(reset)
        bar.addStretch(1)
        cancel = QPushButton("Annuler les modifications")
        cancel.clicked.connect(self.load)
        save = QPushButton("Enregistrer")
        save.setObjectName("Primary")
        save.clicked.connect(self.save)
        bar.addWidget(cancel)
        bar.addWidget(save)
        outer.addLayout(bar)
        self.load()

    def _add_fav(self, name, host):
        r = self.fav.rowCount()
        self.fav.insertRow(r)
        self.fav.setItem(r, 0, QTableWidgetItem(name))
        self.fav.setItem(r, 1, QTableWidgetItem(host))

    @staticmethod
    def _set_cb(cb, value):
        i = cb.findData(value)
        if i >= 0:
            cb.setCurrentIndex(i)

    def load(self):
        s = self.settings
        m, th, ui, h = (s.section(k) for k in ("measure", "thresholds", "ui", "history"))
        self._set_cb(self.interval, m["interval"])
        self.timeout.setValue(m["timeout_ms"])
        self.size.setValue(m["packet_size"])
        self.max_hops.setValue(m["max_hops"])
        self._set_cb(self.family, m["family"])
        self._set_cb(self.mode, m["mode"])
        self._set_cb(self.focus, m["focus"])
        self.rdns.setChecked(m["reverse_dns"])
        self.lat_warn.setValue(int(th["latency_warn"]))
        self.lat_bad.setValue(int(th["latency_bad"]))
        self.lat_crit.setValue(int(th["latency_crit"]))
        self.loss_warn.setValue(th["loss_warn"])
        self.loss_crit.setValue(th["loss_crit"])
        self.jit.setValue(int(th["jitter_warn"]))
        self.outage.setValue(th["outage_count"])
        self.win.setValue(th["alert_window"])
        self._set_cb(self.theme, ui["theme"])
        self._set_cb(self.window, ui["time_window"])
        self.scale.setValue(int(ui.get("graph_scale_ms", 0)))
        self.notif.setChecked(ui["notifications"])
        self.tray_notif.setChecked(ui["tray_notifications"])
        self.tray_min.setChecked(ui["minimize_to_tray"])
        self.sound.setChecked(ui.get("sound", False))
        self.record.setChecked(h["record"])
        self.retention.setValue(h["retention_days"])
        self.memory.setValue(h["memory_samples"])
        self.fav.setRowCount(0)
        for fav in s.get("favorites", []):
            self._add_fav(fav.get("name", ""), fav.get("host", ""))

    def save(self):
        if not (self.lat_warn.value() < self.lat_bad.value() < self.lat_crit.value()):
            QMessageBox.warning(self, "Seuils", "Les seuils de latence doivent être croissants "
                                "(élevée < forte dégradation < problème important).")
            return
        if self.loss_warn.value() >= self.loss_crit.value():
            QMessageBox.warning(self, "Seuils", "Le seuil critique de pertes doit être supérieur "
                                "au seuil d'alerte.")
            return
        s = self.settings
        s.set("measure.interval", self.interval.currentData())
        s.set("measure.timeout_ms", self.timeout.value())
        s.set("measure.packet_size", self.size.value())
        s.set("measure.max_hops", self.max_hops.value())
        s.set("measure.family", self.family.currentData())
        s.set("measure.mode", self.mode.currentData())
        s.set("measure.focus", self.focus.currentData())
        s.set("measure.reverse_dns", self.rdns.isChecked())
        s.set("thresholds.latency_warn", float(self.lat_warn.value()))
        s.set("thresholds.latency_bad", float(self.lat_bad.value()))
        s.set("thresholds.latency_crit", float(self.lat_crit.value()))
        s.set("thresholds.loss_warn", float(self.loss_warn.value()))
        s.set("thresholds.loss_crit", float(self.loss_crit.value()))
        s.set("thresholds.jitter_warn", float(self.jit.value()))
        s.set("thresholds.outage_count", self.outage.value())
        s.set("thresholds.alert_window", self.win.value())
        s.set("ui.theme", self.theme.currentData())
        s.set("ui.time_window", self.window.currentData())
        s.set("ui.graph_scale_ms", self.scale.value())
        s.set("ui.notifications", self.notif.isChecked())
        s.set("ui.tray_notifications", self.tray_notif.isChecked())
        s.set("ui.minimize_to_tray", self.tray_min.isChecked())
        s.set("ui.sound", self.sound.isChecked())
        s.set("history.record", self.record.isChecked())
        s.set("history.retention_days", self.retention.value())
        s.set("history.memory_samples", self.memory.value())
        favs = []
        for r in range(self.fav.rowCount()):
            name = (self.fav.item(r, 0).text() if self.fav.item(r, 0) else "").strip()
            host = (self.fav.item(r, 1).text() if self.fav.item(r, 1) else "").strip()
            if host:
                favs.append({"name": name or host, "host": host})
        s.set("favorites", favs)
        try:
            s.save()
        except OSError as exc:
            QMessageBox.warning(self, "Paramètres", f"Enregistrement impossible : {exc}")
            return
        self.applied.emit()

    def _reset(self):
        if QMessageBox.question(self, "Paramètres", "Revenir aux valeurs par défaut "
                                "(les favoris sont conservés) ?") == QMessageBox.Yes:
            self.settings.reset()
            self.load()
            self.save()
