"""Point d'entrée de l'application."""
from __future__ import annotations

import argparse
import ctypes
import logging
import sys
import threading
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import APP_NAME, APP_VERSION, Settings, data_dir
from .logger import EventBus, setup_logging

log = logging.getLogger("nettrace")


def _excepthook(exc_type, exc, tb):
    """Une erreur inattendue est journalisée et signalée, sans fermer
    l'application ni interrompre les mesures."""
    log.error("Exception non gérée :\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
    app = QApplication.instance()
    if app is not None and threading.current_thread() is threading.main_thread():
        QMessageBox.warning(None, APP_NAME,
                            f"Une erreur inattendue s'est produite :\n{exc}\n\n"
                            f"Détails dans {data_dir() / 'nettrace.log'}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="NetTracePro", description=APP_NAME)
    parser.add_argument("--debug", action="store_true", help="journalisation détaillée")
    parser.add_argument("targets", nargs="*", help="destinations à surveiller au démarrage")
    args = parser.parse_args(argv)

    setup_logging(args.debug)
    log.info("Démarrage %s %s (Python %s)", APP_NAME, APP_VERSION, sys.version.split()[0])
    sys.excepthook = _excepthook
    threading.excepthook = lambda a: log.error("Exception dans %s", a.thread.name,
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    if sys.platform == "win32":
        try:  # icône correcte dans la barre des tâches
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NetTracePro.App")
        except Exception:
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("NetTracePro")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    from .net.monitor import Monitor
    from .net.dns import reverse_dns
    from .net.tracer import shutdown_pool
    from .storage.history import HistoryDB
    from .ui import theme
    from .ui.main_window import MainWindow

    settings = Settings()
    theme.apply_theme(settings.get("ui.theme", "dark"))
    app.setWindowIcon(QIcon(theme.app_icon_pixmap(256)))

    history = HistoryDB()
    threading.Thread(target=history.maintenance,
                     args=(int(settings.get("history.retention_days", 90)),),
                     name="maintenance", daemon=True).start()
    bus = EventBus()
    monitor = Monitor(settings, history, bus)
    win = MainWindow(settings, history, bus, monitor)
    win.show()
    for t in args.targets:
        win.start_target(t)

    code = app.exec()
    try:
        settings.save()
    except OSError:
        pass
    reverse_dns.shutdown()
    shutdown_pool()
    log.info("Arrêt")
    return code
