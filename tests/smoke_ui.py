"""Test fonctionnel de l'interface complète, avec de vraies mesures.

Lance la fenêtre principale (hors écran par défaut), démarre plusieurs
surveillances (dont une injoignable et un nom invalide), navigue dans toutes
les pages, bascule le thème, exporte CSV + PDF, rouvre la session depuis
l'historique et ouvre la comparaison. Des captures sont écrites dans le
dossier passé en argument.

    python tests/smoke_ui.py <dossier_sortie> [durée_s]
"""
import os
import sys
import tempfile
import time
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp())
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 15
OUT.mkdir(parents=True, exist_ok=True)
os.environ["NETTRACE_DATA_DIR"] = str(OUT / "data")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

# Pas de boîtes de dialogue bloquantes pendant le test
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

from nettrace.config import Settings  # noqa: E402
from nettrace.logger import EventBus, setup_logging  # noqa: E402

setup_logging(True)
app = QApplication(sys.argv[:1])
app.setStyle("Fusion")
from nettrace.net.monitor import Monitor  # noqa: E402
from nettrace.storage.export import export_csv, export_pdf  # noqa: E402
from nettrace.storage.history import HistoryDB  # noqa: E402
from nettrace.ui import theme  # noqa: E402
from nettrace.ui.history_view import CompareDialog  # noqa: E402
from nettrace.ui.main_window import MainWindow  # noqa: E402

settings = Settings()
theme.apply_theme("dark")
history = HistoryDB()
bus = EventBus()
events = []
bus.event.connect(events.append)
monitor = Monitor(settings, history, bus)
win = MainWindow(settings, history, bus, monitor)
win.resize(1600, 980)
win.show()


def pump(seconds):
    t = time.time() + seconds
    while time.time() < t:
        app.processEvents()
        time.sleep(0.02)


def shot(name):
    pump(0.3)
    win.grab().save(str(OUT / f"{name}.png"))


errors = []
s1 = win.start_target("one.one.one.one", name="Cloudflare", interval=0.5)
s2 = win.start_target("8.8.8.8", name="Google DNS", mode="ping", interval=1.0)
s3 = win.start_target("10.255.255.1", name="Injoignable", interval=1.0, timeout_ms=800)
s4 = win.start_target("domaine-inexistant.invalid", name="DNS invalide")
dup = win.start_target("8.8.8.8")
assert dup is s2, "une destination déjà surveillée doit être réutilisée"
pump(DUR)

win.monitor_page.tabs.setCurrentIndex(0)
shot("01_surveillance_cloudflare")
v = win.monitor_page.views()[0]
for i in range(1, 4):
    v.tabs.setCurrentIndex(i)
    shot(f"02_graph_{i}")
v.tabs.setCurrentIndex(0)
# sélection d'un saut + clic sur la frise
v._select_hop(1)
v._timeline_clicked(time.time() - DUR / 2)
shot("03_saut1_focus")
v._go_live()
v._select_hop(None)
win.monitor_page.tabs.setCurrentIndex(2)
shot("04_injoignable")
win.show_page(0)
shot("05_dashboard")
win.show_page(4)
pump(4)
shot("06_reseau")
win.show_page(3)
shot("07_evenements")
win.show_page(5)
shot("08_parametres")
win.toggle_theme()
win.show_page(1)
win.monitor_page.tabs.setCurrentIndex(0)
shot("09_surveillance_clair")
win.show_page(0)
shot("10_dashboard_clair")
win.toggle_theme()

# pause / reprise
s1.pause()
pump(1.5)
n_paused = len(s1.buffer)
pump(1.5)
assert len(s1.buffer) - n_paused <= 1, "aucune mesure pendant la pause"
s1.resume()
pump(2)
assert len(s1.buffer) > n_paused, "les mesures reprennent"

# exports
th = settings.section("thresholds")
files = export_csv(s1, OUT / "cloudflare.csv", th)
history.flush()
export_pdf(s1, OUT / "cloudflare.pdf", th, history.list_events(s1.db_id), "Test")
assert (OUT / "cloudflare.pdf").stat().st_size > 20000

# arrêt + historique
monitor.stop(s1)
pump(3)
history.flush()
win.show_page(2)
pump(0.5)
shot("11_historique")
recs = history.list_sessions()
rec = next(r for r in recs if r.id == s1.db_id)
assert rec.samples > 10, rec
hv = win.history_view
src = hv._source(rec)
win.open_history(src)
shot("12_historique_ouvert")
dlg = CompareDialog(history, [r for r in recs if r.samples][:2])
dlg.resize(1200, 700)
dlg.show()
pump(0.5)
dlg.grab().save(str(OUT / "13_comparaison.png"))
dlg.close()
dlg1 = CompareDialog(history, [rec])
dlg1.split_cb.setCurrentIndex(0)
dlg1.show()
pump(0.3)
dlg1.close()

print("échantillons :", {s.name: len(s.buffer) for s in monitor.sessions})
print("états :", {s.name: (s.state, s.status_text) for s in monitor.sessions})
print("événements :")
for e in events:
    print("  ", e.level, e.category, e.target, "-", e.message)
print("fichiers :", [str(f) for f in files])
win.quit()
pump(1)
print("OK")
