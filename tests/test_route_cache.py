"""Персистентный кэш маршрутов 2GIS (память + route_cache в Store).

Покрывает: повтор без запроса к API, разные TTL (метро 15 мин / пешком 6 ч),
переживание «рестарта», обход по allow_cache=False (iOS-регрессия),
короткий TTL для ошибок с сохранением типа исключения.
"""
import asyncio
import json
import time

import httpx
import pytest

from student_bot import routing as R
from student_bot.routing import (NoMetroError, RouteOption, RoutingError,
                                 TwoGisRouting, _db_key, cache_key)
from student_bot.store import Store


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


WALK_OPTS = [RouteOption(mode="walk", duration_s=1500, distance_m=9000, summary="25 мин")]
METRO_OPTS = [RouteOption(mode="metro", duration_s=2100, transfers=1, summary="35 мин, 1 пересадка")]


@pytest.fixture()
def store_(tmp_path):
    return Store(str(tmp_path / "db.sqlite3"))


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    """Заглушки парсеров: мок всегда успешен; тесты переопределяют при нужде."""
    monkeypatch.setattr(R, "parse_walk_payload", lambda p: WALK_OPTS)
    monkeypatch.setattr(R, "parse_metro_payload", lambda p, **kw: METRO_OPTS)


def client_with(store_, counter=None):
    def h(req):
        if counter is not None:
            counter.append(str(req.url))
        return httpx.Response(200, json={"routes": []})
    c = TwoGisRouting(api_key="k", http=httpx.AsyncClient(
        transport=httpx.MockTransport(h)))
    c.attach_cache(store_)
    return c


def age_entry(store_, fr, to, kind, age_s):
    key = _db_key(cache_key(fr, to, kind))
    row = store_.get_route_cache(key)
    store_.put_route_cache(key, row["payload"], time.time() - age_s)


def test_repeat_request_hits_cache_no_second_post(store_):
    calls = []
    c = client_with(store_, counter=calls)
    fr, to = (55.79, 37.60), (55.75, 37.73)
    a = run(c.walking(fr, to))
    b = run(c.walking(fr, to))
    assert len(calls) == 1
    assert a[0].duration_s == b[0].duration_s == 1500 and b[0].summary == "25 мин"
    run(c.metro(fr, to))
    run(c.metro(fr, to))
    assert len(calls) == 2  # метро — отдельный ключ/режим


def test_cache_survives_restart(store_):
    calls = []
    c1 = client_with(store_, counter=calls)
    fr, to = (55.79, 37.60), (55.75, 37.73)
    run(c1.walking(fr, to))
    assert len(calls) == 1
    c2 = client_with(store_, counter=calls)  # «рестарт»: память пустая
    opts = run(c2.walking(fr, to))
    assert len(calls) == 1 and opts[0].duration_s == 1500
    assert opts[0].steps == tuple() and isinstance(opts[0].raw, dict)


def test_metro_ttl_15_min(store_):
    calls = []
    c = client_with(store_, counter=calls)
    fr, to = (55.79, 37.60), (55.75, 37.73)
    run(c.metro(fr, to))
    c.drop()
    age_entry(store_, fr, to, "metro", 16 * 60)  # 16 мин > METRO_TTL_S
    run(c.metro(fr, to))
    assert len(calls) == 2


def test_walk_ttl_6_hours(store_):
    calls = []
    assert R.WALK_TTL_S == 6 * 3600
    c = client_with(store_, counter=calls)
    fr, to = (55.79, 37.60), (55.75, 37.73)
    run(c.walking(fr, to))
    c.drop()
    age_entry(store_, fr, to, "walk", 2 * 3600)  # 2 ч < 6 ч — свежий
    run(c.walking(fr, to))
    assert len(calls) == 1
    age_entry(store_, fr, to, "walk", 7 * 3600)  # 7 ч > 6 ч — просрочен
    c.drop()  # прошлое чтение прогрело память — чистим, чтобы проверить БД
    run(c.walking(fr, to))
    assert len(calls) == 2


def test_allow_cache_false_always_fresh(store_):
    # регрессия на iOS API: каждый вызов — реальный запрос, кэш лишь обновляется
    calls = []
    c = client_with(store_, counter=calls)
    fr, to = (55.79, 37.60), (55.75, 37.73)
    run(c.walking(fr, to))
    run(c.walking(fr, to, allow_cache=False))
    run(c.walking(fr, to, allow_cache=False))
    assert len(calls) == 3
    row = store_.get_route_cache(_db_key(cache_key(fr, to, "walk")))
    assert abs(row["fetched_at"] - time.time()) < 5  # кэш прогрет свежим вызовом
    c.drop()
    run(c.walking(fr, to))  # allow_cache=True — теперь попадает в прогретый кэш
    assert len(calls) == 3


def test_error_cached_briefly_then_retried(store_, monkeypatch):
    calls = []
    monkeypatch.setattr(R, "parse_walk_payload", lambda p: [])
    c = client_with(store_, counter=calls)
    fr, to = (55.79, 37.60), (55.75, 37.73)
    with pytest.raises(RoutingError):
        run(c.walking(fr, to))
    with pytest.raises(RoutingError):
        run(c.walking(fr, to))  # маркер ошибки, <= ERROR_TTL_S — без нового POST
    assert len(calls) == 1
    monkeypatch.setattr(R, "parse_walk_payload", lambda p: WALK_OPTS)
    c.drop()
    age_entry(store_, fr, to, "walk", R.ERROR_TTL_S + 1)
    opts = run(c.walking(fr, to))  # ошибка не должна залипать
    assert len(calls) == 2 and opts[0].duration_s == 1500
    row = json.loads(store_.get_route_cache(_db_key(cache_key(fr, to, "walk")))["payload"])
    assert isinstance(row, list)  # успех перезаписал маркер ошибки


def test_no_metro_error_type_preserved(store_, monkeypatch):
    calls = []
    monkeypatch.setattr(R, "parse_metro_payload", lambda p, **kw: [])
    c = client_with(store_, counter=calls)
    fr, to = (55.79, 37.60), (55.75, 37.73)
    with pytest.raises(NoMetroError):
        run(c.metro(fr, to))
    c.drop()
    with pytest.raises(NoMetroError):  # из кэша восстанавливается тот же тип
        run(c.metro(fr, to))
    assert len(calls) == 1


def test_night_metro_bypasses_cache(store_):
    calls = []
    c = client_with(store_, counter=calls)
    fr, to = (55.79, 37.60), (55.75, 37.73)
    run(c.metro(fr, to, max_wait_s=None))  # ночной расчёт: не кэшируется
    assert len(calls) == 1
    c2 = client_with(store_, counter=calls)
    run(c2.metro(fr, to, max_wait_s=None))
    assert len(calls) == 2
