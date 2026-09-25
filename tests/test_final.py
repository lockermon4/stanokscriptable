"""Final-assembly tests: 2GIS option mapping, favorites CRUD, store persistence,
geocoder fallback, foot provider, notes CRUD, sent-flags."""
import asyncio

import httpx

from student_bot.config import Settings
from student_bot.geocode import DualGeocoder
from student_bot.routing_foot import FosFootProvider
from student_bot.service import option_to_route
from student_bot.store import Favorite, Store, UserSettings

S = Settings()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_option_to_route_first_option_drives_exit():
    from student_bot.routing import RouteOption
    opt = RouteOption(mode="metro", duration_s=1260, distance_m=8000, transfers=1,
                      walk_before_s=300, walk_after_s=240, summary="21 мин, 1 пересадка")
    r = option_to_route(opt)
    assert r.travel_seconds == 1260 and r.provider == "2gis" and r.is_approximate is False


def test_favorites_crud_and_user_scoping(tmp_path):
    p = str(tmp_path / "f.sqlite3")
    s = Store(p)
    fid = s.add_favorite(7, "Дом → СТАНКИН", "55.63,37.52", "55.79,37.59", "metro")
    assert fid > 0
    s.add_favorite(7, "Дом → Фрезер", "55.63,37.52", "55.73,37.73", "walk")
    favs = s.list_favorites(7)
    assert [(f.name, f.transport_type) for f in favs] == [
        ("Дом → СТАНКИН", "metro"), ("Дом → Фрезер", "walk")]
    assert favs[0].from_coords == "55.63,37.52" and isinstance(favs[0], Favorite)
    assert s.list_favorites(8) == []  # чужое не видно
    assert s.delete_favorite(7, fid) is True
    assert [f.name for f in s.list_favorites(7)] == ["Дом → Фрезер"]
    assert s.delete_favorite(7, fid) is False  # повторное удаление
    assert s.delete_favorite(8, favs[1].id) is False  # чужое не удаляется
    s2 = Store(p)  # рестарт: переживает
    assert [f.name for f in s2.list_favorites(7)] == ["Дом → Фрезер"]


def test_transport_legacy_migrates_on_read(tmp_path):
    p = str(tmp_path / "m.sqlite3")
    s = Store(p)
    with s._conn() as c:
        c.execute("INSERT INTO users(user_id, transport) VALUES (1, 'transit')")
        c.execute("INSERT INTO users(user_id, transport) VALUES (2, 'driving')")
        c.execute("INSERT INTO users(user_id, transport) VALUES (3, 'foot')")
    assert s.get_user(1).transport == "metro"
    assert s.get_user(2).transport == "walk"
    assert s.get_user(3).transport == "walk"
    assert s.get_user(999).transport == "metro"  # дефолт новым


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
