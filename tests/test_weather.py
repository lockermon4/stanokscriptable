"""Погода: порог зонта, недоступность, строка."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from student_bot.texts import weather_line
from student_bot.weather import Weather, _kind_of, drop_cache, get_weather

TZ = ZoneInfo("Europe/Moscow")


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _hourly(temp=9.4, prob=70, code=61):
    return {"hourly": {
        "time": ["2026-09-28T07:00", "2026-09-28T08:00", "2026-09-28T09:00"],
        "temperature_2m": [temp - 1, temp, temp + 1],
        "precipitation_probability": [prob - 10, prob, prob],
        "weathercode": [code, code, code]}}


def _client(payload=None, fail=False):
    def h(req):
        if fail:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=payload)
    return httpx.AsyncClient(transport=httpx.MockTransport(h))


def test_umbrella_threshold():
    drop_cache()
    when = datetime(2026, 9, 28, 8, 10, tzinfo=TZ)
    w49 = run(get_weather(55.63, 37.52, when, _client(_hourly(prob=49))))
    drop_cache()
    w50 = run(get_weather(55.64, 37.53, when, _client(_hourly(prob=50))))
    drop_cache()
    w0 = run(get_weather(55.65, 37.54, when, _client(_hourly(prob=0, code=0))))
    assert w49.umbrella is False and w50.umbrella is True
    assert w49.kind == "rain" and w0.kind == "none"
    assert w49.temp_c == 9.4 and w49.precip_prob == 49


def test_snow_kind():
    drop_cache()
    w = run(get_weather(55.63, 37.52, datetime(2026, 9, 28, 8, tzinfo=TZ),
                        _client(_hourly(temp=-3.2, prob=80, code=73))))
    assert w.kind == "snow" and w.umbrella is True


def test_weather_unavailable_returns_none():
    drop_cache()
    w = run(get_weather(55.63, 37.52, datetime(2026, 9, 28, 8, tzinfo=TZ),
                        _client(fail=True)))
    assert w is None  # утреннее уйдёт без строки погоды


def test_weather_line_formats():
    assert weather_line("ru", 9.4, 70, "rain") == "🌧 +9°, дождь 70%: возьми зонт"
    assert weather_line("ru", 18.2, 0, "none") == "☀️ +18°, без осадков"
    assert weather_line("ru", -3.6, 30, "snow") == "⛅ -4°, снег 30%"
    assert weather_line("en", 9.4, 70, "rain") == "🌧 +9°, rain 70%: take an umbrella"
    assert weather_line("en", 18.0, 5, "none") == "☀️ +18°, no precipitation"


def test_kind_mapping():
    assert _kind_of(95, 90) == "rain" and _kind_of(71, 90) == "snow"
    assert _kind_of(0, 0) == "none" and _kind_of(3, 30) == "rain"


def _hourly_tz():
    return {"utc_offset_seconds": 10800,
            "hourly_units": {"temperature_2m": "°C"},
            "hourly": {
                "time": ["2026-09-28T18:00", "2026-09-28T19:00", "2026-09-28T20:00"],
                "temperature_2m": [12.6, 11.9, 11.2],
                "precipitation_probability": [0, 0, 0],
                "weathercode": [1, 1, 0]}}


def _client_capture(payload, seen):
    def h(req):
        seen.update(req.url.params)
        return httpx.Response(200, json=payload)
    return httpx.AsyncClient(transport=httpx.MockTransport(h))


def test_request_pins_units_and_timezone():
    """temperature_unit и timezone заданы явно, не дефолты API."""
    drop_cache()
    seen = {}
    w = run(get_weather(55.70, 37.60, datetime(2026, 9, 28, 19, 5, tzinfo=TZ),
                        _client_capture(_hourly_tz(), seen)))
    assert seen.get("temperature_unit") == "celsius"
    assert seen.get("timezone") == "Europe/Moscow"
    assert w.temp_c == 11.9  # слот 19:00 MSK


def test_utc_and_naive_when_pick_same_msk_slot():
    """Тот же момент в UTC и наивный — тот же слот MSK (было: сдвиг на 3 ч)."""
    from zoneinfo import ZoneInfo
    drop_cache()
    msk = datetime(2026, 9, 28, 19, 5, tzinfo=TZ)
    w_msk = run(get_weather(55.71, 37.60, msk, _client(_hourly_tz())))
    drop_cache()
    utc = msk.astimezone(ZoneInfo("UTC"))
    w_utc = run(get_weather(55.71, 37.60, utc, _client(_hourly_tz())))
    drop_cache()
    w_naive = run(get_weather(55.71, 37.60, datetime(2026, 9, 28, 19, 5),
                              _client(_hourly_tz())))
    assert (w_msk.temp_c, w_utc.temp_c, w_naive.temp_c) == (11.9, 11.9, 11.9)


def test_cache_holds_raw_hourly_not_selected_hour():
    """Один HTTP-запрос на координаты; час выбирается при каждом вызове."""
    drop_cache()
    calls = []
    def h(req):
        calls.append(1)
        return httpx.Response(200, json=_hourly_tz())
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    w1 = run(get_weather(55.72, 37.60, datetime(2026, 9, 28, 18, 5, tzinfo=TZ), client))
    w2 = run(get_weather(55.72, 37.60, datetime(2026, 9, 28, 20, 5, tzinfo=TZ), client))
    assert len(calls) == 1 and (w1.temp_c, w2.temp_c) == (12.6, 11.2)
