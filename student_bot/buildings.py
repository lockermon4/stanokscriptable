"""Building directory: code -> exact address. Stored SEPARATELY from routing logic.

Never guess an unknown building address: lookup returns None and callers
must say so honestly instead of inventing a route.

Stankin note (verified 2026-09-23): /api/schedule has NO corpus field, only
`cabinet` ("0303", "357(ж)", "Фрезер 303(ММ)", "Стадион 1", ...).
`resolve_cabinet` maps cabinet -> building code via explicit prefix rules
(default from buildings.yaml `cabinet_default`). Only "Фрезер*" is a certain
match (ш. Фрезер, 10); the rest default to the main building and are flagged
heuristic=True so the bot can label the address honestly.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Building:
    code: str
    address: str
    lat: float | None = None
    lon: float | None = None


class BuildingStore:
    def __init__(self, buildings: list[Building], cabinet_rules: dict[str, str] | None = None,
                 cabinet_default: str = ""):
        # normalize keys: strip + casefold for tolerant matching
        self._by_code = {b.code.strip().casefold(): b for b in buildings if b.code.strip()}
        self._rules = {k.strip().casefold(): v for k, v in (cabinet_rules or {}).items()}
        self._default = cabinet_default

    @classmethod
    def from_mapping(cls, mapping: dict[str, object], cabinet_rules: dict[str, str] | None = None,
                     cabinet_default: str = "") -> "BuildingStore":
        items: list[Building] = []
        for code, val in mapping.items():
            if isinstance(val, str):
                items.append(Building(code=code, address=val))
            elif isinstance(val, dict):
                items.append(
                    Building(
                        code=code,
                        address=str(val.get("address", "")),
                        lat=val.get("lat"),  # type: ignore
                        lon=val.get("lon"),  # type: ignore
                    )
                )
        return cls(items, cabinet_rules=cabinet_rules, cabinet_default=cabinet_default)

    def lookup(self, code: str) -> Building | None:
        if not code:
            return None
        return self._by_code.get(code.strip().casefold())

    def resolve_cabinet(self, cabinet: str) -> tuple[Building | None, bool]:
        """Map `cabinet` ("Фрезер 303(ММ)") -> (Building, heuristic).

        heuristic=True means prefix/default rule, not an explicit per-cabinet row.
        Empty cabinet -> (None, False): honestly unknown, do not guess.
        """
        cab = (cabinet or "").strip()
        if not cab:
            return None, False
        low = cab.casefold()
        for prefix, code in self._rules.items():
            if low.startswith(prefix):
                return self.lookup(code), False  # explicit rule = certain
        if self._default:
            return self.lookup(self._default), True
        return None, False

    @property
    def codes(self) -> list[str]:
        return sorted(b.code for b in self._by_code.values())


def load_buildings_yaml(path: str) -> BuildingStore:
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None  # type: ignore
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if yaml is not None:
        data = yaml.safe_load(text) or {}
        buildings = data.get("buildings", data if isinstance(data, dict) else {})
        rules = data.get("cabinet_rules", {}) if isinstance(data, dict) else {}
        default = data.get("cabinet_default", "") if isinstance(data, dict) else ""
    else:  # minimal fallback parser for `code: address` lines
        buildings = {}
        rules, default = {}, ""
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line in ("buildings:", "cabinet_rules:"):
                continue
            if line.startswith("cabinet_default:"):
                default = line.split(":", 1)[1].strip().strip("'\"")
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                buildings[k.strip().strip("'\"")] = v.strip().strip("'\"")
    if not isinstance(buildings, dict):
        return BuildingStore([])
    if not isinstance(rules, dict):
        rules = {}
    return BuildingStore.from_mapping(buildings, cabinet_rules={str(k): str(v) for k, v in rules.items()},
                                      cabinet_default=str(default or ""))
