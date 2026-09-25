"""Pedestrian routing via FOSSGIS (routing.openstreetmap.de/routed-foot).

Why not OSRM demo for foot (verified 2026-09-24): router.project-osrm.org
currently returns IDENTICAL duration/distance for foot/bike/driving profiles
(734 m in 104 s = car speed), i.e. no real foot profile. FOSSGIS serves a real
foot profile (same 734 m in 507 s = 4.5 km/h, correct walking speed).

Policy: max ~1 req/s, reasonable non-commercial use, attribution OSM.
Long-term: self-host OSRM with foot profile (see README).

URL scheme differs from OSRM demo: {base}/routed-foot/route/v1/driving/...
(provider ignores the profile segment; the server fixes the profile).
"""
from __future__ import annotations

import asyncio
import time

import httpx

from .config import Settings
from .routing_base import RouteLeg, RouteResult


class FootRoutingError(RuntimeError):
    pass


class FosFootProvider:
    """Real walking times (not straight-line!). Never present as exact to the
    meter: OSRM foot routing follows sidewalks but knows no closures/weather."""

    supports_transit = False
    supports_arrival_time = False
    supports_live_traffic = False
    name = "fos-foot"

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None,
                 cache_ttl_s: int = 6 * 3600):
        self.s = settings
        self._http = http or httpx.AsyncClient(timeout=20.0)
        self._last_ts = 0.0
        self._cache: dict[tuple[float, float, float, float], tuple[float, int]] = {}
        self._ttl = cache_ttl_s

    def _key(self, a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float, float, float]:
        return (round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5))

    async def foot_seconds(self, a_lonlat: tuple[float, float], b_lonlat: tuple[float, float]) -> int:
        """Walking seconds between (lon, lat) points. Cached + throttled."""
        key = self._key(a_lonlat, b_lonlat)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        now = time.monotonic()
        wait = 1.1 - (now - self._last_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        (flon, flat), (tlon, tlat) = a_lonlat, b_lonlat
        url = f"{self.s.foot_base}/routed-foot/route/v1/driving/{flon},{flat};{tlon},{tlat}"
        try:
            r = await self._http.get(url, params={"overview": "false"})
            r.raise_for_status()
            data = r.json()
            self._last_ts = time.monotonic()
        except Exception as e:
            raise FootRoutingError(f"foot routing failed: {e}") from e
        if data.get("code") != "Ok" or not data.get("routes"):
            raise FootRoutingError(f"foot routing no route: {data.get('code')}")
        secs = int(float(data["routes"][0]["duration"]))
        self._cache[key] = (time.monotonic(), secs)
        return secs

    async def route(self, from_lonlat: tuple[float, float], to_lonlat: tuple[float, float],
                    mode: str = "foot", arrive_by=None) -> RouteResult:
        from datetime import datetime, timezone

        secs = await self.foot_seconds(from_lonlat, to_lonlat)
        return RouteResult(travel_seconds=secs, legs=(RouteLeg("foot", secs),),
                           is_approximate=False,  # deterministic sidewalk routing, no live data to miss
                           calculated_at=datetime.now(timezone.utc), provider=self.name)
