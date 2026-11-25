"""Résolution DNS directe et inverse (avec cache et pool dédié).

La résolution inverse (PTR) peut être très lente pour certaines IP : elle est
faite en arrière‑plan et ne bloque jamais les mesures.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)


class ResolveError(Exception):
    pass


def is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text.strip().strip("[]").split("%")[0])
        return True
    except ValueError:
        return False


def ip_version(ip: str) -> int:
    return ipaddress.ip_address(ip.split("%")[0]).version


def clean_host(text: str) -> str:
    """Nettoie une saisie : retire schéma, chemin, port, crochets IPv6."""
    t = text.strip()
    if "://" in t:
        t = t.split("://", 1)[1]
    t = t.split("/", 1)[0]
    if t.startswith("["):
        t = t[1:].split("]", 1)[0]
    elif t.count(":") == 1:          # nom:port ou IPv4:port
        t = t.split(":", 1)[0]
    return t.strip()


def resolve(host: str, family: str = "auto") -> list[str]:
    """Résout un nom en liste d'adresses IP (IPv4 d'abord en mode auto,
    comme la plupart des outils de diagnostic)."""
    host = clean_host(host)
    if not host:
        raise ResolveError("Destination vide")
    if is_ip(host):
        v = ip_version(host)
        if family == "ipv4" and v != 4 or family == "ipv6" and v != 6:
            raise ResolveError(f"{host} n'est pas une adresse {family.upper()}")
        return [host]
    fam = {"ipv4": socket.AF_INET, "ipv6": socket.AF_INET6}.get(family, socket.AF_UNSPEC)
    try:
        infos = socket.getaddrinfo(host, None, fam, socket.SOCK_RAW)
    except socket.gaierror as exc:
        raise ResolveError(f"Impossible de résoudre « {host} » ({exc.strerror})") from exc
    except UnicodeError as exc:
        raise ResolveError(f"Nom invalide : {host}") from exc
    v4 = [i[4][0] for i in infos if i[0] == socket.AF_INET]
    v6 = [i[4][0] for i in infos if i[0] == socket.AF_INET6]
    out = list(dict.fromkeys(v4 + v6))
    if not out:
        raise ResolveError(f"Aucune adresse pour « {host} »")
    return out


class ReverseDNS:
    """Cache de résolution inverse, résolu en arrière‑plan."""

    def __init__(self, workers: int = 8):
        self._cache: dict[str, str | None] = {}
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(workers, thread_name_prefix="rdns")

    def lookup(self, ip: str | None) -> str | None:
        """Renvoie le nom s'il est connu, sinon lance la résolution et
        renvoie None (le nom apparaîtra au rafraîchissement suivant)."""
        if not ip:
            return None
        with self._lock:
            if ip in self._cache:
                return self._cache[ip]
            if ip in self._pending:
                return None
            self._pending.add(ip)
        try:
            self._pool.submit(self._resolve, ip)
        except RuntimeError:  # pool arrêté (fermeture de l'appli)
            pass
        return None

    def _resolve(self, ip: str) -> None:
        name = None
        try:
            name = socket.gethostbyaddr(ip)[0]
            if name == ip:
                name = None
        except (OSError, UnicodeError):
            name = None
        with self._lock:
            self._cache[ip] = name
            self._pending.discard(ip)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


reverse_dns = ReverseDNS()
