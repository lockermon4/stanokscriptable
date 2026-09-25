"""Unit tests for the static metro estimator + sanity on real OSM data.

Leg weight: dist / METRO_SPEED_MS + METRO_DWELL_S (distance-based, validated);
transfers and boarding waits are separate weights. See README for calibration.
"""
import os

import pytest

from student_bot.metro import BOARD_WAIT_S, METRO_DWELL_S, METRO_SPEED_MS, TRANSFER_S, MetroGraph, Station, haversine_m

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "metro_moscow.json")
LEG_001 = 1111.95 / METRO_SPEED_MS + METRO_DWELL_S  # ~131.1 s for toy grid


def _toy() -> MetroGraph:
    sts = [
        Station(0, "A", 55.0, 37.0, ("red",)),
        Station(1, "B", 55.01, 37.0, ("red", "blue")),
        Station(2, "C", 55.02, 37.0, ("blue",)),
        Station(3, "D", 55.03, 37.0, ("blue",)),
    ]
    return MetroGraph(sts, {"red": [0, 1], "blue": [1, 2, 3]})


def test_direct_ride_no_transfer():
    g = _toy()
    r = g.ride(2, 3)
    assert (r.stops, r.transfers) == (1, 0)
    assert r.seconds == pytest.approx(BOARD_WAIT_S + LEG_001, abs=2)
    assert "пересадка" not in r.pretty


def test_ride_with_transfer_explicit_in_display():
    g = _toy()
    r = g.ride(0, 3)
    assert (r.stops, r.transfers) == (3, 1)
    assert r.seconds == pytest.approx(BOARD_WAIT_S + 3 * LEG_001 + TRANSFER_S, abs=3)
    assert "⟷пересадка red→blue⟷" in r.pretty
    assert r.path[0] == "A" and r.path[-1] == "D"


def test_same_station_zero():
    g = _toy()
    r = g.ride(1, 1)
    assert r.seconds == 0 and r.stops == 0


def test_nearest():
    g = _toy()
    assert g.nearest(55.029, 37.0).name == "D"


def test_haversine_sanity():
    # ~111 m per 0.001 deg latitude
    assert 100 < haversine_m(55.0, 37.0, 55.001, 37.0) < 125


def _real() -> MetroGraph:
    if not os.path.exists(DATA):
        pytest.skip("no metro data file")
    return MetroGraph.load(DATA)


def _by_name(g: MetroGraph, *names: str) -> Station:
    for s in g.stations:
        if s.name in names or set(names) & set(s.aliases):
            return s
    raise AssertionError(f"station not found: {names}")


def test_real_coverage():
    g = _real()
    assert len(g.stations) >= 200
    assert len(g.lines) >= 15
    sav = _by_name(g, "Савёловская")
    assert set(sav.lines) >= {"9", "11"}


def test_r1_short_hop_direct():
    g = _real()
    r = g.ride(_by_name(g, "Савёловская").id, _by_name(g, "Менделеевская").id)
    assert (r.stops, r.transfers) == (1, 0)
    assert "пересадка" not in r.pretty


def test_r2_konkovo_mendeleevskaya_via_center():
    g = _real()
    r = g.ride(_by_name(g, "Коньково").id, _by_name(g, "Менделеевская").id)
    assert r.transfers == 1
    assert "Октябрьская" in r.path  # orange -> circle, not BKL detour
    assert "пересадка" in r.pretty


def test_r3_schelkovskaya_via_elektrozavodskaya():
    g = _real()
    r = g.ride(_by_name(g, "Щёлковская").id, _by_name(g, "Савёловская").id)
    assert r.transfers == 1
    assert "Электрозаводская" in r.path


def test_r4_krylatskoe_via_bkl_kuntsevskaya():
    g = _real()
    r = g.ride(_by_name(g, "Крылатское").id, _by_name(g, "Савёловская").id)
    assert r.transfers == 1
    assert "Кунцевская" in r.path  # BKL is right here


def test_r5_rechnoy_nizhegorodskaya_single_transfer():
    g = _real()
    r = g.ride(_by_name(g, "Речной вокзал").id, _by_name(g, "Нижегородская").id)
    assert r.transfers == 1 and 12 <= r.stops <= 14


def test_r6_konkovo_nizhegorodskaya_via_bkl():
    g = _real()
    r = g.ride(_by_name(g, "Коньково").id, _by_name(g, "Нижегородская").id)
    assert r.transfers == 1 and 10 <= r.stops <= 12


def test_calibration_konkovo_oktyabrskaya():
    # Known ride: ~20-22 min real. Model must stay in a sane band (not fitted).
    g = _real()
    r = g.ride(_by_name(g, "Коньково").id, _by_name(g, "Октябрьская").id)
    assert r.transfers == 0 and r.stops == 8
    assert 18 * 60 <= r.seconds <= 28 * 60


def test_hub_labels_per_line():
    g = _real()
    m = _by_name(g, "Менделеевская")
    assert m.labels.get("9") == "Менделеевская"
    assert m.labels.get("5") == "Новослободская"
