"""Погода в утреннем уведомлении: WeatherAPI.com, ключ в WEATHERAPI_KEY.

Берём почасовой прогноз на время выхода (или на 08:00, если выход не посчитан):
температура, вероятность и тип осадков. Порог зонта: precip_prob >= 50%.
Запрос: GET forecast.json?key=...&q={lat},{lon}&days=1&aqi=no&alerts=no.
Часы forecast.forecastday[*].hour[] — wall time зоны location.tz_id
(проверяем, что это Europe/Moscow; иначе берём зону из ответа как есть),
`when` приводим к ней же: UTC/наивное `when` выбор не сдвигает.
Температура — temp_c как есть, без конвертации (проверено живьём:
hourly temp_c совпадает с current.temp_c). Если запрошенное время уже
прошло (вечером виджет ссылается на утренний exit_at), берём fact из
current.temp_c, а не устаревший почасовой слот: прогноз на прошедший
час — это утро, а не «сейчас на улице».
Вероятность — max(chance_of_rain, chance_of_snow); тип — по
will_it_rain/will_it_snow, шансам и condition.text.
Кэш 30 мин по округлённым координатам хранит сырой почасовой ответ,
час выбирается при каждом вызове. Таймаут короткий; ключа нет или
ошибка — None, утреннее уходит как обычно. Цифры из воздуха не берём.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("bot.weather")

BASE = "https://api.weatherapi.com/v1/forecast.json"
TZ_NAME = "Europe/Moscow"
MSK = ZoneInfo(TZ_NAME)
TTL_S = 30 * 60

SNOW_WORDS = ("snow", "sleet", "blizzard", "снег")
RAIN_WORDS = ("rain", "drizzle", "shower", "дождь")


@dataclass(frozen=True)
class Weather:
    temp_c: float
    precip_prob: int  # 0..100
    kind: str  # "rain" | "snow" | "none"
    umbrella: bool


class WeatherError(RuntimeError):
    pass


_cache: dict[tuple[float, float], tuple[float, dict]] = {}


def _key(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 2), round(lon, 2))


def _api_key(explicit: str | None) -> str:
    return explicit or os.environ.get("WEATHERAPI_KEY", "")


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _kind_of(hour: dict, prob: int) -> str:
    will_rain = _int(hour.get("will_it_rain"))
    will_snow = _int(hour.get("will_it_snow"))
    crain = _int(hour.get("chance_of_rain"))
    csnow = _int(hour.get("chance_of_snow"))
    cond = str(((hour.get("condition") or {}).get("text")) or "").lower()
    if will_snow == 1 or csnow > crain or any(w in cond for w in SNOW_WORDS):
        return "snow"
    if will_rain == 1 or crain > 0 or any(w in cond for w in RAIN_WORDS):
        return "rain"
    return "none" if prob < 20 else "rain"


async def _fetch_hours(lat: float, lon: float, api_key: str | None,
                       http: httpx.AsyncClient | None = None) -> dict:
    """Сырой почасовой прогноз (кэш 30 мин по координатам). Сырой ответ пишем
    в debug-лог — по нему сверяем выбор часа с улицей."""
    key = _key(lat, lon)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    token = _api_key(api_key)
    if not token:
        raise WeatherError("WEATHERAPI_KEY не задан (env). Погода недоступна.")
    own = http is None
    client = http or httpx.AsyncClient(timeout=10.0)
    try:
        r = await client.get(BASE, params={
            "key": token, "q": f"{lat},{lon}",
            "days": 1, "aqi": "no", "alerts": "no"})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise WeatherError("not a JSON object")
        if isinstance(data.get("error"), dict):
            err = data["error"]
            raise WeatherError(f"weatherapi error {err.get('code')}: {err.get('message')}")
        loc = data.get("location") or {}
        tz_id = str(loc.get("tz_id") or TZ_NAME)
        hours: list[dict] = []
        for fday in (data.get("forecast") or {}).get("forecastday") or []:
            if isinstance(fday, dict):
                for h in fday.get("hour") or []:
                    if isinstance(h, dict) and h.get("time"):
                        hours.append(h)
        if not hours:
            raise WeatherError("empty hourly")
        current = data.get("current") if isinstance(data.get("current"), dict) else None
        log.debug("weatherapi raw lat=%.2f lon=%.2f tz=%s localtime=%s days=%d hours=%d first=%s current=%s",
                  lat, lon, tz_id, loc.get("localtime"),
                  len((data.get("forecast") or {}).get("forecastday") or []),
                  len(hours), hours[0].get("time"),
                  current and current.get("temp_c"))
        payload = {"tz_id": tz_id, "hours": hours, "current": current}
        _cache[key] = (time.monotonic(), payload)
        return payload
    finally:
        if own:
            try:
                await client.aclose()
            except Exception:
                pass


def _select(payload: dict, when: datetime) -> Weather:
    """Час, ближайший к `when` в зоне ответа. Наивное `when` считаем
    временем той же зоны."""
    try:
        tz = ZoneInfo(payload.get("tz_id") or TZ_NAME)
    except Exception:
        tz = MSK
    target = when if when.tzinfo is not None else when.replace(tzinfo=tz)
    target = target.astimezone(tz)
    best, best_dt = -1, None
    for i, h in enumerate(payload["hours"]):
        try:
            cur = datetime.strptime(h["time"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except (ValueError, TypeError, KeyError):
            continue
        if best_dt is None or abs((cur - target).total_seconds()) < abs((best_dt - target).total_seconds()):
            best, best_dt = i, cur
    if best < 0:
        raise WeatherError("no parseable hour")
    h = payload["hours"][best]
    prob = max(_int(h.get("chance_of_rain")), _int(h.get("chance_of_snow")))
    kind = _kind_of(h, prob)
    temp_c = float(h["temp_c"])
    src = "hourly"
    cur = payload.get("current")
    if target <= datetime.now(tz) and isinstance(cur, dict) and cur.get("temp_c") is not None:
        # время уже прошло — показываем факт, а не прогноз утреннего слота
        temp_c = float(cur["temp_c"])
        src = "current"
    w = Weather(temp_c=temp_c, precip_prob=prob, kind=kind,
                umbrella=prob >= 50)
    log.info("weather slot=%s temp=%.1f°C prob=%d%% kind=%s src=%s (target=%s)",
             h["time"], w.temp_c, prob, kind, src,
             target.strftime("%Y-%m-%dT%H:%M%z"))
    return w


async def get_weather(lat: float, lon: float, when: datetime,
                      http: httpx.AsyncClient | None = None,
                      api_key: str | None = None) -> Weather | None:
    """Прогноз на час, ближайший к `when`. None = данных нет (молча пропускаем)."""
    try:
        return _select(await _fetch_hours(lat, lon, api_key, http), when)
    except Exception as e:
        log.warning("weather skipped: %s", e)
        return None


def drop_cache() -> None:
    _cache.clear()
