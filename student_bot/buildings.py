"""Building directory: code -> exact address. Stored SEPARATELY from routing logic.

Never guess an unknown building address: lookup returns None and callers
must say so honestly instead of inventing a route.

Stankin note: /api/schedule has NO corpus field, only `cabinet`.
Mapping cabinet -> building is an ORDERED regex list from buildings.yaml
(first match wins); only `confirmed` buildings may be routed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Building:
    code: str
    address: str
    lat: float | None = None
    lon: float | None = None


class BuildingStore:
    def __init__(self, buildings: list[Building], cabinet_rules: dict[str, str] | None = None,
                 cabinet_default: str = "", confirmed: set[str] | None = None,
                 cabinet_regex: list[tuple[str, str]] | None = None):
        # normalize keys: strip + casefold for tolerant matching
        self._by_code = {b.code.strip().casefold(): b for b in buildings if b.code.strip()}
        self._rules = {k.strip().casefold(): v for k, v in (cabinet_rules or {}).items()}
        self._default = cabinet_default
        # confirmed=None -> all confirmed (programmatic use/tests);
        # explicit set (from YAML) -> only listed codes may be routed.
        self._confirmed = None if confirmed is None else {c.strip().casefold() for c in confirmed}
        self._regex: list[tuple[re.Pattern, str]] = [
            (re.compile(pat, re.IGNORECASE), code) for pat, code in (cabinet_regex or [])]

    @classmethod
    def from_mapping(cls, mapping: dict[str, object], cabinet_rules: dict[str, str] | None = None,
                     cabinet_default: str = "", confirmed: set[str] | None = None,
                     cabinet_regex: list[tuple[str, str]] | None = None) -> "BuildingStore":
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
        return cls(items, cabinet_rules=cabinet_rules, cabinet_default=cabinet_default,
                   confirmed=confirmed, cabinet_regex=cabinet_regex)

    def _is_confirmed(self, code: str) -> bool:
        if self._confirmed is None:
            return True
        return code.strip().casefold() in self._confirmed

    def lookup(self, code: str) -> Building | None:
        if not code:
            return None
        b = self._by_code.get(code.strip().casefold())
        if b is None or not self._is_confirmed(b.code):
            return None
        return b

    def resolve_cabinet(self, cabinet: str) -> tuple[Building | None, bool]:
        """Map `cabinet` -> (Building, heuristic) via ordered regex rules,
        then legacy prefix rules, then default. Unconfirmed buildings resolve
        to None (no routing until confirmed). Empty cabinet -> (None, False)."""
        cab = (cabinet or "").strip()
        if not cab:
            return None, False
        for rx, code in self._regex:
            if rx.search(cab):
                b = self.lookup(code)
                return (b, False) if b is not None else (None, False)
        low = cab.casefold()
        for prefix, code in self._rules.items():
            if low.startswith(prefix):
                b = self.lookup(code)
                return (b, False) if b is not None else (None, False)
        if self._default:
            b = self.lookup(self._default)
            return (b, True) if b is not None else (None, False)
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
        confirmed = data.get("confirmed", None) if isinstance(data, dict) else None
        regex_raw = data.get("cabinet_regex", []) if isinstance(data, dict) else []
    else:  # minimal fallback parser for `code: address` lines
        buildings = {}
        rules, default, confirmed = {}, "", None
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
    conf = {str(c) for c in confirmed} if isinstance(confirmed, list) else None
    rx: list[tuple[str, str]] = []
    if isinstance(regex_raw, list):
        for item in regex_raw:
            if isinstance(item, dict) and item.get("pattern") and item.get("building"):
                rx.append((str(item["pattern"]), str(item["building"])))
    return BuildingStore.from_mapping(buildings, cabinet_rules={str(k): str(v) for k, v in rules.items()},
                                      cabinet_default=str(default or ""), confirmed=conf,
                                      cabinet_regex=rx)
