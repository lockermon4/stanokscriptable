"""Final-assembly tests: candidate search, no double count, store persistence,
geocoder fallback, foot provider, notes CRUD, sent-flags."""
import asyncio
import os

import httpx
import pytest

from student_bot.config import Settings
from student_bot.geocode import DualGeocoder
from student_bot.metro import MetroGraph
from student_bot.routing_foot import FosFootProvider
from student_bot.service import _door_to_door_transit
from student_bot.store import Store, UserSettings

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "metro_moscow.json")
S = Settings()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class ScriptFoot:
    """foot_seconds from a script {(a_lon,a_lat,b_lon,b_lat rounded): secs}."""

    def __init__(self, mapping, default=600):
        self.m = mapping
        self.default = default
        self.calls = 0

    async def foot_seconds(self, a, b):
        self.calls += 1
        k = (round(a[0], 3), round(a[1], 3), round(b[0], 3), round(b[1], 3))
        return self.m.get(k, self.default)


def _real():
    if not os.path.exists(DATA):
        pytest.skip("no metro data file")
    return MetroGraph.load(DATA)


def _st(g, name):
    return next(s for s in g.stations if s.name == name)


def test_candidate_search_picks_min_total():
    g = _real()
    konk = _st(g, "Коньково")
    sav = _st(g, "Савёловская")
    men = _st(g, "Менделеевская")
    home = (konk.lat + 0.001, konk.lon + 0.001)
    dest = (sav.lat + 0.001, sav.lon - 0.001)
    # Make Mendeleevskaya egress artificially long so Savelyovskaya must win.
    async def foot(a, b):
        from student_bot.metro import haversine_m
        base = haversine_m(a[1], a[0], b[1], b[0]) / 1.33
        if abs(b[0] - men.lon) < 0.002 and abs(b[1] - men.lat) < 0.002:
            return int(base + 3600)
        return int(base)

    class F:
        async def foot_seconds(self, a, b):
            return await foot(a, b)

    route, summary = run(_door_to_door_transit(F(), g, home, dest, n_each=2))
    assert "Савёловская" in summary and "Менделеевская" not in summary.split("→")[0]


def test_no_double_count_legs_sum_equals_total():
    g = _real()
    konk = _st(g, "Коньково")
    sav = _st(g, "Савёловская")
    home = (konk.lat + 0.001, konk.lon + 0.001)
    dest = (sav.lat + 0.001, sav.lon - 0.001)

    class F:
        async def foot_seconds(self, a, b):
            return 500

    route, summary = run(_door_to_door_transit(F(), g, home, dest, n_each=1))
    if route.provider == "metro-topology":
        assert sum(l.seconds for l in route.legs) == route.travel_seconds
        assert "пересад" in summary  # transfer points explicit (or "без пересадок")


def test_store_notes_crud_and_persistence(tmp_path):
    p = str(tmp_path / "t.sqlite3")
    s1 = Store(p)
    s1.set_note(7, "2026-09-25", "взять халат")
    assert s1.get_note(7, "2026-09-25") == "взять халат"
    s1.set_note(7, "2026-09-25", "взять халат и сменку")
    s2 = Store(p)  # new instance = restart
    assert s2.get_note(7, "2026-09-25") == "взять халат и сменку"
    s2.delete_note(7, "2026-09-25")
    assert Store(p).get_note(7, "2026-09-25") == ""


def test_store_user_and_sent_persist(tmp_path):
    p = str(tmp_path / "t.sqlite3")
    s1 = Store(p)
    s1.save_user(UserSettings(user_id=9, group="ИДБ-26-14", home_address="дом",
                              home_lat=55.1, home_lon=37.1))
    s1.mark_sent(9, "2026-09-25", "morn")
    s2 = Store(p)
    u = s2.get_user(9)
    assert (u.group, u.home_lat, u.home_lon) == ("ИДБ-26-14", 55.1, 37.1)
    assert s2.was_sent(9, "2026-09-25", "morn") is True
    assert s2.was_sent(9, "2026-09-25", "eve") is False


def test_geocoder_falls_back_to_photon():
    def handler(request: httpx.Request) -> httpx.Response:
        if "nominatim" in str(request.url):
            return httpx.Response(403, json={})
        return httpx.Response(200, json={"features": [
            {"geometry": {"coordinates": [37.5, 55.7]}, "properties": {}}]})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    geo = DualGeocoder(S, http=http)
    assert run(geo.geocode("Москва, Вадковский пер., 3А")) == (55.7, 37.5)


def test_geocoder_none_when_no_result():
    def handler(request: httpx.Request) -> httpx.Response:
        if "nominatim" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"features": []})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    geo = DualGeocoder(S, http=http)
    assert run(geo.geocode("абракадабра несуществующая")) is None


def test_foot_provider_parses_and_caches():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert "/routed-foot/route/v1/" in str(request.url)
        return httpx.Response(200, json={"code": "Ok", "routes": [{"duration": 507.3}]})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    st = Settings(foot_base="https://routing.openstreetmap.de")
    f = FosFootProvider(st, http=http)
    a, b = (37.5, 55.7), (37.51, 55.71)
    assert run(f.foot_seconds(a, b)) == 507
    assert run(f.foot_seconds(a, b)) == 507
    assert calls["n"] == 1  # second call served from cache
