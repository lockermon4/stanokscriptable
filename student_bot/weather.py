"""Погода в утреннем уведомлении: Open-Meteo, бесплатно, без ключа.

Берём почасовой прогноз на время выхода (или на 08:00, если выход не посчитан):
температура, вероятность и тип осадков. Порог зонта: >= 50%.
Единицы и таймзона заданы явно (temperature_unit=celsius,
timezone=Europe/Moscow) — на дефолты API не полагаемся.
Часы hourly-массива — wall time заявленной таймзоны (проверяем по
utc_offset_seconds из ответа), `when` приводим к ней же: UTC/наивное `when`
больше не сдвигает выбор на часы. Кэш 30 мин по округлённым координатам
хранит сырой почасовой ответ, час выбирается при каждом вызове.
Таймаут короткий; при любой ошибке — None, утреннее уходит как обычно.
Цифры из воздуха не берём.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("bot.weather")

BASE = "https://api.open-meteo.com/v1/forecast"
TZ_NAME = "Europe/Moscow"
MSK = ZoneInfo(TZ_NAME)
TTL_S = 30 * 60

# WMO weathercode -> тип осадков (стандартная таблица WMO, не выдумано).
RAIN = {51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99}
SNOW = {71, 73, 75, 77, 85, 86}


@dataclass(frozen=True)
class Weather:
    temp_c: float
    precip_prob: int  # 0..100
    kind: str  # "rain" | "snow" | "none"
    umbrella: bool


_cache: dict[tuple[float, float], tuple[float, dict]] = {}


def _key(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 2), round(lon, 2))


def _kind_of(code: int | None, prob: int) -> str:
    if code in SNOW:
        return "snow"
    if code in RAIN:
        return "rain"
    return "none" if prob < 20 else "rain"


def _response_tz(offset) -> timezone | ZoneInfo:
    """Таймзона часов hourly: из utc_offset_seconds ответа, иначе MSK."""
    off = offset
    if isinstance(off, bool):
        off = None
    if isinstance(off, (int, float)):
        return timezone(timedelta(seconds=int(off)))
    return MSK


async def _fetch_hourly(lat: float, lon: float,
                        http: httpx.AsyncClient | None = None) -> dict:
    """Сырой почасовой ответ (кэш 30 мин по координатам). Сырой ответ пишем
    в debug-лог — по нему сверяем выбор часа с улицей."""
    key = _key(lat, lon)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    own = http is None
    client = http or httpx.AsyncClient(timeout=10.0)
    try:
        r = await client.get(BASE, params={
            "latitude": lat, "longitude": lon,
            "hourly": "temperature_2m,precipitation_probability,weathercode",
            "temperature_unit": "celsius", "timezone": TZ_NAME, "forecast_days": 3})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        temps = hourly.get("temperature_2m") or []
        if not times or not temps:
            raise ValueError("empty hourly")
        log.debug("open-meteo raw lat=%.2f lon=%.2f offset=%s units=%s slots=%d first=%s",
                  lat, lon, data.get("utc_offset_seconds"),
                  (data.get("hourly_units") or {}).get("temperature_2m"),
                  len(times), times[0])
        payload = {"offset": data.get("utc_offset_seconds"),
                   "times": list(times), "temps": list(temps),
                   "probs": list(hourly.get("precipitation_probability") or []),
                   "codes": list(hourly.get("weathercode") or [])}
        _cache[key] = (time.monotonic(), payload)
        return payload
    finally:
        if own:
            try:
                await client.aclose()
            except Exception:
                pass


def _select(payload: dict, when: datetime) -> Weather:
    """Час, ближайший к `when`. Часы — wall time таймзоны ответа;
    наивное `when` считаем временем той же зоны."""
    tz = _response_tz(payload.get("offset"))
    target = when if when.tzinfo is not None else when.replace(tzinfo=tz)
    target = target.astimezone(tz)
    best, best_dt = -1, None
    for i, ts in enumerate(payload["times"]):
        try:
            cur = datetime.strptime(ts, "%Y-%m-%dT%H:%M").replace(tzinfo=tz)
        except (ValueError, TypeError):
            continue
        if best_dt is None or abs((cur - target).total_seconds()) < abs((best_dt - target).total_seconds()):
            best, best_dt = i, cur
    if best < 0 or best >= len(payload["temps"]):
        raise ValueError("no parseable hour")
    temps, probs, codes = payload["temps"], payload["probs"], payload["codes"]
    prob = int(probs[best]) if best < len(probs) and probs[best] is not None else 0
    code = int(codes[best]) if best < len(codes) and codes[best] is not None else None
    kind = _kind_of(code, prob)
    w = Weather(temp_c=float(temps[best]), precip_prob=prob, kind=kind,
                umbrella=prob >= 50)
    log.info("weather slot=%s temp=%.1f°C prob=%d%% kind=%s (target=%s)",
             payload["times"][best], w.temp_c, prob, kind,
             target.strftime("%Y-%m-%dT%H:%M%z"))
    return w


async def get_weather(lat: float, lon: float, when: datetime,
                      http: httpx.AsyncClient | None = None) -> Weather | None:
    """Прогноз на час, ближайший к `when`. None = данных нет (молча пропускаем)."""
    try:
        return _select(await _fetch_hourly(lat, lon, http), when)
    except Exception as e:
        log.warning("weather skipped: %s", e)
        return None


def drop_cache() -> None:
    _cache.clear()
