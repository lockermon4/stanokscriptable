"""Groups cascade: primary -> GROUPS_JSON_URL -> local cache."""
import asyncio
import json

import httpx

from student_bot.config import Settings
from student_bot.normalize import normalize_groups
from student_bot.schedule_client import ScheduleApiError, ScheduleClient


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _settings(tmp_path, fallback=""):
    return Settings(schedule_api_base="https://x.test",
                    groups_fallback_url=fallback,
                    groups_cache_file=str(tmp_path / "g.json"))


def test_primary_ok_and_cached(tmp_path):
    def h(req):
        return httpx.Response(200, json={"items": ["ИДБ-26-14"]})
    sc = ScheduleClient(_settings(tmp_path),
                        http=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    assert run(sc.get_groups_raw()) == {"items": ["ИДБ-26-14"]}
    assert json.load(open(tmp_path / "g.json"))["data"] == {"items": ["ИДБ-26-14"]}


def test_fallback_json_and_plain_text(tmp_path):
    def h(req):
        if "fallback" in str(req.url):
            return httpx.Response(200, json=["А-1", "Б-2"])
        return httpx.Response(500, text="down")
    sc = ScheduleClient(_settings(tmp_path, "https://cdn.test/fallback.json"),
                        http=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    assert run(sc.get_groups_raw()) == ["А-1", "Б-2"]

    def h2(req):
        if "fallback" in str(req.url):
            return httpx.Response(200, text="А-1\nБ-2\n")
        return httpx.Response(500, text="down")
    sc2 = ScheduleClient(_settings(tmp_path, "https://cdn.test/fallback.txt"),
                         http=httpx.AsyncClient(transport=httpx.MockTransport(h2)))
    assert run(sc2.get_groups_raw()) == ["А-1", "Б-2"]


def test_cache_when_all_down(tmp_path):
    (tmp_path / "g.json").write_text(json.dumps({"at": 0, "data": {"items": ["КЭШ-1"]}}),
                                     encoding="utf-8")

    def h(req):
        return httpx.Response(500, text="down")
    sc = ScheduleClient(_settings(tmp_path, "https://cdn.test/f.json"),
                        http=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    assert run(sc.get_groups_raw()) == {"items": ["КЭШ-1"]}


def test_all_fail_raises(tmp_path):
    def h(req):
        return httpx.Response(500, text="down")
    sc = ScheduleClient(_settings(tmp_path), http=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    try:
        run(sc.get_groups_raw())
        raise AssertionError("should raise")
    except ScheduleApiError:
        pass


def test_shapes_normalize():
    assert [g.name for g in normalize_groups(["А-1"])] == ["А-1"]
    assert [g.name for g in normalize_groups({"items": ["А-1"]})] == ["А-1"]
    assert [g.name for g in normalize_groups({"groups": [{"name": "А-1"}]})] == ["А-1"]
