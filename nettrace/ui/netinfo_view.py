"""Page « Réseau local » : IP locales et publiques, passerelle, IPv4/IPv6,
outil de résolution DNS. Tout est fait en arrière‑plan."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..net import icmp
from ..net.dns import ResolveError, clean_host, resolve
from ..net.netinfo import NetInfo, local_info, public_ips


class _Relay(QObject):
    info = Signal(object)
    public = Signal(object, object)
    gateway = Signal(object)
    dns = Signal(str)


def gateway_ip() -> str | None:
    """Première passerelle : réponse d'une sonde TTL=1 vers Internet."""
    for target in ("8.8.8.8", "1.1.1.1"):
        r = icmp.probe(target, ttl=1, timeout_ms=1000)
        if r.address:
            return r.address
    return None


class NetInfoView(QWidget):
    info_changed = Signal(object)     # NetInfo (pour la barre latérale/tableau de bord)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.info = NetInfo()
        self.relay = _Relay()
        self.relay.info.connect(self._on_info)
        self.relay.public.connect(self._on_public)
        self.relay.gateway.connect(lambda g: self.v["gw"].setText(g or "Non détectée"))
        self.relay.dns.connect(self._on_dns)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        t = QLabel("Réseau local")
        t.setObjectName("PageTitle")
        root.addWidget(t)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.v = {}

        def panel(title, fields):
            fr = QFrame()
            fr.setObjectName("Panel")
            lay = QVBoxLayout(fr)
            lay.setContentsMargins(16, 12, 16, 12)
            h = QLabel(title)
            h.setObjectName("SectionTitle")
            lay.addWidget(h)
            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignLeft)
            for key, label in fields:
                val = QLabel("…")
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                val.setWordWrap(True)
                self.v[key] = val
                lab = QLabel(label)
                lab.setObjectName("Muted")
                form.addRow(lab, val)
            lay.addLayout(form)
            lay.addStretch(1)
            return fr

        grid.addWidget(panel("Ce poste", [("host", "Nom du poste"), ("v4", "IPv4 locale"),
                                          ("v6", "IPv6 globale"), ("gw", "Passerelle"),
                                          ("all", "Toutes les adresses")]), 0, 0)
        grid.addWidget(panel("Internet", [("pub4", "IPv4 publique"), ("pub6", "IPv6 publique"),
                                          ("s4", "Connectivité IPv4"), ("s6", "Connectivité IPv6")]), 0, 1)
        root.addLayout(grid)
        rb = QHBoxLayout()
        self.refresh_btn = QPushButton("Actualiser")
        self.refresh_btn.clicked.connect(self.refresh)
        rb.addWidget(self.refresh_btn)
        rb.addStretch(1)
        root.addLayout(rb)

        # Outil DNS
        fr = QFrame()
        fr.setObjectName("Panel")
        lay = QVBoxLayout(fr)
        lay.setContentsMargins(16, 12, 16, 12)
        h = QLabel("Résolution DNS")
        h.setObjectName("SectionTitle")
        lay.addWidget(h)
        row = QHBoxLayout()
        self.dns_in = QLineEdit()
        self.dns_in.setPlaceholderText("Nom de domaine ou adresse IP (ex. www.google.com)")
        self.dns_in.returnPressed.connect(self.lookup)
        self.dns_fam = QComboBox()
        self.dns_fam.addItem("IPv4 et IPv6", "auto")
        self.dns_fam.addItem("IPv4 seulement", "ipv4")
        self.dns_fam.addItem("IPv6 seulement", "ipv6")
        b = QPushButton("Résoudre")
        b.setObjectName("Primary")
        b.clicked.connect(self.lookup)
        row.addWidget(self.dns_in, 1)
        row.addWidget(self.dns_fam)
        row.addWidget(b)
        lay.addLayout(row)
        self.dns_out = QPlainTextEdit()
        self.dns_out.setReadOnly(True)
        self.dns_out.setMaximumHeight(180)
        lay.addWidget(self.dns_out)
        root.addWidget(fr)
        root.addStretch(1)

    # ---------------------------------------------------------------
    def refresh(self):
        self.refresh_btn.setEnabled(False)
        for k in ("pub4", "pub6", "gw"):
            self.v[k].setText("recherche…")

        def work():
            info = local_info()
            self.relay.info.emit(info)
            self.relay.gateway.emit(gateway_ip())
            v4, v6 = public_ips(info)
            self.relay.public.emit(v4, v6)

        threading.Thread(target=work, name="netinfo", daemon=True).start()

    def _on_info(self, info: NetInfo):
        self.info = info
        self.v["host"].setText(info.hostname)
        self.v["v4"].setText(info.local_v4 or "Aucune")
        self.v["v6"].setText(info.local_v6 or "Aucune")
        self.v["all"].setText("\n".join(info.all_addresses) or "—")
        self.info_changed.emit(info)

    def _on_public(self, v4, v6):
        self.info.public_v4, self.info.public_v6 = v4, v6
        self.v["pub4"].setText(v4 or "Indisponible (pas d'accès Internet ?)")
        self.v["pub6"].setText(v6 or "Indisponible")
        self.v["s4"].setText("✔ Disponible" if v4 else ("Réseau local seulement" if self.info.has_v4 else "✘ Absente"))
        self.v["s6"].setText("✔ Disponible" if v6 else ("Adresse locale seulement" if self.info.has_v6 else "✘ Absente"))
        self.refresh_btn.setEnabled(True)
        self.info_changed.emit(self.info)

    def lookup(self):
        host = clean_host(self.dns_in.text())
        if not host:
            return
        fam = self.dns_fam.currentData()
        self.dns_out.setPlainText(f"Résolution de {host}…")

        def work():
            import socket
            try:
                addrs = resolve(host, fam)
                lines = [f"{host} :"]
                for a in addrs:
                    kind = "IPv6" if ":" in a else "IPv4"
                    try:
                        rev = socket.gethostbyaddr(a)[0]
                    except OSError:
                        rev = "(pas de nom inverse)"
                    lines.append(f"  {kind:5}  {a:40}  {rev}")
                self.relay.dns.emit("\n".join(lines))
            except ResolveError as exc:
                self.relay.dns.emit(str(exc))

        threading.Thread(target=work, name="dns-tool", daemon=True).start()

    def _on_dns(self, text: str):
        self.dns_out.setPlainText(text)
