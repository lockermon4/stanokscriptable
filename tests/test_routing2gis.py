"""2GIS routing: кэш/TTL/retry/ошибки + тексты флоу (варианты, детали, избранное)."""
import asyncio
import time

import httpx
import pytest

from student_bot import routing as R
from student_bot.routing import (NoMetroError, RouteOption, RoutingError,
                                 TwoGisRouting, cache_key,
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


def test_parsers_real_schema():
    from student_bot.routing import parse_metro_payload, parse_walk_payload
    # Реальная форма walk (живой ответ 2026-09-26): числа + ui_*-тексты.
    walk = {"message": None, "result": [{
        "algorithm": "по основным улицам",
        "total_distance": 20097, "total_duration": 16077,
        "ui_total_distance": {"unit": "км", "value": "20"},
        "ui_total_duration": "4 часа 27 мин",
        "maneuvers": [{"comment": "start"}, {"comment": "finish"}]}]}
    w = parse_walk_payload(walk)
    assert len(w) == 1 and w[0].duration_s == 16077 and w[0].distance_m == 20097
    assert w[0].summary == "4 часа 27 мин, 20 км (по основным улицам)"
    assert w[0].steps == ()  # только start/finish — честно пусто
    assert parse_walk_payload({"result": []}) == []
    assert parse_walk_payload({}) == []

    metro = [{
        "id": "1", "total_duration": 2464, "total_distance": 8939,
        "total_walkway_distance": "пешком 20 мин", "transfer_count": 1,
        "crossing_count": 0, "pedestrian": False, "transport_types": ["metro"],
        "movements": [
            {"type": "walkway", "moving_duration": 300,
             "waypoint": {"subtype": "pedestrian", "comment": "пешком 400 м"}},
            {"type": "passage", "moving_duration": 412, "waiting_duration": 90,
             "routes": None,
             "metro": {"line_name": "Сокольническая линия",
                       "ui_direction_suggest": "в сторону станции «Бульвар Рокоссовского»",
                       "ui_station_count": "3 станции"},
             "waypoint": {"name": "Сокольники", "subtype": "metro"}},
            {"type": "crossing", "moving_duration": 120,
             "waypoint": {"name": "Октябрьская", "comment": "переход"}},
            {"type": "walkway", "moving_duration": 560,
             "waypoint": {"subtype": "pedestrian", "comment": "пешком 800 м"}},
        ]},
        {"id": "2", "total_duration": 5000, "pedestrian": True, "transport_types": [],
         "movements": []},  # пешеходный вариант — отсеивается
    ]
    m = parse_metro_payload(metro)
    assert len(m) == 1
    o = m[0]
    assert o.duration_s == 2464 and o.transfers == 1 and o.distance_m == 8939
    assert o.walk_before_s == 300 and o.walk_after_s == 560  # crossing не в счёт
    assert "пешком 20 мин" in o.summary
    assert o.steps[0] == "🚶 пешком 400 м"
    assert "Сокольники" in o.steps[1] and "Сокольническая линия" in o.steps[1]
    assert "Переход: Октябрьская" in o.steps[2]
    # только пешком / пусто -> пусто (вызывающий код кидает NoMetroError)
    assert parse_metro_payload([{"pedestrian": True}]) == []
    assert parse_metro_payload([]) == []
    assert parse_metro_payload({}) == []


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


def test_rate_limit_caps_burst(monkeypatch):
    import time as _t
    monkeypatch.setattr(R, "parse_walk_payload",
                        lambda p: [RouteOption(mode="walk", duration_s=1, summary="s")])
    r = ok_client(lambda req: payload_ok())
    r.walk_ttl = 0  # каждый раз в сеть, чтобы мерить guard, а не кэш

    async def burst():
        await asyncio.gather(*[r.walking((55.0 + i * 0.001, 37.0), (55.1, 37.1))
                               for i in range(4)])

    t0 = _t.monotonic()
    run(burst())
    dt = _t.monotonic() - t0
    # 4 запроса с интервалом ≥0.05с -> не быстрее ~0.15с (далеко от 50 RPS)
    assert dt >= 0.15


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


def _metro_item(wait_s):
    return {"id": "1", "total_duration": 2464, "total_distance": 8939,
            "total_walkway_distance": "пешком 20 мин", "transfer_count": 1,
            "pedestrian": False, "transport_types": ["metro"],
            "movements": [
                {"type": "walkway", "moving_duration": 300,
                 "waypoint": {"comment": "пешком 400 м"}},
                {"type": "passage", "moving_duration": 412,
                 "waiting_duration": wait_s,
                 "metro": {"line_name": "Л1"},
                 "waypoint": {"name": "Ст"}}]}


def test_insane_waiting_filtered_out():
    from student_bot.routing import MAX_WAIT_S, parse_metro_payload
    assert MAX_WAIT_S == 1800
    # Живой кейс 2026-09-28: waiting 15708с посреди дня — мусор, не расписание.
    assert parse_metro_payload([_metro_item(15708)]) == []
    ok = parse_metro_payload([_metro_item(15708), _metro_item(120)])
    assert len(ok) == 1 and ok[0].duration_s == 2464
    assert parse_metro_payload([_metro_item(1800)]) != []  # граница: 30 мин ещё ок


def test_day_exit_line_shows_metro_fallback():
    from types import SimpleNamespace
    from student_bot.bot import day_exit_line
    from student_bot.exit_time import ExitPlan
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from student_bot.models import Lesson
    tz = ZoneInfo("Europe/Moscow")
    les = Lesson(group="G", day=datetime(2026, 9, 29).date(),
                 starts_at=datetime(2026, 9, 29, 10, 15, tzinfo=tz),
                 ends_at=datetime(2026, 9, 29, 11, 50, tzinfo=tz),
                 subject="М", room="0209")
    plan = ExitPlan(lesson=les, travel_seconds=270 * 60, buffer_min=10,
                    exit_at=datetime(2026, 9, 29, 5, 35, tzinfo=tz),
                    is_approximate=False, route_calculated_at=None, already_passed=False)
    view = SimpleNamespace(plan=plan, target=les, metro_fallback=True)
    txt = day_exit_line(view, "ru")
    assert "05:35" in txt and "Маршрута на метро нет" in txt
    view2 = SimpleNamespace(plan=plan, target=les, metro_fallback=False)
    assert "Маршрута на метро нет" not in day_exit_line(view2, "ru")


def test_metro_hours_states():
    from datetime import datetime
    from student_bot.metro_hours import metro_state, opens_at_text
    assert opens_at_text() == "05:30"
    t = lambda h, m: metro_state(datetime(2026, 9, 26, h, m))
    assert t(0, 29) == "open" and t(12, 0) == "open" and t(23, 0) == "open"
    assert t(0, 30) == "gray" and t(0, 59) == "gray"
    assert t(1, 0) == "closed" and t(3, 0) == "closed" and t(5, 29) == "closed"
    assert t(5, 30) == "open"


def test_metro_closed_gray_texts_and_button():
    from student_bot.texts import (leave_now_buttons, metro_closed, metro_gray,
                                   route_details_buttons)
    assert "05:30" in metro_closed("ru") and "закрыто" in metro_closed("ru")
    assert "01:00" in metro_gray("ru")
    assert "closed" in metro_closed("en")
    datas = [b.callback_data for row in route_details_buttons(0, "ru").inline_keyboard for b in row]
    assert "rt:now" in datas
    texts = [b.text for row in leave_now_buttons("ru").inline_keyboard for b in row]
    assert "🏃 Выйти сейчас" in texts


def test_leave_now_line_verdicts():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from student_bot.models import Lesson
    from student_bot.texts import leave_now_line
    tz = ZoneInfo("Europe/Moscow")
    les = Lesson(group="G", day=datetime(2026, 9, 26).date(),
                 starts_at=datetime(2026, 9, 26, 8, 30, tzinfo=tz),
                 ends_at=datetime(2026, 9, 26, 10, 5, tzinfo=tz),
                 subject="М", room="0303")
    assert "успеваешь" in leave_now_line("ru", "🚶", datetime(2026, 9, 26, 8, 0, tzinfo=tz), les)
    late = leave_now_line("ru", "🚇", datetime(2026, 9, 26, 8, 50, tzinfo=tz), les)
    assert "опоздаешь на ~20 мин" in late and "останется ~75 мин" in late
    assert "не успеешь" in leave_now_line("ru", "🚶", datetime(2026, 9, 26, 10, 30, tzinfo=tz), les)
    assert "не посчиталось" in leave_now_line("ru", "🚶", None, les)
