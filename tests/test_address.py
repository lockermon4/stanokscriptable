"""Address verification: parsing, field matching, confirm format. Pure logic."""
from student_bot.address_check import Candidate, format_confirm, match_candidate, parse_address


def _cand(**kw):
    d = dict(lat=55.6, lon=37.5, city="Москва", street="улица Островитянова",
             house_raw="33А Дом аспирантов улица Островитянова", is_house=True, label="x")
    d.update(kw)
    return Candidate(**d)


def test_parse_full():
    p = parse_address("ул. Островитянова, 33А")
    assert (p.street, p.house, p.block) == ("островитянова", "33а", None)
    assert p.street_type == "ул."


def test_parse_corpus():
    p = parse_address("ул. Вавилова, д. 12, корп. 2")
    assert (p.street, p.house, p.block, p.block_kind) == ("вавилова", "12", "2", "корп.")


def test_parse_no_type_single_part():
    p = parse_address("Островитянова 33А")
    assert (p.street, p.house) == ("островитянова", "33а")


def test_parse_city():
    p = parse_address("Москва, Вавилова, 12")
    assert p.city == "Москва" and p.street == "вавилова" and p.house == "12"


def test_match_ok():
    p = parse_address("ул. Островитянова, 33А")
    assert match_candidate(p, _cand())[0] is True


def test_match_street_only_insufficient():
    p = parse_address("ул. Островитянова, 33А")
    ok, reasons = match_candidate(p, _cand(is_house=False, house_raw="улица Островитянова"))
    assert ok is False and any("не дом" in r for r in reasons)


def test_match_wrong_house():
    p = parse_address("ул. Островитянова, 35")
    ok, _ = match_candidate(p, _cand())
    assert ok is False


def test_match_wrong_city():
    p = parse_address("ул. Островитянова, 33А")
    ok, _ = match_candidate(p, _cand(city="Казань"))
    assert ok is False


def test_match_block_required_and_present():
    p = parse_address("ул. Вавилова, 12, корп. 2")
    good = _cand(street="улица Вавилова", house_raw="12к2 Вавилова")
    assert match_candidate(p, good)[0] is True
    bad = _cand(street="улица Вавилова", house_raw="12 Вавилова")
    assert match_candidate(p, bad)[0] is False


def test_match_house_letter():
    p = parse_address("ул. Островитянова, 33Б")
    assert match_candidate(p, _cand())[0] is False


def test_parse_city_first():
    p = parse_address("Казань, ул. Баумана, 1")
    assert (p.city, p.street, p.house) == ("Казань", "баумана", "1")


def test_parse_stroenie_short():
    p = parse_address("Островитянова, 4, с1")
    assert (p.house, p.block, p.block_kind) == ("4", "1", "стр.")


def test_format_confirm():
    assert format_confirm("Москва", "Вавилова", "ул.", "12", "2", "корп.") == \
        "Москва, ул. Вавилова, д. 12, корп. 2"
    assert format_confirm("Москва", "Островитянова", "ул.", "33А", None, None) == \
        "Москва, ул. Островитянова, д. 33А"
