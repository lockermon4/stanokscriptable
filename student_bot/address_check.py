"""Address verification before saving: parse -> match field-by-field -> confirm.

Matching rules:
- city must be Moscow (absent city defaults to Moscow and is shown in confirm);
- street + house number must match exactly (normalized);
- corpus/structure must match too, if the user specified it;
- only a house-level result with coordinates is accepted; street/district-only
  matches are insufficient;
- several suitable results -> show variants for choice.
Pure functions; geocoding lives in geocode.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

STREET_TYPES = {
    "улица": "ул.", "ул": "ул.", "ул.": "ул.",
    "проспект": "пр-т.", "пр": "пр-т.", "пр.": "пр-т.", "просп": "пр-т.", "просп.": "пр-т.",
    "переулок": "пер.", "пер": "пер.", "пер.": "пер.",
    "шоссе": "ш.", "ш": "ш.", "ш.": "ш.",
    "бульвар": "бул.", "б": "бул.", "бул": "бул.", "бул.": "бул.",
    "проезд": "пр-д.", "пр-д": "пр-д.", "проезд.": "пр-д.",
    "площадь": "пл.", "пл": "пл.", "пл.": "пл.",
    "набережная": "наб.", "наб": "наб.", "наб.": "наб.",
    "тупик": "туп.", "туп": "туп.", "туп.": "туп.",
    "аллея": "ал.", "ал": "ал.", "ал.": "ал.",
}
HOUSE_WORDS = {"д", "д.", "дом"}
BLOCK_WORDS = {"корп": "корп.", "корп.": "корп.", "корпус": "корп.", "к": "корп.", "к.": "корп.",
               "стр": "стр.", "стр.": "стр.", "строение": "стр.", "с": "стр.", "с.": "стр.",
               "вл": "вл.", "вл.": "вл.", "владение": "вл.", "соор": "соор.", "соор.": "соор."}


def _norm(s: str) -> str:
    return s.casefold().replace("ё", "е").strip()


def _tokens(s: str) -> list[str]:
    return re.findall(r"\d+|[а-яa-z]+", _norm(s))


@dataclass
class ParsedAddress:
    city: str | None = None
    street: str | None = None  # name without type word
    street_type: str | None = None  # normalized short, e.g. "ул."
    house: str | None = None  # digits + optional letter, e.g. "33а", "12"
    block: str | None = None  # digits of корпус/строение, e.g. "2"
    block_kind: str | None = None  # "корп." / "стр." / ...
    raw: str = ""


def _parse_street_token(text: str) -> tuple[str | None, str | None]:
    """'ул. Вавилова' / 'Вавилова' -> (name, type). Type may be None."""
    t = text.strip().strip(",")
    low = _norm(t)
    for word, short in STREET_TYPES.items():
        if low == word or low.startswith(word + " ") or low.startswith(word + "."):
            name = re.sub(r"^" + re.escape(word) + r"\.?\s*", "", low).strip()
            return (name or None, short)
        if low.endswith(" " + word):
            return (low[: -len(word)].strip() or None, short)
    return (low or None, None)


def parse_address(text: str) -> ParsedAddress:
    p = ParsedAddress(raw=text.strip())
    if not p.raw:
        return p
    parts = [x.strip() for x in re.split(r",|;", p.raw) if x.strip()]
    rest: list[str] = []
    for part in parts:
        low = _norm(part)
        if low in ("москва", "moscow", "г. москва", "г москва", "город москва"):
            p.city = "Москва"
            continue
        m = re.match(r"^г\.?\s+(.+)$", low)
        if m:
            p.city = part.strip()
            continue
        # block: корп/стр/вл + number (may share part with house: "12 к. 2").
        # Alternatives ordered longest-first ("строение" before "стр").
        bm = re.search(r"(корпус|строение|владение|корп|соор|стр|вл|к|с)\.?\s*(\d+\s*[а-яa-z]?)", low)
        if bm:
            word = bm.group(1)
            p.block_kind = BLOCK_WORDS.get(word, BLOCK_WORDS.get(word + ".", "корп."))
            p.block = re.sub(r"\s+", "", bm.group(2))
            # house may precede in same part: "д. 12, к. 2" or "12 к.2"
            head = part[: bm.start()].strip(" ,")
            hm = re.search(r"(\d+\s*[а-яa-z]?)", _norm(head))
            if hm and p.house is None:
                h = re.sub(r"\s+", "", hm.group(1))
                # "д." alone head -> still take number
                p.house = h
            if p.street is None and head:
                nm, tp = _parse_street_token(head)
                if nm and not re.fullmatch(r"\d+\s*[а-яa-z]?", nm):
                    p.street, p.street_type = nm, tp or p.street_type
            continue
        # house: д/дом + number, or lone number token ("д. 12", "12", "33А")
        hm = re.match(r"^(д|дом)\.?\s*(\d+\s*[а-яa-z]?)$", low)
        if hm:
            p.house = re.sub(r"\s+", "", hm.group(2))
            continue
        if re.fullmatch(r"\d+\s*[а-яa-z]?", low):
            if p.house is None:
                p.house = re.sub(r"\s+", "", low)
            continue
        # street? (a trailing house number belongs to house, not street)
        nm, tp = _parse_street_token(part)
        if nm and p.street is None:
            hm2 = re.match(r"^(.+?)\s+(\d+\s*[а-яa-z]?)$", nm)
            if hm2 and p.house is None:
                p.house = re.sub(r"\s+", "", hm2.group(2))
                nm = hm2.group(1).strip()
                _, tp = _parse_street_token(nm)
            if nm and not re.fullmatch(r"\d+\s*[а-яa-z]?", nm):
                p.street, p.street_type = nm, tp or p.street_type
                continue
        rest.append(part)
    # Fallback: single part like "Островитянова 33А" (street + house, no type word)
    if p.street is None and p.house is None and len(parts) == 1:
        m = re.match(r"^(.+?)\s+(\d+\s*[а-яa-z]?)$", _norm(parts[0]))
        if m:
            p.street, p.street_type = m.group(1).strip(), None
            p.house = re.sub(r"\s+", "", m.group(2))
    elif p.street is None and p.house is not None and rest:
        # "33А, Островитянова" reversed order
        cand = rest[0]
        nm, tp = _parse_street_token(cand)
        if nm:
            p.street, p.street_type = nm, tp
    # City as first part ("Казань, ул. Баумана, 1"): if the rest is a complete
    # street+house address on its own with a DIFFERENT street, the first part
    # is a city, not the street.
    if p.city is None and len(parts) >= 2 and not re.search(r"\d", parts[0]):
        tmp = parse_address(", ".join(parts[1:]))
        if tmp.street and tmp.house and tmp.street != p.street:
            p.city = parts[0].strip()
            p.street, p.street_type, p.house, p.block, p.block_kind = (
                tmp.street, tmp.street_type, tmp.house, tmp.block, tmp.block_kind)
    return p


@dataclass
class Candidate:
    lat: float
    lon: float
    city: str = ""
    street: str = ""  # raw road name from provider
    house_raw: str = ""  # housenumber + name for block matching
    is_house: bool = False
    label: str = ""


def _street_key(s: str) -> str:
    nm, _ = _parse_street_token(s)
    return _norm(nm or s)


def match_candidate(p: ParsedAddress, c: Candidate) -> tuple[bool, list[str]]:
    """Field-by-field check. Returns (suitable, reasons)."""
    reasons: list[str] = []
    if _norm(c.city) not in ("москва", "moscow", "moskva"):
        return False, [f"не Москва: {c.city or '—'}"]
    if not c.is_house:
        return False, ["точка не дом (улица/район)"]
    if not p.street:
        return False, ["улица не распознана во вводе"]
    if _street_key(c.street) != _street_key(p.street):
        return False, [f"улица не совпала: {c.street}"]
    if not p.house:
        return False, ["номер дома не распознан во вводе"]
    toks = _tokens(c.house_raw)
    digits = re.findall(r"\d+", _norm(p.house))
    letters = re.findall(r"[а-яa-z]+", _norm(p.house))
    if not digits or digits[0] not in toks:
        return False, [f"дом не совпал: {c.house_raw}"]
    if letters:
        # attached letter must follow the house digits ("33а")
        try:
            i = toks.index(digits[0])
            if not (i + 1 < len(toks) and toks[i + 1] == letters[0]):
                return False, [f"буква дома не совпала: {c.house_raw}"]
        except ValueError:
            return False, [f"дом не совпал: {c.house_raw}"]
    if p.block:
        rest = [t for j, t in enumerate(toks) if not (j == toks.index(digits[0]))]
        if p.block not in rest:
            return False, [f"корпус/строение не подтверждён: {c.house_raw}"]
    return True, reasons


def format_confirm(city: str, street: str, street_type: str | None,
                   house: str, block: str | None, block_kind: str | None) -> str:
    tp = f"{street_type} " if street_type else ""
    s = f"{city}, {tp}{street}, д. {house}"
    if block:
        s += f", {block_kind or 'корп.'} {block}"
    return s


def pretty_street(raw: str, fallback: str) -> tuple[str, str | None]:
    nm, tp = _parse_street_token(raw or fallback)
    name = (nm or fallback).strip()
    return (name[:1].upper() + name[1:] if name else fallback, tp)


async def verify_address_text(geocoder, text: str, lang: str = "ru") -> tuple[str, list[tuple[str, float, float]], str]:
    """Parse + geocode + field match. Returns (status, suitable, message).
    status: ok-one | ok-many | not-found | bad-city | unparsed.
    suitable: [(confirm_label, lat, lon)]. Bot is Moscow-only: the query is
    prefixed with Москва unless the user named another city (then refused)."""
    en = lang == "en"
    p = parse_address(text)
    if p.city is not None and _norm(p.city) != "москва":
        return ("bad-city", [],
                (f"I only accept Moscow addresses, but yours is {p.city}. "
                 f"Clarify a Moscow address or send a location pin.") if en else
                (f"Принимаю адреса только по Москве, а у вас — {p.city}. "
                 f"Уточните московский адрес или пришлите геоточку."))
    if not p.street or not p.house:
        return ("unparsed", [],
                ("Couldn't parse the street or house number. Example: “ul. Ostrovityanova, 33А”. "
                 "Or send a location pin (📎 → Location).") if en else
                ("Не разобрал улицу или номер дома. Пример: «ул. Островитянова, 33А». "
                 "Либо пришлите геоточку (скрепка → Геопозиция)."))
    query = text if p.city else f"Москва, {text}"
    cands = await geocoder.candidates(query, limit=5)
    suitable: list[tuple[str, float, float]] = []
    street_only = False
    for c in cands:
        ok, _ = match_candidate(p, c)
        if ok:
            nm, tp = pretty_street(c.street, p.street)
            label = format_confirm("Москва" if not en else "Moscow", nm, tp or p.street_type,
                                   (p.house or "").upper(), p.block, p.block_kind)
            suitable.append((label, c.lat, c.lon))
        elif c.street and not c.is_house:
            street_only = True
    seen: set[str] = set()
    uniq = [s for s in suitable if not (s[0] in seen or seen.add(s[0]))]
    if len(uniq) == 1:
        return ("ok-one", uniq, "")
    if uniq:
        return ("ok-many", uniq[:4], "")
    hint = (" The street is on the map, but I can't find that house — check the number."
            if street_only else "") if en else \
        (" Улица на карте есть, но такого дома не нашёл — проверьте номер."
         if street_only else "")
    return ("not-found", [],
            (f"No exact house found.{hint} Fix the address or send a location pin.") if en else
            (f"Точный дом не найден.{hint} Исправьте адрес или пришлите геоточку."))
