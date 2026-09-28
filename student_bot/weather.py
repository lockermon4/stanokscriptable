"""Погода в утреннем уведомлении: Open-Meteo, бесплатно, без ключа.

Берём почасовой прогноз на время выхода (или на 08:00, если выход не посчитан):
температура, вероятность и тип осадков. Порог зонта: >= 50%.
Кэш 30 мин по округлённым координатам. Таймаут короткий; при любой ошибке —
None, утреннее уходит как обычно. Цифры из воздуха не берём.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

log = logging.getLogger("bot.weather")

BASE = "https://api.open-meteo.com/v1/forecast"
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


_cache: dict[tuple[float, float], tuple[float, Weather | None]] = {}


def _key(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 2), round(lon, 2))


def _kind_of(code: int | None, prob: int) -> str:
    if code in SNOW:
        return "snow"
    if code in RAIN:
        return "rain"
    return "none" if prob < 20 else "rain"


async def get_weather(lat: float, lon: float, when: datetime,
                      http: httpx.AsyncClient | None = None) -> Weather | None:
    """Прогноз на час, ближайший к `when`. None = данных нет (молча пропускаем)."""
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
            "timezone": "Europe/Moscow", "forecast_days": 3})
        r.raise_for_status()
        hourly = r.json().get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            raise ValueError("empty hourly")
        # ближайший час к `when` (парсим явно для надёжности)
        best, best_dt = 0, None
        for i, ts in enumerate(times):
            try:
                cur = datetime.strptime(ts, "%Y-%m-%dT%H:%M")
            except ValueError:
                continue
            cur = cur.replace(tzinfo=when.tzinfo)
            if best_dt is None or abs((cur - when).total_seconds()) < abs((best_dt - when).total_seconds()):
                best, best_dt = i, cur
        temps = hourly.get("temperature_2m", [])
        probs = hourly.get("precipitation_probability", [])
        codes = hourly.get("weathercode", [])
        if not temps or best >= len(temps):
            raise ValueError("no temp")
        prob = int(probs[best]) if best < len(probs) and probs[best] is not None else 0
        code = int(codes[best]) if best < len(codes) and codes[best] is not None else None
        kind = _kind_of(code, prob)
        w = Weather(temp_c=float(temps[best]), precip_prob=prob, kind=kind,
                    umbrella=prob >= 50)
        _cache[key] = (time.monotonic(), w)
        return w
    except Exception as e:
        log.warning("weather skipped: %s", e)
        _cache[key] = (time.monotonic(), None)
        return None
    finally:
        if own:
            try:
                await client.aclose()
            except Exception:
                pass


def drop_cache() -> None:
    _cache.clear()
