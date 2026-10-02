"""HTTP-204 пустой день + нормализация пустого расписания."""
import asyncio
from datetime import date

import httpx

from student_bot.config import Settings
from student_bot.normalize import normalize_day
from student_bot.schedule_client import ScheduleClient


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_204_means_empty_day_not_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    sc = ScheduleClient(Settings(),
                        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert run(sc.get_day_raw("ИДБ-26-14", "2026-09-27")) == {"items": []}


def test_empty_items_normalizes_to_zero_lessons():
    sched, skipped = normalize_day({"items": []}, group="ИДБ-26-14",
                                   day=date(2026, 9, 27), tz_name="Europe/Moscow")
    assert sched.count == 0 and skipped == 0
