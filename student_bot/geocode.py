"""Dual geocoder: Nominatim (primary) + Photon/komoot (fallback).

Why: Nominatim may return 403/429 for some networks (verified 2026-09-24:
bot-UA blocked from our network, browser-UA passed). Instead of faking the
User-Agent (against policy), we fall back to Photon, which serves the same
OSM data and accepted our honest UA (verified: identical coords for
Вадковский 3А). Both responses are cached; throttle max 1 req/s per service.

Policies:
- Nominatim: https://operations.osmfoundation.org/policies/nominatim/
- Photon: https://photon.komoot.io/ (light use OK, self-host for heavy use)
"""
from __future__ import annotations

import asyncio
import time

import httpx

from .config import Settings


from .address_check import Candidate


class GeocodeError(RuntimeError):
    pass


class DualGeocoder:
    """Drop-in replacement for NominatimGeocoder (same .geocode() signature)."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.s = settings
        self._http = http or httpx.AsyncClient(
            timeout=15.0, headers={"User-Agent": settings.nominatim_user_agent})
        self._cache: dict[str, tuple[float, float] | None] = {}
        self._last: dict[str, float] = {"nominatim": 0.0, "photon": 0.0}

    async def _throttle(self, key: str) -> None:
        now = time.monotonic()
        wait = 1.1 - (now - self._last.get(key, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        self._last[key] = time.monotonic()

    async def _nominatim(self, q: str) -> tuple[float, float] | None:
        await self._throttle("nominatim")
        r = await self._http.get(
            f"{self.s.nominatim_base}/search",
            params={"q": q, "format": "jsonv2", "limit": 1},
            headers={"User-Agent": self.s.nominatim_user_agent},
        )
        if r.status_code in (403, 429):
            return None  # blocked/limited -> caller tries Photon, no exception spam
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return float(data[0]["lat"]), float(data[0]["lon"])

    async def _photon(self, q: str) -> tuple[float, float] | None:
        await self._throttle("photon")
        r = await self._http.get(
            "https://photon.komoot.io/api/",
            params={"q": q, "limit": 1},
            headers={"User-Agent": self.s.nominatim_user_agent},
        )
        r.raise_for_status()
        feats = r.json().get("features", [])
        if not feats:
            return None
        lon, lat = feats[0]["geometry"]["coordinates"][:2]
        return float(lat), float(lon)

    async def geocode(self, address: str) -> tuple[float, float] | None:
        """Returns (lat, lon) or None. Raises GeocodeError only if all fails."""
        key = address.strip()
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]
        errors: list[str] = []
        for name, fn in (("nominatim", self._nominatim), ("photon", self._photon)):
            try:
                res = await fn(key)
                if res is not None:
                    self._cache[key] = res
                    return res
                errors.append(f"{name}: no result/blocked")
            except Exception as e:
                errors.append(f"{name}: {e}")
        # Cache only positive hits; negative would mask a recovered service.
        # Return None (callers treat as route_failed) instead of raising,
        # unless everything errored out.
        if any("no result" not in e for e in errors):
            raise GeocodeError("geocode failed: " + "; ".join(errors))
        return None

    # ---- verification support: detailed candidates + reverse ----

    async def candidates(self, address: str, limit: int = 5) -> list[Candidate]:
        """Detailed candidates for address verification (house-level check)."""
        out: list[Candidate] = []
        seen: set[tuple[float, float]] = set()
        for c in await self._nominatim_all(address, limit):
            k = (round(c.lat, 5), round(c.lon, 5))
            if k not in seen:
                seen.add(k)
                out.append(c)
        if not out:
            for c in await self._photon_all(address, limit):
                k = (round(c.lat, 5), round(c.lon, 5))
                if k not in seen:
                    seen.add(k)
                    out.append(c)
        return out

    async def _nominatim_all(self, q: str, limit: int) -> list[Candidate]:
        await self._throttle("nominatim")
        r = await self._http.get(
            f"{self.s.nominatim_base}/search",
            params={"q": q, "format": "jsonv2", "addressdetails": 1, "limit": limit},
            headers={"User-Agent": self.s.nominatim_user_agent},
        )
        if r.status_code in (403, 429):
            return []
        r.raise_for_status()
        out = []
        for it in r.json():
            addr = it.get("address", {}) or {}
            road = addr.get("road", "")
            hn = addr.get("house_number", "")
            out.append(Candidate(
                lat=float(it["lat"]), lon=float(it["lon"]),
                city=addr.get("city", "") or addr.get("town", "") or addr.get("state", ""),
                street=road, house_raw=f"{hn} {it.get('name', '')} {road}".strip(),
                is_house=bool(hn), label=it.get("display_name", "")))
        return out

    async def _photon_all(self, q: str, limit: int) -> list[Candidate]:
        await self._throttle("photon")
        r = await self._http.get(
            "https://photon.komoot.io/api/",
            params={"q": q, "limit": limit},
            headers={"User-Agent": self.s.nominatim_user_agent},
        )
        r.raise_for_status()
        out = []
        for f in r.json().get("features", []):
            p = f.get("properties", {}) or {}
            lon, lat = f["geometry"]["coordinates"][:2]
            hn = str(p.get("housenumber", "") or "")
            out.append(Candidate(
                lat=float(lat), lon=float(lon),
                city=str(p.get("city", "") or p.get("state", "") or ""),
                street=str(p.get("street", "") or ""),
                house_raw=f"{hn} {p.get('name', '') or ''}".strip(),
                is_house=bool(hn),
                label=", ".join(x for x in [p.get("name", ""), hn,
                                            p.get("street", ""), p.get("city", "")] if x)))
        return out

    async def reverse_label(self, lat: float, lon: float) -> str | None:
        """Nearest-address label for a pin. Display only ('рядом с ...')."""
        try:
            await self._throttle("nominatim")
            r = await self._http.get(
                f"{self.s.nominatim_base}/reverse",
                params={"lat": lat, "lon": lon, "format": "jsonv2"},
                headers={"User-Agent": self.s.nominatim_user_agent},
            )
            if r.status_code not in (403, 429):
                r.raise_for_status()
                if r.json().get("display_name"):
                    return r.json()["display_name"]
        except Exception:
            pass
        try:
            await self._throttle("photon")
            r = await self._http.get("https://photon.komoot.io/reverse",
                                     params={"lat": lat, "lon": lon},
                                     headers={"User-Agent": self.s.nominatim_user_agent})
            r.raise_for_status()
            feats = r.json().get("features", [])
            if feats:
                p = feats[0].get("properties", {}) or {}
                return ", ".join(x for x in [p.get("name", ""), str(p.get("housenumber", "") or ""),
                                             p.get("street", ""), p.get("city", "")] if x) or None
        except Exception:
            pass
        return None


# Backwards-compatible alias (old name used in bot wiring/tests).
NominatimGeocoder = DualGeocoder
