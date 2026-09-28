"""Места рядом с корпусом: кофейни/столовые/продукты через 2GIS Places.

Проверено живьём 2026-09-28 (названия полей — из реального ответа, не выдуманы):
  GET https://catalog.api.2gis.com/3.0/items
    ?key=...&q=столовая&point=lon,lat&radius=1000&page_size=3
    &fields=items.point,items.address_name,items.rubrics
  -> {"meta": {"code": 200, ...},
      "result": {"items": [{"id", "name", "type": "branch",
                             "point": {"lat", "lon"},
                             "address_name": "Сущёвская улица, 21 ст8",
                             "rubrics": [{"name": "Столовые", "kind": "primary", ...}]}],
                 "total": N}}
Без key: {"meta": {"code": 400, ...}} — это тоже проверяем (meta.code != 200
считаем ошибкой, т.к. HTTP при этом может быть 200).

Кэш 24 ч по (округлённые координаты корпуса, запрос) — экономим квоту 2GIS.
Время пешком — существующим TwoGisRouting.walking, не "на глаз".
"""
from __future__ import annotations

import asyncio
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

PLACES_URL = "https://catalog.api.2gis.com/3.0/items"
PLACES_TTL_S = 24 * 3600
RADIUS_M = 1000
PAGE_SIZE = 8

# (поисковый запрос, подпись категории-фолбэка)
QUERIES: tuple[tuple[str, str], ...] = (
    ("кофейня", "кофейня"),
    ("кафе", "кафе"),
    ("столовая", "столовая"),
    ("продукты", "магазин"),
)


class PlacesError(RuntimeError):
    pass


@dataclass
class Place:
    name: str
    category: str  # "Столовые" из primary-рубрики или подпись запроса
    lat: float
    lon: float
    address: str = ""
    walk_min: int | None = None  # заполняется attach_walk_times
    raw: dict = field(default_factory=dict, compare=False)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class TwoGisPlaces:
    """Поиск мест + TTL-кэш 24 ч. use_cache=False — свежий запрос."""

    def __init__(self, api_key: str = "", http: httpx.AsyncClient | None = None,
                 ttl_s: int = PLACES_TTL_S, radius_m: int = RADIUS_M):
        self.key = api_key or os.environ.get("GIS_API_KEY", "")
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._cache: dict[tuple, tuple[float, list[Place]]] = {}
        self.ttl = ttl_s
        self.radius_m = radius_m

    def drop(self) -> None:
        self._cache.clear()

    def _cached(self, key: tuple) -> list[Place] | None:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        return None

    async def _query(self, q: str, lat: float, lon: float) -> list[dict]:
        if not self.key:
            raise PlacesError("GIS_API_KEY не задан (env). Поиск мест невозможен.")
        last: Exception | None = None
        for attempt in (0, 1, 2):
            try:
                r = await self._http.get(PLACES_URL, params={
                    "key": self.key, "q": q, "point": f"{lon},{lat}",
                    "radius": self.radius_m, "page_size": PAGE_SIZE,
                    "fields": "items.point,items.address_name,items.rubrics"})
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    raise PlacesError("Places вернул не JSON-объект")
                if data.get("meta", {}).get("code") != 200:
                    raise PlacesError(f"Places meta.code={data.get('meta', {}).get('code')}")
                res = data.get("result") or {}
                items = res.get("items") or []
                return items if isinstance(items, list) else []
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = e
                await asyncio.sleep(0.5 * (attempt + 1))
        raise PlacesError(f"Places недоступен после 3 попыток: {last}")

    @staticmethod
    def _category(item: dict, fallback: str) -> str:
        for rub in item.get("rubrics") or []:
            if isinstance(rub, dict) and rub.get("kind") == "primary" and rub.get("name"):
                return str(rub["name"])
        return fallback

    async def search(self, lat: float, lon: float,
                     use_cache: bool = True) -> list[Place]:
        """Все категории разом (параллельно): мердж, дедуп по id, сортировка."""
        key = (round(lat, 4), round(lon, 4))
        if use_cache:
            parts = [self._cached((*key, q)) for q, _ in QUERIES]
            if all(p is not None for p in parts):
                return self._merge(lat, lon, [p for p in parts if p is not None])

        async def one(q: str, label: str) -> list[Place]:
            qkey = (*key, q)
            hit = self._cached(qkey) if use_cache else None
            if hit is not None:
                return hit
            items = await self._query(q, lat, lon)
            part = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                pt = it.get("point") or {}
                try:
                    plat, plon = float(pt["lat"]), float(pt["lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                part.append(Place(name=str(it.get("name") or "Без названия"),
                                  category=self._category(it, label),
                                  lat=plat, lon=plon,
                                  address=str(it.get("address_name") or ""),
                                  raw={"id": it.get("id")}))
            self._cache[qkey] = (time.monotonic(), part)
            return part

        found: list[Place] = []
        results = await asyncio.gather(*[one(q, label) for q, label in QUERIES],
                                       return_exceptions=True)
        for res in results:
            if isinstance(res, asyncio.CancelledError):
                raise res  # shutdown не глотаем
            if isinstance(res, list):
                found.extend(res)
            # упавшая категория просто пропускается (остальные всё равно покажем)
        if not found:
            errs = [r for r in results if isinstance(r, Exception)]
            if errs:
                first_err = errs[0]
                if isinstance(first_err, PlacesError):
                    raise first_err
                raise PlacesError(str(first_err))
        return self._merge(lat, lon, [found])

    @staticmethod
    def _merge(lat: float, lon: float, parts: list[list[Place]]) -> list[Place]:
        seen: set[str] = set()
        out: list[Place] = []
        for part in parts:
            for p in part:
                pid = p.raw.get("id")
                if pid in seen:
                    continue
                seen.add(pid)
                out.append(p)
        out.sort(key=lambda p: haversine_m(lat, lon, p.lat, p.lon))
        return out

    async def attach_walk_times(self, places: list[Place], from_xy: tuple[float, float],
                                router, limit: int = 4) -> None:
        """Время пешком существующим роутингом (mutates walk_min in place)."""
        flat, flon = from_xy
        for p in places[:limit]:
            try:
                opts = await router.walking((flat, flon), (p.lat, p.lon))
                p.walk_min = opts[0].duration_s // 60 if opts else None
            except Exception:
                p.walk_min = None

    async def close(self) -> None:
        try:
            await self._http.aclose()
        except Exception:
            pass


_shared: TwoGisPlaces | None = None


def _client(http: httpx.AsyncClient | None = None, api_key: str | None = None) -> TwoGisPlaces:
    global _shared
    if http is not None or api_key is not None:
        return TwoGisPlaces(api_key or "", http)
    if _shared is None:
        _shared = TwoGisPlaces()
    return _shared


async def nearby(lat: float, lon: float, **kw: Any) -> list[Place]:
    """Места рядом. kw: http, api_key, use_cache (для тестов/свежих запросов)."""
    c = _client(kw.get("http"), kw.get("api_key"))
    return await c.search(lat, lon, use_cache=kw.get("use_cache", True))
