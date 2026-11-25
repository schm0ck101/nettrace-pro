"""Exports CSV (format Excel français : « ; » et virgule décimale) et
rapport PDF (Qt : QTextDocument + QPdfWriter, aucune dépendance en plus)."""
from __future__ import annotations

import csv
import html
import math
import time
from pathlib import Path

import numpy as np

from ..config import APP_NAME, APP_VERSION, AUTHOR
from ..net.analysis import analyze
from ..net.stats import NOT_PROBED, series_stats


def _num(v) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or v == NOT_PROBED)):
        return ""
    return f"{v:.3f}".replace(".", ",")


def _dt(ts: float) -> str:
    return time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ts))


def hop_rows(source, th: dict, focus: int = 0, end_ts: float | None = None):
    """Analyse des sauts d'une source (session en direct ou historique)."""
    ts, dest, hops = source.buffer.arrays()
    end = len(ts) if end_ts is None else int(np.searchsorted(ts, end_ts, side="right"))
    start = 0 if focus <= 0 else max(0, end - focus)
    return analyze(source.display_hops(), hops[start:end], hops[max(0, end - 300):end], th)


def export_csv(source, path: str | Path, th: dict) -> list[Path]:
    """Écrit deux fichiers : les échantillons et le résumé par saut.
    Renvoie la liste des fichiers créés."""
    path = Path(path)
    ts, dest, hops = source.buffer.arrays()
    disp = source.display_hops()
    n = len(disp)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Horodatage", "Horodatage Unix", "Destination (ms)", "Perte destination"]
                   + [f"Saut {h.ttl} ({h.ip or '*'}) ms" for h in disp])
        for i in range(len(ts)):
            d = float(dest[i])
            w.writerow([_dt(ts[i]), f"{ts[i]:.3f}", _num(d), "1" if math.isnan(d) else "0"]
                       + [_num(float(hops[i, k])) if k < hops.shape[1] else "" for k in range(n)])
    summary = path.with_name(path.stem + "_resume_sauts.csv")
    rows = hop_rows(source, th)
    with summary.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Saut", "Adresse IP", "Nom DNS", "Envoyés", "Reçus", "Pertes (%)",
                    "Min (ms)", "Moy (ms)", "Max (ms)", "Jitter (ms)", "État"])
        for r in rows:
            s = r.stats
            w.writerow([r.ttl, r.ip or "*", r.hostname or "", s.sent, s.received,
                        _num(s.loss_pct), _num(s.min), _num(s.avg), _num(s.max),
                        _num(s.jitter), r.state])
    return [path, summary]


# ---------------------------------------------------------------- PDF
def _fmt(v, unit=" ms"):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v:.1f}{unit}"


def export_pdf(source, path: str | Path, th: dict, events=None, title: str | None = None) -> Path:
    """Génère un rapport PDF : synthèse, graphiques, tableau des sauts,
    événements. Doit être appelé depuis le thread de l'interface."""
    from PySide6.QtCore import QMarginsF, QSizeF, QUrl
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

    from ..ui import theme
    from ..ui.charts import HopCompareChart, JitterChart, LossChart, TimelineChart

    path = Path(path)
    ts, dest, hops = source.buffer.arrays()
    st = series_stats(dest)
    rows = hop_rows(source, th)
    interval = float(source.opt.interval)
    t0, t1 = (ts[0], ts[-1]) if ts.size else (time.time(), time.time())

    # Graphiques rendus hors écran avec la palette claire (impression)
    pal = theme.LIGHT
    imgs = {}
    tl = TimelineChart()
    tl.apply_palette(pal)
    tl.set_thresholds(th)
    tl.set_window(0)
    tl.update_data(ts, dest, interval)
    imgs["timeline"] = tl.grab_image(1100, 300)
    lc = LossChart()
    lc.apply_palette(pal)
    lc.set_thresholds(th)
    lc.update_data(ts, dest, interval)
    lc.plot.setXRange(t0, t1, padding=0.01)
    imgs["loss"] = lc.grab_image(1100, 220)
    jc = JitterChart()
    jc.apply_palette(pal)
    jc.set_thresholds(th)
    jc.update_data(ts, dest, interval)
    jc.plot.setXRange(t0, t1, padding=0.01)
    imgs["jitter"] = jc.grab_image(1100, 220)
    if source.opt.mode == "trace" and rows:
        hc = HopCompareChart()
        hc.apply_palette(pal)
        hc.set_thresholds(th)
        hc.update_rows(rows)
        imgs["hops"] = hc.grab_image(1100, 380)
    for w in (tl, lc, jc):
        w.deleteLater()

    doc = QTextDocument()
    for k, img in imgs.items():
        doc.addResource(QTextDocument.ImageResource, QUrl(f"img://{k}"), img)

    e = html.escape
    lvl_col = {-1: pal.info, 0: pal.ok, 1: pal.warn, 2: pal.bad, 3: pal.crit}
    h = [f"""<html><head><style>
      body {{ font-family: 'Segoe UI'; font-size: 9pt; color: #18202c; }}
      h1 {{ font-size: 18pt; color: #14324f; margin-bottom: 0; }}
      h2 {{ font-size: 12pt; color: #14324f; margin-top: 16px; border-bottom: 1px solid #d6dde6; }}
      .muted {{ color: #5b6879; }}
      table.grid {{ border-collapse: collapse; }}
      table.grid th {{ background: #eef1f5; color: #384556; padding: 4px 6px; text-align: left; }}
      table.grid td {{ padding: 3px 6px; border-bottom: 1px solid #e3e8ee; }}
      td.num {{ text-align: right; }}
    </style></head><body>
    <h1>{e(title or 'Rapport de diagnostic réseau')}</h1>
    <p class="muted">{e(APP_NAME)} {APP_VERSION} ({AUTHOR}) — généré le {_dt(time.time())}</p>
    <h2>Synthèse</h2>
    <table cellspacing="0" cellpadding="3">
      <tr><td><b>Destination</b></td><td>{e(source.name)} ({e(source.opt.host)} → {e(source.target_ip or '?')})</td></tr>
      <tr><td><b>Période</b></td><td>du {_dt(t0)} au {_dt(t1)} ({(t1 - t0) / 60:.1f} min)</td></tr>
      <tr><td><b>Mode</b></td><td>{'Traceroute continu' if source.opt.mode == 'trace' else 'Ping'} — intervalle {interval:g} s</td></tr>
      <tr><td><b>Paquets</b></td><td>{st.sent} envoyés, {st.received} reçus, <b>{st.loss_pct:.2f} % de pertes</b></td></tr>
      <tr><td><b>Latence</b></td><td>moyenne {_fmt(st.avg)}, minimum {_fmt(st.min)}, maximum {_fmt(st.max)}</td></tr>
      <tr><td><b>Jitter</b></td><td>{_fmt(st.jitter)}</td></tr>
    </table>
    <h2>Latence de la destination</h2>
    <p><img src="img://timeline" width="690"></p>
    <h2>Pertes de paquets</h2>
    <p><img src="img://loss" width="690"></p>
    <h2>Jitter</h2>
    <p><img src="img://jitter" width="690"></p>
    """]
    if "hops" in imgs:
        h.append('<h2>Comparatif par saut</h2><p><img src="img://hops" width="690"></p>')
    h.append("<h2>Détail des sauts</h2><table class='grid' width='100%' cellspacing='0'>"
             "<tr><th>Saut</th><th>Adresse IP</th><th>Nom DNS</th><th>Pertes</th><th>Min</th>"
             "<th>Moy.</th><th>Max</th><th>Jitter</th><th>État</th></tr>")
    for r in rows:
        s = r.stats
        h.append(f"<tr><td>{r.ttl}</td><td>{e(r.ip or '*')}</td><td>{e(r.hostname or '')}</td>"
                 f"<td class='num'>{s.loss_pct:.1f} %</td><td class='num'>{_fmt(s.min, '')}</td>"
                 f"<td class='num'>{_fmt(s.avg, '')}</td><td class='num'>{_fmt(s.max, '')}</td>"
                 f"<td class='num'>{_fmt(s.jitter, '')}</td>"
                 f"<td style='color:{lvl_col.get(r.level, '#000')}'>{e(r.state)}</td></tr>")
    h.append("</table>")
    if events:
        h.append("<h2>Événements</h2><table class='grid' width='100%' cellspacing='0'>"
                 "<tr><th>Date</th><th>Niveau</th><th>Message</th></tr>")
        from ..events import LEVEL_LABEL
        for ev in events[:300]:
            col = {"error": pal.crit, "warning": pal.bad, "success": pal.ok}.get(ev.level, "#384556")
            h.append(f"<tr><td>{_dt(ev.ts)}</td><td style='color:{col}'>"
                     f"{LEVEL_LABEL.get(ev.level, ev.level)}</td><td>{e(ev.message)}</td></tr>")
        h.append("</table>")
    h.append("</body></html>")
    doc.setHtml("".join(h))

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Millimeter)
    writer.setResolution(110)
    writer.setTitle(title or "Rapport NetTrace Pro")
    writer.setCreator(f"{APP_NAME} {APP_VERSION} — {AUTHOR}")
    lay = writer.pageLayout()
    rect = lay.paintRectPixels(writer.resolution())
    doc.setPageSize(QSizeF(rect.width(), rect.height()))
    doc.print_(writer)
    return path
