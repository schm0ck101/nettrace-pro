"""Test d'endurance : plusieurs destinations à 0,5 s, rafraîchissement de
toutes les vues, tampon mémoire réduit pour forcer la rotation. Affiche la
mémoire et le nombre de threads pour détecter les fuites.

    python tests/stress_ui.py <dossier> [durée_s]
"""
import ctypes
import os
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp())
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 240
os.environ["NETTRACE_DATA_DIR"] = str(OUT / "data")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from nettrace.config import Settings  # noqa: E402
from nettrace.logger import EventBus, setup_logging  # noqa: E402


class PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "a", "b", "c", "d",
                                       "PagefileUsage", "PeakPagefileUsage")]


def rss_mb():
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    ctypes.windll.psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(),
                                             ctypes.byref(pmc), pmc.cb)
    return pmc.PagefileUsage / 1e6


setup_logging()
app = QApplication(sys.argv[:1])
from nettrace.net.monitor import Monitor  # noqa: E402
from nettrace.storage.history import HistoryDB  # noqa: E402
from nettrace.ui.main_window import MainWindow  # noqa: E402

settings = Settings()
settings.set("history.memory_samples", 300)       # rotation rapide du tampon
history = HistoryDB()
bus = EventBus()
monitor = Monitor(settings, history, bus)
win = MainWindow(settings, history, bus, monitor)
win.resize(1500, 950)
win.show()
for host in ("1.1.1.1", "8.8.8.8", "9.9.9.9", "www.google.com", "10.255.255.1", "192.168.1.1"):
    win.start_target(host, interval=0.5)

t0 = time.time()
last = 0
views = win.monitor_page.views()
i = 0
while time.time() - t0 < DUR:
    app.processEvents()
    win.monitor_page.tabs.setCurrentIndex(i % len(views))
    views[i % len(views)].tabs.setCurrentIndex((i // len(views)) % 4)
    win.tick()
    i += 1
    if time.time() - last > 30:
        last = time.time()
        print(f"t={time.time() - t0:5.0f}s  mémoire={rss_mb():6.1f} Mo  threads={threading.active_count():3d}  "
              f"échantillons={[s.buffer.total for s in monitor.sessions]}", flush=True)
    time.sleep(0.25)

ok = all(len(s.buffer) <= 300 for s in monitor.sessions)
print("tampons bornés :", ok, [len(s.buffer) for s in monitor.sessions])
win.quit()
t = time.time()
while time.time() - t < 3:
    app.processEvents()
    time.sleep(0.05)
print("sessions en base :", [(r.name, r.samples) for r in history.list_sessions()][:6])
print("OK" if ok else "ÉCHEC")
