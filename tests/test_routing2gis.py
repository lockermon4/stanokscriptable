"""2GIS routing: кэш/TTL/retry/ошибки + тексты флоу (варианты, детали, избранное)."""
import asyncio
import time

import httpx
import pytest

from student_bot import routing as R
from student_bot.routing import (NoMetroError, RouteOption, RoutingError,
                                 SchemaNotDocumented, TwoGisRouting, cache_key,
                                 get_metro_route, get_walking_route)
from student_bot.store import fmt_coords, norm_transport, parse_coords


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def ok_client(handler, api_key="k"):
    return TwoGisRouting(api_key=api_key,
                         http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def payload_ok():
    return httpx.Response(200, json={"routes": [{"x": 1}]})


# ---------- ключ и базовые ошибки ----------

def test_no_key_refuses():
    r = TwoGisRouting(api_key="", http=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: payload_ok())))
    import os
    old = os.environ.pop("GIS_API_KEY", None)
    try:
        with pytest.raises(RoutingError):
            run(r.walking((55.0, 37.0), (55.1, 37.1)))
    finally:
        if old is not None:
            os.environ["GIS_API_KEY"] = old


def test_parsers_wait_for_docs():
    with pytest.raises(SchemaNotDocumented):
        R.parse_walk_payload({"routes": []})
    with pytest.raises(SchemaNotDocumented):
        R.parse_metro_payload({"routes": []})


def test_4xx_is_fatal_no_retry():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(400, json={"error": "bad"})
    r = ok_client(h)
    with pytest.raises(RoutingError):
        run(r.walking((55.0, 37.0), (55.1, 37.1)))
    assert len(calls) == 1  # 400 не ретраится


def test_empty_walk_is_honest_error(monkeypatch):
    monkeypatch.setattr(R, "parse_walk_payload", lambda p: [])
    r = ok_client(lambda req: payload_ok())
    with pytest.raises(RoutingError):
        run(r.walking((55.0, 37.0), (55.1, 37.1)))


# ---------- кэш: ключ, TTL, use_cache=False ----------

def test_cache_key_rounds_to_4():
    assert cache_key((55.123456, 37.123456), (55.7, 37.5), "walk") == \
        cache_key((55.123451, 37.123451), (55.7, 37.5), "walk")
    assert cache_key((55.1, 37.1), (55.7, 37.5), "walk") != \
        cache_key((55.1, 37.1), (55.7, 37.5), "metro")


def test_cache_hit_no_second_request(monkeypatch):
    monkeypatch.setattr(R, "parse_walk_payload",
                        lambda p: [RouteOption(mode="walk", duration_s=600, summary="10 мин")])
    calls = []

    def h(req):
        calls.append(1)
        return payload_ok()
    r = ok_client(h)
    a = run(r.walking((55.0, 37.0), (55.1, 37.1)))
    b = run(r.walking((55.0, 37.0), (55.1, 37.1)))
    assert a == b and len(calls) == 1


def test_cache_ttl_expires(monkeypatch):
    monkeypatch.setattr(R, "parse_walk_payload",
                        lambda p: [RouteOption(mode="walk", duration_s=600, summary="10 мин")])
    calls = []

    def h(req):
        calls.append(1)
        return payload_ok()
    r = ok_client(h, )
    r.walk_ttl = 0  # мгновенно протухает
    run(r.walking((55.0, 37.0), (55.1, 37.1)))
    run(r.walking((55.0, 37.0), (55.1, 37.1)))
    assert len(calls) == 2


def test_fresh_bypass_for_favorites(monkeypatch):
    monkeypatch.setattr(R, "parse_metro_payload",
                        lambda p: [RouteOption(mode="metro", duration_s=1200, summary="20 мин")])
    calls = []

    def h(req):
        calls.append(1)
        return payload_ok()
    r = ok_client(h)
    run(r.metro((55.0, 37.0), (55.1, 37.1)))
    run(r.metro((55.0, 37.0), (55.1, 37.1), use_cache=False))  # избранное: свежий запрос
    assert len(calls) == 2


def test_module_functions_share_client(monkeypatch):
    monkeypatch.setattr(R, "parse_walk_payload",
                        lambda p: [RouteOption(mode="walk", duration_s=1, summary="s")])
    calls = []

    def h(req):
        calls.append(1)
        return payload_ok()
    import os
    os.environ["GIS_API_KEY"] = "k"
    R._shared = None
    try:
        run(get_walking_route((55.0, 37.0), (55.1, 37.1),
                              **{"http": httpx.AsyncClient(transport=httpx.MockTransport(h))}))
    finally:
        R._shared = None
        del os.environ["GIS_API_KEY"]
    assert len(calls) == 1


def test_metro_empty_means_no_metro(monkeypatch):
    monkeypatch.setattr(R, "parse_metro_payload", lambda p: [])
    r = ok_client(lambda req: payload_ok())
    # пустой список от парсера сервис превратит в NoMetroError/route_failed;
    # сам клиент пустой ответ метро не кэширует как успех:
    with pytest.raises((NoMetroError, RoutingError)):
        run(r.metro((55.0, 37.0), (55.1, 37.1)))


# ---------- тексты флоу ----------

def _opt(**kw):
    base = dict(mode="metro", duration_s=1260, distance_m=8000, transfers=1,
                walk_before_s=300, walk_after_s=240, summary="s")
    base.update(kw)
    return RouteOption(**base)


def test_variant_labels():
    from student_bot.texts import variant_label, variants_buttons, variants_text
    m = _opt()
    assert variant_label(m, 0, "ru") == "21 мин, 1 пересадка, 9 мин пешком"
    w = _opt(mode="walk", duration_s=1800, distance_m=2500)
    assert variant_label(w, 0, "ru") == "30 мин 2.5 км"
    t = variants_text("ru", "08:30 — М", [m, w])
    assert "1. 21 мин" in t and "2. 30 мин" in t and "Выберите вариант" in t
    datas = [b.callback_data for row in variants_buttons([m, w], "ru").inline_keyboard for b in row]
    assert datas[:2] == ["rtv:0", "rtv:1"]


def test_details_and_exit_line():
    from student_bot.bot import exit_line_for
    from student_bot.texts import route_details, route_details_buttons
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from student_bot.models import Lesson
    les = Lesson(group="G", day=datetime(2026, 9, 26).date(),
                 starts_at=datetime(2026, 9, 26, 8, 30, tzinfo=ZoneInfo("Europe/Moscow")),
                 ends_at=datetime(2026, 9, 26, 10, 5, tzinfo=ZoneInfo("Europe/Moscow")),
                 subject="М", room="0303")
    assert exit_line_for(les, 1260, 10, "ru") == "🏃 Выйти в 07:59 (~21 мин в пути), прибытие ~08:20."
    o = _opt(steps=("м. Коньково (оранжевая)", "м. Третьяковская — пересадка"))
    d = route_details("ru", o, "exit")
    assert "Коньково" in d and "exit" in d
    assert route_details("ru", _opt(), "") .endswith("(Пошагового описания нет.)")
    datas = [b.callback_data for row in route_details_buttons(2, "ru").inline_keyboard for b in row]
    assert "rt:save:2" in datas


def test_fav_texts_and_coords():
    from student_bot.texts import (fav_confirm_delete, fav_deleted, fav_item_buttons,
                                   fav_list_buttons, fav_list_text, mode_buttons)
    from student_bot.store import Favorite
    assert parse_coords("55.63,37.52") == (55.63, 37.52)
    assert parse_coords("junk") is None
    assert fmt_coords(55.63, 37.52) == "55.63,37.52"
    assert "Пока нет" in fav_list_text("ru", [])
    favs = [Favorite(id=3, user_id=7, name="Дом", from_coords="a", to_coords="b",
                     transport_type="metro")]
    assert "Дом" in fav_list_text("ru", favs)
    datas = [b.callback_data for row in fav_list_buttons(favs, "ru").inline_keyboard for b in row]
    assert "fav:3" in datas
    datas2 = [b.callback_data for row in fav_item_buttons(3, "ru").inline_keyboard for b in row]
    assert "favdel:3" in datas2
    q, kb = fav_confirm_delete("ru", "Дом", 3)
    assert "Дом" in q
    assert "favdel_yes:3" in [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "удалён" in fav_deleted("ru", "Дом")
    texts = [b.text for row in mode_buttons("ru").inline_keyboard for b in row]
    assert "🚶 Пешком" in texts and "🚇 Метро" in texts


def test_norm_transport_and_menu_routes():
    assert norm_transport("transit") == "metro"
    assert norm_transport("driving") == "walk"
    assert norm_transport("foot") == "walk"
    assert norm_transport("metro") == "metro"
    assert norm_transport("") == "metro"
    from student_bot.texts import menu_kb, menu_match
    assert any("⭐ Маршруты" in row for row in menu_kb("ru"))
    assert menu_match()["⭐ Маршруты"] == "routes"
    assert menu_match()["Мои маршруты"] == "routes"
    from student_bot.texts import leave_error_text, route_session_expired
    assert "пар больше нет" in leave_error_text("ru", "no_lessons")
    assert "корпуса неизвестен" in leave_error_text("ru", "unknown_building")
    assert "Когда выходить" in route_session_expired("ru")
