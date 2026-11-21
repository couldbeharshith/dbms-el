"""Habit detection: does a group of observations follow a daily rhythm?

Time of day is circular (23:00 is close to 01:00), so the test is the Rayleigh test on
the hour-of-day angles: R is how tightly they cluster (0 = spread out, 1 = same minute
every day) and p says whether that is more than chance. A binomial test on weekday vs
weekend days adds "weekdays only" / "weekends only"."""

from collections import Counter
from datetime import datetime

import numpy as np
from scipy.stats import binomtest

from backend.app import config


def detect_habit(times: list[datetime]) -> dict | None:
    """times must already be in the user's local time zone."""
    days = {t.date() for t in times}
    if len(times) < config.HABIT_MIN_OBSERVATIONS or len(days) < config.HABIT_MIN_DAYS:
        return None

    # One angle per active day (its average time) so a long session does not count as many days.
    by_day: dict = {}
    for t in times:
        by_day.setdefault(t.date(), []).append(t.hour + t.minute / 60)
    day_angles = np.array([np.angle(np.mean(np.exp(2j * np.pi * np.array(h) / 24)))
                           for h in by_day.values()])
    vector = np.mean(np.exp(1j * day_angles))
    n = len(day_angles)
    r = float(abs(vector))
    z = n * r * r
    p = float(np.exp(np.sqrt(1 + 4 * n + 4 * (n * n - (n * r) ** 2)) - (1 + 2 * n)))  # Rayleigh p-value
    if r < config.HABIT_MIN_CONCENTRATION or p > config.HABIT_MAX_P_VALUE:
        return None

    mean_hour = (float(np.angle(vector)) % (2 * np.pi)) * 24 / (2 * np.pi)
    weekday_days = sum(1 for d in days if d.weekday() < 5)
    weekend_days = len(days) - weekday_days
    pattern = "daily"
    if len(days) >= 5:
        if binomtest(weekday_days, len(days), 5 / 7, alternative="greater").pvalue < 0.05 and weekend_days == 0:
            pattern = "weekdays"
        elif binomtest(weekend_days, len(days), 2 / 7, alternative="greater").pvalue < 0.05 and weekday_days == 0:
            pattern = "weekends"
    return {"mean_hour": mean_hour, "concentration": r, "p_value": p, "z": float(z),
            "pattern": pattern, "n_days": len(days)}


def describe_habit(label: str, habit: dict) -> str:
    hour = habit["mean_hour"]
    start, end = int(hour) % 24, (int(hour) + 1) % 24
    part = ("in the early morning" if hour < 6 else "in the morning" if hour < 12 else
            "in the afternoon" if hour < 17 else "in the evening" if hour < 21 else "at night")
    days = {"daily": "most days", "weekdays": "on weekdays", "weekends": "on weekends"}[habit["pattern"]]
    return (f"Habit: the user usually spends time on {label} {days} {part} "
            f"(around {start:02d}:00-{end:02d}:00).")


def dominant_label(members: list[dict]) -> str:
    tags = Counter(t for m in members for t in (m.get("topics") or []))
    return tags.most_common(1)[0][0] if tags else "a recurring activity"
