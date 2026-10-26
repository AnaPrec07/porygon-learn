"""Spaced repetition (SM-2 flavoured). Grades: 0 again, 1 hard, 2 good, 3 easy."""
from __future__ import annotations

from datetime import datetime, timedelta

AGAIN, HARD, GOOD, EASY = 0, 1, 2, 3
MIN_EASE = 1.3


def schedule(ease: float, interval: float, reps: int, lapses: int, grade: int, now: datetime) -> dict:
    if grade not in (AGAIN, HARD, GOOD, EASY):
        raise ValueError("grade must be 0-3")
    if grade == AGAIN:
        return {"ease": max(MIN_EASE, ease - 0.2), "interval_days": 0.0, "reps": 0,
                "lapses": lapses + 1, "due": now + timedelta(minutes=10)}
    if reps == 0:
        new_interval = 4.0 if grade == EASY else 1.0
    elif reps == 1:
        new_interval = {HARD: 3.0, GOOD: 6.0, EASY: 8.0}[grade]
    else:
        factor = {HARD: 1.2, GOOD: ease, EASY: ease * 1.3}[grade]
        new_interval = max(interval + 1, round(interval * factor))
    new_ease = max(MIN_EASE, ease + {HARD: -0.15, GOOD: 0.0, EASY: 0.15}[grade])
    return {"ease": new_ease, "interval_days": float(new_interval), "reps": reps + 1,
            "lapses": lapses, "due": now + timedelta(days=new_interval)}
