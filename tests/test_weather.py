"""Погода (WeatherAPI.com): порог зонта, недоступность, цельсии без конвертации."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from student_bot.texts import weather_line
from student_bot.weather import Weather, _kind_of, drop_cache, get_weather

TZ = ZoneInfo("Europe/Moscow")
KEY = "test-key"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _hour(t, temp_c, rain=0, snow=0, will_rain=0, will_snow=0, cond="Clear", temp_f=None):
    return {"time": t, "temp_c": temp_c,
            "temp_f": temp_f if temp_f is not None else round(temp_c * 9 / 5 + 32, 1),
            "chance_of_rain": rain, "chance_of_snow": snow,
            "will_it_rain": will_rain, "will_it_snow": will_snow,
            "condition": {"text": cond}}


def _payload(hours, tz="Europe/Moscow"):
    return {"location": {"tz_id": tz, "localtime": "2026-09-28 19:05"},
            "forecast": {"forecastday": [{"date": "2026-09-28", "hour": hours}]}}


def _client(payload=None, fail=False, seen=None):
    def h(req):
        if fail:
            raise httpx.ConnectError("down")
        if seen is not None:
            seen.update(req.url.params)
        return httpx.Response(200, json=payload)
    return httpx.AsyncClient(transport=httpx.MockTransport(h))


def _hours(rain=70, snow=0, temp=9.4, cond="Light rain"):
    return _payload([
        _hour("2026-09-28 18:00", temp - 1, rain - 10, cond=cond),
        _hour("2026-09-28 19:00", temp, rain, snow, int(rain > 0), int(snow > 0), cond),
        _hour("2026-09-28 20:00", temp + 1, rain, cond=cond)])


def test_umbrella_threshold():
    drop_cache()
    when = datetime(2026, 9, 28, 19, 5, tzinfo=TZ)
    w49 = run(get_weather(55.63, 37.52, when, _client(_hours(rain=49)), KEY))
    drop_cache()
    w50 = run(get_weather(55.64, 37.53, when, _client(_hours(rain=50)), KEY))
    drop_cache()
    w0 = run(get_weather(55.65, 37.54, when,
                         _client(_hours(rain=0, cond="Clear")), KEY))
    assert w49.umbrella is False and w50.umbrella is True
    assert w49.kind == "rain" and w0.kind == "none"
    assert w49.temp_c == 9.4 and w49.precip_prob == 49


def test_snow_kind():
    drop_cache()
    w = run(get_weather(55.63, 37.52, datetime(2026, 9, 28, 19, tzinfo=TZ),
                        _client(_hours(rain=0, snow=80, temp=-3.2, cond="Light snow")),
                        KEY))
    assert w.kind == "snow" and w.umbrella is True


def test_prob_is_max_of_rain_and_snow():
    drop_cache()
    w = run(get_weather(55.63, 37.52, datetime(2026, 9, 28, 19, tzinfo=TZ),
                        _client(_hours(rain=30, snow=60, cond="Light snow")), KEY))
    assert w.precip_prob == 60 and w.kind == "snow" and w.umbrella is True


def test_temp_celsius_no_conversion():
    """temp_c берём как есть; temp_f в ответе игнорируем."""
    drop_cache()
    w = run(get_weather(55.63, 37.52, datetime(2026, 9, 28, 19, tzinfo=TZ),
                        _client(_payload([
                            _hour("2026-09-28 19:00", 21.5, temp_f=70.7)])),
                        KEY))
    assert w.temp_c == 21.5


def test_request_params():
    drop_cache()
    seen = {}
    run(get_weather(55.70, 37.60, datetime(2026, 9, 28, 19, 5, tzinfo=TZ),
                    _client(_hours(), seen=seen), KEY))
    assert seen.get("key") == KEY and seen.get("q") == "55.7,37.6"
    assert seen.get("aqi") == "no" and seen.get("alerts") == "no"


def test_utc_and_naive_when_pick_same_msk_slot():
    """Тот же момент в UTC и наивный — тот же слот MSK (сдвига быть не должно)."""
    from zoneinfo import ZoneInfo
    drop_cache()
    msk = datetime(2026, 9, 28, 19, 5, tzinfo=TZ)
    w_msk = run(get_weather(55.71, 37.60, msk, _client(_hours()), KEY))
    drop_cache()
    utc = msk.astimezone(ZoneInfo("UTC"))
    w_utc = run(get_weather(55.71, 37.60, utc, _client(_hours()), KEY))
    drop_cache()
    w_naive = run(get_weather(55.71, 37.60, datetime(2026, 9, 28, 19, 5),
                              _client(_hours()), KEY))
    assert (w_msk.temp_c, w_utc.temp_c, w_naive.temp_c) == (9.4, 9.4, 9.4)


def test_cache_holds_raw_hourly_not_selected_hour():
    """Один HTTP-запрос на координаты; час выбирается при каждом вызове."""
    drop_cache()
    calls = []
    def h(req):
        calls.append(1)
        return httpx.Response(200, json=_hours())
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    w1 = run(get_weather(55.72, 37.60, datetime(2026, 9, 28, 18, 5, tzinfo=TZ),
                         client, KEY))
    w2 = run(get_weather(55.72, 37.60, datetime(2026, 9, 28, 20, 5, tzinfo=TZ),
                         client, KEY))
    assert len(calls) == 1 and (w1.temp_c, w2.temp_c) == (8.4, 10.4)


def test_weather_unavailable_returns_none():
    drop_cache()
    w = run(get_weather(55.63, 37.52, datetime(2026, 9, 28, 19, tzinfo=TZ),
                        _client(fail=True), KEY))
    assert w is None  # утреннее уйдёт без строки погоды


def test_no_key_returns_none_without_http(monkeypatch):
    monkeypatch.delenv("WEATHERAPI_KEY", raising=False)
    drop_cache()
    def h(req):
        raise AssertionError("no HTTP without key")
    w = run(get_weather(55.63, 37.52, datetime(2026, 9, 28, 19, tzinfo=TZ),
                        httpx.AsyncClient(transport=httpx.MockTransport(h)),
                        api_key=""))
    assert w is None


def test_api_error_payload_returns_none():
    drop_cache()
    w = run(get_weather(55.63, 37.52, datetime(2026, 9, 28, 19, tzinfo=TZ),
                        _client({"error": {"code": 2006, "message": "API key is invalid"}}),
                        KEY))
    assert w is None


def test_weather_line_formats():
    assert weather_line("ru", 9.4, 70, "rain") == "🌧 +9°, дождь 70%: возьми зонт"
    assert weather_line("ru", 18.2, 0, "none") == "☀️ +18°, без осадков"
    assert weather_line("ru", -3.6, 30, "snow") == "⛅ -4°, снег 30%"
    assert weather_line("en", 9.4, 70, "rain") == "🌧 +9°, rain 70%: take an umbrella"
    assert weather_line("en", 18.0, 5, "none") == "☀️ +18°, no precipitation"


def test_kind_mapping():
    h = lambda **kw: {"condition": {"text": "Clear"}, **kw}  # noqa: E731
    assert _kind_of(h(will_it_snow=1, chance_of_snow=80), 80) == "snow"
    assert _kind_of(h(will_it_rain=1, chance_of_rain=90), 90) == "rain"
    assert _kind_of({**h(), "condition": {"text": "Light snow"}}, 10) == "snow"
    assert _kind_of(h(), 0) == "none" and _kind_of(h(), 30) == "rain"
