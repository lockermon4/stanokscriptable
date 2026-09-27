"""Часы работы метро Москвы: открыто 05:30–01:00 (вестибюли; отдельные с 05:20,
первые поезда 05:28–05:50, последние ~01:00; источники: mos.ru, banki.ru 2026).

Чистые функции поверх Europe/Moscow: открылось/закрыто/серая зона
(00:30–01:00 — последние поезда, успеть не гарантировано).
Закрыто = 01:00–05:30. Полночь обрабатывается явно (закрытие после полуночи).
"""
from __future__ import annotations

from datetime import datetime, time as dtime

OPEN_H, OPEN_M = 5, 30
CLOSE_H, CLOSE_M = 1, 0
GRAY_MIN = 30  # за столько до закрытия — "последние поезда"


def opens_at_text() -> str:
    return f"{OPEN_H:02d}:{OPEN_M:02d}"


def metro_state(now: datetime) -> str:
    """'open' | 'gray' | 'closed' по wall time (naive ок).
    closed = 01:00–05:30; gray = 00:30–01:00 (последние поезда)."""
    t = now.time()
    if dtime(CLOSE_H, CLOSE_M) <= t < dtime(OPEN_H, OPEN_M):
        return "closed"
    if dtime(0, 60 - GRAY_MIN) <= t < dtime(CLOSE_H, CLOSE_M):
        return "gray"
    return "open"
