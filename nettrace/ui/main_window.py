"""Fenêtre principale : barre latérale, pages, onglets des destinations,
icône de la zone de notification et rafraîchissement périodique."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QMenu, QMessageBox, QPushButton, QStackedWidget, QSystemTrayIcon,
                               QTabWidget, QVBoxLayout, QWidget)

from ..config import APP_NAME, APP_VERSION, AUTHOR, AUTHOR_URL, INTERVALS
from ..events import NetEvent
from ..net.dns import clean_host
from . import theme
from .dashboard import Dashboard
from .events_view import EventsView
from .history_view import HistoryView
from .netinfo_view import NetInfoView
from .notifications import Notifier
from .settings_view import SettingsView
from .target_view import StatusDot, TargetView, interval_label

log = logging.getLogger(__name__)


def dot_icon(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.end()
    return QIcon(pm)


class MonitorPage(QWidget):
    """Page « Surveillance » : saisie de destination + un onglet par cible."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(10)
        t = QLabel("Surveillance")
        t.setObjectName("PageTitle")
        root.addWidget(t)

        bar = QFrame()
        bar.setObjectName("Panel")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 10, 12, 10)
        self.dest = QComboBox()
        self.dest.setEditable(True)
        self.dest.setMinimumWidth(320)
        self.dest.lineEdit().setPlaceholderText("Adresse IP, nom de domaine ou serveur (ex. 8.8.8.8)")
        self.dest.lineEdit().returnPressed.connect(self.start_clicked)
        self.mode = QComboBox()
        self.mode.addItem("Traceroute continu", "trace")
        self.mode.addItem("Ping seul", "ping")
        self.family = QComboBox()
        self.family.addItem("IPv4/IPv6 auto", "auto")
        self.family.addItem("IPv4", "ipv4")
        self.family.addItem("IPv6", "ipv6")
        self.interval = QComboBox()
        for v in INTERVALS:
            self.interval.addItem(f"Toutes les {interval_label(v)}", v)
        self.count = QComboBox()
        for label, n in (("Surveillance continue", 0), ("10 échantillons", 10),
                         ("50 échantillons", 50), ("100 échantillons", 100), ("500 échantillons", 500)):
            self.count.addItem(label, n)
        self.start_btn = QPushButton("  Démarrer")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self.start_clicked)
        lab = QLabel("Destination")
        lab.setObjectName("Muted")
        bl.addWidget(lab)
        bl.addWidget(self.dest, 1)
        bl.addWidget(self.mode)
        bl.addWidget(self.family)
        bl.addWidget(self.interval)
        bl.addWidget(self.count)
        bl.addWidget(self.start_btn)
        root.addWidget(bar)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.empty = QLabel("Saisissez une destination puis cliquez sur « Démarrer ».\n"
                            "Chaque destination s'ouvre dans son propre onglet, avec ses graphiques "
                            "et ses statistiques.\nVous pouvez en surveiller plusieurs à la fois.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("Muted")
        root.addWidget(self.empty, 1)
        root.addWidget(self.tabs, 1)
        self.tabs.hide()
        self.reload_defaults()

    def reload_defaults(self):
        s = self.win.settings
        cur = self.dest.currentText()
        self.dest.clear()
        for fav in s.get("favorites", []):
            self.dest.addItem(f"{fav['host']}", fav["host"])
            self.dest.setItemData(self.dest.count() - 1, f"Favori : {fav['name']}", Qt.ToolTipRole)
        for h in s.get("recent", []):
            if self.dest.findData(h) < 0:
                self.dest.addItem(h, h)
        self.dest.setEditText(cur)
        m = s.section("measure")
        for cb, val in ((self.mode, m["mode"]), (self.family, m["family"]),
                        (self.interval, m["interval"])):
            i = cb.findData(val)
            if i >= 0:
                cb.setCurrentIndex(i)

    def start_clicked(self):
        host = clean_host(self.dest.currentText())
        if not host:
            QMessageBox.information(self, APP_NAME, "Saisissez une adresse IP ou un nom de domaine.")
            return
        name = next((f["name"] for f in self.win.settings.get("favorites", [])
                     if f["host"].lower() == host.lower()), None)
        self.win.start_target(host, name=name, mode=self.mode.currentData(),
                              family=self.family.currentData(),
                              interval=self.interval.currentData(), count=self.count.currentData())

    def views(self) -> list[TargetView]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def add_view(self, view: TargetView, title: str):
        i = self.tabs.addTab(view, title)
        self.tabs.setCurrentIndex(i)
        self._sync_empty()
        return i

    def close_tab(self, i: int):
        view = self.tabs.widget(i)
        self.tabs.removeTab(i)
        if view.live:
            self.win.monitor.remove(view.source)
        view.deleteLater()
        self._sync_empty()

    def _sync_empty(self):
        has = self.tabs.count() > 0
        self.tabs.setVisible(has)
        self.empty.setVisible(not has)


class MainWindow(QMainWindow):
    def __init__(self, settings, history, bus, monitor):
        super().__init__()
        self.settings = settings
        self.history = history
        self.bus = bus
        self.monitor = monitor
        self._quitting = False
        self.setWindowTitle(f"{APP_NAME}")
        self.setWindowIcon(QIcon(theme.app_icon_pixmap(256)))
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)

        central = QWidget()
        central.setObjectName("Central")
        self.setCentralWidget(central)
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # --- Barre latérale ---------------------------------------------
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(230)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 12, 14)
        sl.setSpacing(4)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(theme.app_icon_pixmap(64).scaled(36, 36, Qt.KeepAspectRatio,
                                                        Qt.SmoothTransformation))
        head.addWidget(logo)
        hb = QVBoxLayout()
        hb.setSpacing(0)
        tt = QLabel(APP_NAME)
        tt.setObjectName("AppTitle")
        st = QLabel(f"Diagnostic réseau · v{APP_VERSION}")
        st.setObjectName("AppSubtitle")
        hb.addWidget(tt)
        hb.addWidget(st)
        head.addLayout(hb, 1)
        sl.addLayout(head)
        sl.addSpacing(18)

        self.stack = QStackedWidget()
        self.dashboard = Dashboard(monitor, settings)
        self.monitor_page = MonitorPage(self)
        self.history_view = HistoryView(history, settings)
        self.events_view = EventsView(history)
        self.netinfo = NetInfoView()
        self.settings_view = SettingsView(settings)
        self.pages = [
            (theme.G_DASHBOARD, "Tableau de bord", self.dashboard),
            (theme.G_MONITOR, "Surveillance", self.monitor_page),
            (theme.G_HISTORY, "Historique", self.history_view),
            (theme.G_EVENTS, "Événements", self.events_view),
            (theme.G_NETWORK, "Réseau local", self.netinfo),
            (theme.G_SETTINGS, "Paramètres", self.settings_view),
        ]
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.nav_buttons = []
        for i, (glyph, label, page) in enumerate(self.pages):
            b = QPushButton(f"  {label}")
            b.setObjectName("NavButton")
            b.setCheckable(True)
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("glyph", glyph)
            self.nav.addButton(b, i)
            self.nav_buttons.append(b)
            sl.addWidget(b)
            self.stack.addWidget(page)
        self.nav.idClicked.connect(self.show_page)
        sl.addStretch(1)

        # état global
        status = QFrame()
        status.setObjectName("Panel")
        stl = QVBoxLayout(status)
        stl.setContentsMargins(12, 10, 12, 10)
        srow = QHBoxLayout()
        self.g_dot = StatusDot(12)
        srow.addWidget(self.g_dot)
        self.g_text = QLabel("Inactif")
        self.g_text.setObjectName("SectionTitle")
        srow.addWidget(self.g_text, 1)
        stl.addLayout(srow)
        self.ip_lbl = QLabel("IP locale : …")
        self.ip_lbl.setObjectName("Muted")
        self.pub_lbl = QLabel("IP publique : …")
        self.pub_lbl.setObjectName("Muted")
        stl.addWidget(self.ip_lbl)
        stl.addWidget(self.pub_lbl)
        sl.addWidget(status)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("NavButton")
        self.theme_btn.clicked.connect(self.toggle_theme)
        sl.addWidget(self.theme_btn)

        # signature de l'auteur (clic = site)
        brand = QLabel()
        brand.setAlignment(Qt.AlignCenter)
        pm = theme.brand_pixmap(16)
        if pm.isNull():
            brand.setText(f"Créé par {AUTHOR}")
        else:
            brand.setPixmap(pm)
        brand.setToolTip(f"{APP_NAME} — créé par {AUTHOR} · {AUTHOR_URL}")
        brand.setCursor(Qt.PointingHandCursor)
        brand.mousePressEvent = lambda e: QDesktopServices.openUrl(QUrl(AUTHOR_URL))
        brand.setContentsMargins(0, 6, 0, 2)
        sl.addWidget(brand)

        lay.addWidget(side)
        lay.addWidget(self.stack, 1)

        # --- Liaisons ----------------------------------------------------
        self.dashboard.open_session.connect(self.focus_session)
        self.dashboard.quick_start.connect(lambda h, n: self.start_target(h, name=n))
        self.dashboard.start_all_favorites.connect(self.start_favorites)
        self.history_view.open_source.connect(self.open_history)
        self.settings_view.applied.connect(self.apply_settings)
        self.netinfo.info_changed.connect(self._netinfo_changed)
        self.monitor.sessions_changed.connect(self.dashboard.sync_cards)
        bus.event.connect(self.on_event)

        # --- Zone de notification -----------------------------------------
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip(APP_NAME)
            menu = QMenu()
            a_show = QAction("Afficher NetTrace Pro", self)
            a_show.triggered.connect(self.restore)
            a_pause = QAction("Tout mettre en pause", self)
            a_pause.triggered.connect(self.monitor.pause_all)
            a_resume = QAction("Tout reprendre", self)
            a_resume.triggered.connect(self.monitor.resume_all)
            a_quit = QAction("Quitter", self)
            a_quit.triggered.connect(self.quit)
            for a in (a_show, a_pause, a_resume):
                menu.addAction(a)
            menu.addSeparator()
            menu.addAction(a_quit)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(
                lambda r: self.restore() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
            self.tray.show()
        self.notifier = Notifier(self, self.tray, settings)

        QShortcut(QKeySequence("Ctrl+N"), self, activated=self._new_target)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self._close_current)
        QShortcut(QKeySequence("F5"), self, activated=self._refresh_current)

        # --- Rafraîchissement ----------------------------------------------
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)

        self.apply_theme_icons()
        self.nav_buttons[0].setChecked(True)
        self.show_page(0)
        self.events_view.reload()
        for ev in reversed(self.events_view.events[:30]):
            self.dashboard.add_event(ev)
        self.netinfo.refresh()
        self._pub_timer = QTimer(self)
        self._pub_timer.timeout.connect(self.netinfo.refresh)
        self._pub_timer.start(10 * 60 * 1000)

    # ------------------------------------------------------------ pages
    def show_page(self, i: int):
        self.nav_buttons[i].setChecked(True)
        page = self.pages[i][2]
        self.stack.setCurrentWidget(page)
        if page is self.history_view:
            self.history_view.reload()
        self.tick()

    def _new_target(self):
        self.show_page(1)
        self.monitor_page.dest.setFocus()
        self.monitor_page.dest.lineEdit().selectAll()

    def _close_current(self):
        if self.stack.currentWidget() is self.monitor_page and self.monitor_page.tabs.count():
            self.monitor_page.close_tab(self.monitor_page.tabs.currentIndex())

    def _refresh_current(self):
        if self.stack.currentWidget() is self.history_view:
            self.history_view.reload()
        elif self.stack.currentWidget() is self.netinfo:
            self.netinfo.refresh()
        self.tick()

    # -------------------------------------------------------- sessions
    def start_target(self, host: str, name: str | None = None, **over):
        host = clean_host(host)
        if not host:
            return None
        existing = self.monitor.find(host)
        if existing is not None:
            self.focus_session(existing)
            return existing
        s = self.monitor.start(host, name=name, **over)
        view = self._make_view(s)
        self.monitor_page.add_view(view, s.name)
        self.monitor_page.reload_defaults()
        self.show_page(1)
        return s

    def _make_view(self, s) -> TargetView:
        view = TargetView(s, self.settings, self.history)
        view.request_stop.connect(lambda v: self.monitor.stop(v.source))
        view.request_restart.connect(self.restart_view)
        return view

    def restart_view(self, view: TargetView):
        tabs = self.monitor_page.tabs
        i = tabs.indexOf(view)
        s = self.monitor.restart(view.source)
        nv = self._make_view(s)
        tabs.removeTab(i)
        view.deleteLater()
        tabs.insertTab(i, nv, s.name)
        tabs.setCurrentIndex(i)

    def start_favorites(self):
        favs = self.settings.get("favorites", [])
        if not favs:
            QMessageBox.information(self, APP_NAME, "Aucun favori : ajoutez‑en dans les Paramètres.")
            return
        for f in favs:
            if self.monitor.find(f["host"]) is None:
                s = self.monitor.start(f["host"], name=f["name"])
                self.monitor_page.add_view(self._make_view(s), s.name)
        self.show_page(0)

    def focus_session(self, s):
        for i, v in enumerate(self.monitor_page.views()):
            if v.source is s:
                self.monitor_page.tabs.setCurrentIndex(i)
                break
        self.show_page(1)

    def open_history(self, src):
        title = f"🕘 {src.name} ({time.strftime('%d/%m %H:%M', time.localtime(src.rec.started))})"
        view = TargetView(src, self.settings, self.history)
        self.monitor_page.add_view(view, title)
        self.show_page(1)

    # ------------------------------------------------------ événements
    def on_event(self, ev: NetEvent):
        self.events_view.add_event(ev)
        self.dashboard.add_event(ev)
        self.notifier.notify(ev)

    def _netinfo_changed(self, info):
        self.ip_lbl.setText(f"IP locale : {info.local_v4 or '—'}"
                            + (f"\nIPv6 : {info.local_v6}" if info.local_v6 else ""))
        self.pub_lbl.setText(f"IP publique : {info.public_v4 or '—'}")
        self.dashboard.set_netinfo(
            f"IP locale {info.local_v4 or '—'}   ·   IP publique {info.public_v4 or '—'}"
            + (f"\nIPv6 {info.public_v6}" if info.public_v6 else ""))

    # ------------------------------------------------------------ thème
    def apply_theme_icons(self):
        p = theme.palette()
        for b in self.nav_buttons:
            b.setIcon(theme.glyph_icon(b.property("glyph"), p.text))
        dark = p.name == "dark"
        self.theme_btn.setIcon(theme.glyph_icon("" if dark else "", p.text))
        self.theme_btn.setText("  Mode clair" if dark else "  Mode sombre")

    def toggle_theme(self):
        new = "light" if theme.palette().name == "dark" else "dark"
        self.settings.set("ui.theme", new)
        try:
            self.settings.save()
        except OSError:
            pass
        self.settings_view.load()
        self._apply_theme(new)

    def _apply_theme(self, name: str):
        theme.apply_theme(name)
        self.apply_theme_icons()
        for v in self.monitor_page.views():
            v.apply_theme()
        self.dashboard.sync_cards()
        self.tick()

    def apply_settings(self):
        self.monitor.apply_settings()
        if self.settings.get("ui.theme") != theme.palette().name:
            self._apply_theme(self.settings.get("ui.theme"))
        for v in self.monitor_page.views():
            v.apply_settings()
        self.dashboard.rebuild_quick()
        self.monitor_page.reload_defaults()
        QMessageBox.information(self, APP_NAME, "Paramètres enregistrés et appliqués.")

    # ---------------------------------------------------------- minuterie
    def tick(self):
        try:
            page = self.stack.currentWidget()
            if page is self.monitor_page and self.isVisible():
                v = self.monitor_page.tabs.currentWidget()
                if v is not None:
                    v.refresh()
            elif page is self.dashboard and self.isVisible():
                self.dashboard.refresh()
            # icônes des onglets
            p = theme.palette()
            for i, v in enumerate(self.monitor_page.views()):
                s = v.source
                col = (theme.level_color(s.level) if s.state == "running" else
                       p.crit if s.state == "error" else p.muted)
                self.monitor_page.tabs.setTabIcon(i, dot_icon(col))
            # état global
            running = [s for s in self.monitor.sessions if s.state in ("running", "paused", "error")]
            if running:
                worst = max(running, key=lambda s: s.level)
                self.g_dot.set_color(theme.level_color(worst.level))
                self.g_text.setText("Connexion normale" if worst.level == 0 else worst.status_text)
                if self.tray:
                    self.tray.setToolTip(f"{APP_NAME} — {self.g_text.text()}")
            else:
                self.g_dot.set_color(p.muted)
                self.g_text.setText("Aucune surveillance")
            fast = any(s.opt.interval < 1 and s.state == "running" for s in self.monitor.sessions)
            want = 500 if fast else 1000
            if self.timer.interval() != want:
                self.timer.setInterval(want)
        except Exception:
            log.exception("Rafraîchissement de l'interface")

    # --------------------------------------------------------- fenêtre
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.notifier.reposition()

    def restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if (not self._quitting and self.tray and self.settings.get("ui.minimize_to_tray", False)):
            e.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, "La surveillance continue en arrière‑plan.",
                                  QSystemTrayIcon.Information, 3000)
            return
        self._shutdown()
        e.accept()

    def quit(self):
        self._quitting = True
        self.close()

    def _shutdown(self):
        self.timer.stop()
        try:
            self.monitor.shutdown()
            self.history.close()
        except Exception:
            log.exception("Fermeture")
        if self.tray:
            self.tray.hide()
        from PySide6.QtWidgets import QApplication
        QApplication.instance().quit()
