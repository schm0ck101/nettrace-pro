"""Tests unitaires du moteur (statistiques, ICMP, analyse, historique, DNS).
Lancer :  .venv\\Scripts\\python -m pytest -q tests
"""
import math
import os
import time

import numpy as np
import pytest

from nettrace.config import DEFAULTS
from nettrace.net import icmp
from nettrace.net.analysis import LEVEL_INFO, analyze
from nettrace.net.dns import ResolveError, clean_host, resolve
from nettrace.net.stats import (NOT_PROBED, SampleBuffer, bucket_loss, consecutive_losses,
                                rolling_jitter, series_stats)
from nettrace.net.tracer import HopInfo

TH = DEFAULTS["thresholds"]


def test_series_stats_basic():
    st = series_stats(np.array([10, 20, np.nan, 30, NOT_PROBED], dtype=np.float32))
    assert st.sent == 4 and st.received == 3
    assert st.loss_pct == pytest.approx(25.0)
    assert st.min == 10 and st.max == 30 and st.avg == pytest.approx(20)
    assert st.jitter == pytest.approx(10)
    assert st.current == 30 and not st.last_lost


def test_series_stats_empty():
    st = series_stats(np.array([NOT_PROBED, NOT_PROBED]))
    assert st.sent == 0 and st.loss_pct == 0 and math.isnan(st.avg)


def test_consecutive_losses():
    assert consecutive_losses(np.array([1, np.nan, 2, np.nan, np.nan])) == 2
    assert consecutive_losses(np.array([np.nan, np.nan])) == 2
    assert consecutive_losses(np.array([1.0, 2.0])) == 0


def test_buffer_ring_keeps_capacity():
    b = SampleBuffer(capacity=100, columns=3)
    for i in range(1000):
        b.append(float(i), float(i), np.array([i, i, i], dtype=np.float32))
    ts, dest, hops = b.arrays()
    assert len(ts) == 100 and ts[0] == 900 and ts[-1] == 999
    assert np.all(np.diff(ts) == 1) and hops[-1, 2] == 999
    assert b.total == 1000
    assert len(b.since(950.0)[0]) == 50


def test_rolling_jitter_and_buckets():
    v = np.array([10, 12, np.nan, 10, 14], dtype=float)
    j = rolling_jitter(v)
    assert math.isnan(j[2]) and j[0] == 0
    ts = np.arange(10, dtype=float)
    x, pct = bucket_loss(ts, np.array([1, np.nan] * 5, dtype=float), 2.0)
    assert np.allclose(pct, 50)


def test_icmp_loopback_and_timeout():
    r = icmp.probe("127.0.0.1", timeout_ms=1000)
    assert r.reached and r.rtt is not None and r.address == "127.0.0.1"
    r = icmp.probe("::1", timeout_ms=1000)
    assert r.reached
    r = icmp.probe("10.255.255.1", timeout_ms=300)   # non routé : pas de réponse
    assert not r.replied
    r = icmp.probe("not-an-ip", timeout_ms=300)
    assert r.status == icmp.IP_BAD_DESTINATION


def test_icmp_ttl_expired():
    """TTL=1 vers Internet : la passerelle répond « TTL expiré »."""
    r = icmp.probe("1.1.1.1", ttl=1, timeout_ms=1500)
    if r.status == icmp.IP_REQ_TIMED_OUT:
        pytest.skip("pas de réponse de la passerelle")
    assert r.status == icmp.IP_TTL_EXPIRED_TRANSIT and r.address


def test_dns():
    assert clean_host("https://www.example.com:443/path") == "www.example.com"
    assert clean_host("[2001:db8::1]:80") == "2001:db8::1"
    assert resolve("127.0.0.1") == ["127.0.0.1"]
    with pytest.raises(ResolveError):
        resolve("nom-qui-n-existe-pas.invalid")
    with pytest.raises(ResolveError):
        resolve("127.0.0.1", "ipv6")


def test_analysis_icmp_filtered_hop_is_not_a_problem():
    n = 50
    hops = [HopInfo(1, "192.168.1.1"), HopInfo(2, None), HopInfo(3, "10.0.0.1"),
            HopInfo(4, "1.1.1.1", is_destination=True)]
    m = np.full((n, 4), 5.0, dtype=np.float32)
    m[:, 1] = np.nan                  # saut 2 ne répond jamais
    m[::2, 2] = np.nan                # saut 3 : 50 % de pertes mais pas en aval
    rows = analyze(hops, m, m, TH)
    assert rows[1].level == LEVEL_INFO and "sans impact" in rows[1].state
    assert rows[2].level == LEVEL_INFO
    assert rows[3].state == "Destination OK"


def test_analysis_real_loss_propagates():
    hops = [HopInfo(1, "a"), HopInfo(2, "b"), HopInfo(3, "c", is_destination=True)]
    m = np.full((100, 3), 5.0, dtype=np.float32)
    m[::4, 1:] = np.nan               # 25 % dès le saut 2, jusqu'à la destination
    rows = analyze(hops, m, m, TH)
    assert rows[1].level >= 2 and "Perte" in rows[1].state
    assert rows[2].level >= 2


def test_analysis_latency_jump():
    hops = [HopInfo(1, "a"), HopInfo(2, "b"), HopInfo(3, "c", is_destination=True)]
    m = np.array([[2, 150, 155]] * 30, dtype=np.float32)
    rows = analyze(hops, m, None, TH)
    assert "Hausse de latence" in rows[1].state


def test_history_roundtrip(tmp_path):
    from nettrace.events import NetEvent
    from nettrace.storage.history import HistoryDB
    db = HistoryDB(tmp_path / "h.db")
    sid = db.create_session("Test", "example", "1.2.3.4", "trace", 1.0, 30, time.time())
    for i in range(20):
        h = np.full(30, NOT_PROBED, dtype=np.float32)
        h[:3] = [1, np.nan, 3 + i]
        db.add_sample(sid, 1000.0 + i, np.nan if i == 5 else 3.0 + i, h)
    db.set_hop(sid, 1, "192.168.1.1", "box")
    db.add_event(NetEvent("warning", "loss", "test", "Test", sid))
    db.finalize(sid, 1020.0)
    db.flush()
    rec = db.get_session(sid)
    assert rec.samples == 20 and rec.loss == pytest.approx(5.0)
    buf = db.load_samples(sid, columns=30)
    ts, dest, hops = buf.arrays()
    assert len(ts) == 20 and math.isnan(dest[5]) and hops[3, 2] == 6
    assert math.isnan(hops[0, 1]) and hops[0, 5] == NOT_PROBED
    assert db.load_hops(sid) == [(1, "192.168.1.1", "box")]
    assert len(db.list_events(sid)) == 1
    assert db.list_sessions("example")[0].id == sid
    db.delete_sessions([sid])
    assert db.get_session(sid) is None
    db.close()


def test_trace_session_live():
    """Session réelle de quelques secondes vers la boucle locale."""
    from nettrace.net.tracer import TraceOptions, TraceSession
    evs = []
    s = TraceSession(TraceOptions("127.0.0.1", interval=0.2, timeout_ms=500, count=8),
                     TH, on_event=evs.append)
    s.start()
    t_end = time.time() + 10
    while s.state not in ("finished", "error") and time.time() < t_end:
        time.sleep(0.1)
    assert s.state == "finished"
    ts, dest, _ = s.buffer.arrays()
    assert len(ts) == 8 and not np.isnan(dest).any()
    assert s.route_len == 1
