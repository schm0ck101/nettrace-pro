"""Informations sur la connexion locale : IP locales, IP publiques, nom du
poste, prise en charge IPv4 / IPv6."""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Services publics renvoyant uniquement l'adresse IP de l'appelant.
PUBLIC_V4_URLS = ["https://api.ipify.org?format=json", "https://ipv4.icanhazip.com"]
PUBLIC_V6_URLS = ["https://api6.ipify.org?format=json", "https://ipv6.icanhazip.com"]


@dataclass
class NetInfo:
    hostname: str = ""
    local_v4: str | None = None          # IP utilisée pour sortir vers Internet
    local_v6: str | None = None
    all_addresses: list[str] = field(default_factory=list)
    public_v4: str | None = None
    public_v6: str | None = None

    @property
    def has_v4(self) -> bool:
        return self.local_v4 is not None

    @property
    def has_v6(self) -> bool:
        return self.local_v6 is not None


def _route_source(family: int, probe_ip: str) -> str | None:
    """Adresse source que Windows choisirait pour joindre ``probe_ip``.
    Un ``connect`` UDP n'envoie aucun paquet : il interroge juste la table
    de routage."""
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect((probe_ip, 53))
            ip = s.getsockname()[0]
            return ip.split("%")[0]
    except OSError:
        return None


def local_info() -> NetInfo:
    info = NetInfo(hostname=socket.gethostname())
    info.local_v4 = _route_source(socket.AF_INET, "8.8.8.8")
    v6 = _route_source(socket.AF_INET6, "2001:4860:4860::8888")
    if v6 and not ipaddress.ip_address(v6).is_link_local:
        info.local_v6 = v6
    addrs = []
    try:
        for fam, *_rest, sa in socket.getaddrinfo(info.hostname, None):
            ip = sa[0].split("%")[0]
            if ip not in addrs:
                addrs.append(ip)
    except OSError:
        pass
    info.all_addresses = sorted(addrs, key=lambda a: (":" in a, a))
    return info


def _fetch_ip(urls: list[str], version: int, timeout: float = 4.0) -> str | None:
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NetTracePro"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(200).decode("ascii", "ignore").strip()
            ip = json.loads(body)["ip"] if body.startswith("{") else body
            if ipaddress.ip_address(ip).version == version:
                return ip
        except Exception as exc:  # réseau absent, service indisponible…
            log.debug("IP publique via %s : %s", url, exc)
    return None


def public_ips(info: NetInfo | None = None) -> tuple[str | None, str | None]:
    """IP publiques (v4, v6) ; None si Internet n'est pas disponible."""
    v4 = _fetch_ip(PUBLIC_V4_URLS, 4)
    v6 = _fetch_ip(PUBLIC_V6_URLS, 6) if (info is None or info.has_v6) else None
    return v4, v6
