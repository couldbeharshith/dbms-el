"""Time-pattern features of a group of observations (a cluster or a memory's evidence).

Shared by the importance model, the habit detector and the decay model. Pure
functions over timestamps, so they work the same on live data and simulated data."""

from datetime import datetime, timezone

import numpy as np

SESSION_GAP_MIN = 30
IMPORTANCE_FEATURES = [
    "n_obs", "n_days", "n_sessions", "span_days", "days_since_last", "days_since_first",
    "active_day_ratio", "mean_gap_days", "gap_cv", "max_obs_one_day_share",
    "n_days_last7",
]


def _days(ts: list[datetime]) -> np.ndarray:
    return np.array([t.timestamp() / 86400 for t in ts])


def importance_features(times: list[datetime], now: datetime | None = None) -> list[float]:
    now = now or datetime.now(timezone.utc)
    t = np.sort(_days(times))
    now_d = now.timestamp() / 86400
    day_ids = np.floor(t).astype(int)
    unique_days, per_day = np.unique(day_ids, return_counts=True)
    gaps = np.diff(unique_days).astype(float)
    sessions = 1 + int(np.sum(np.diff(t) * 1440 > SESSION_GAP_MIN))
    span = float(t[-1] - t[0])
    return [
        float(len(t)),
        float(len(unique_days)),
        float(sessions),
        span,
        float(now_d - t[-1]),
        float(now_d - t[0]),
        float(len(unique_days) / (span + 1.0)),
        float(gaps.mean()) if len(gaps) else 0.0,
        float(gaps.std() / gaps.mean()) if len(gaps) and gaps.mean() > 0 else 0.0,
        float(per_day.max() / len(t)),
        float(np.sum(unique_days >= np.floor(now_d) - 6)),
    ]


DECAY_FEATURES = [
    "age_days", "days_since_update", "importance", "n_evidence", "evidence_last14",
    "days_since_last_evidence", "hits_total", "hits_last14", "days_since_last_hit",
]


def decay_features(age_days: float, days_since_update: float, importance: float,
                   evidence_times: list[datetime], hit_times: list[datetime],
                   now: datetime | None = None) -> list[float]:
    now = now or datetime.now(timezone.utc)
    now_d = now.timestamp() / 86400
    ev = _days(evidence_times) if evidence_times else np.array([])
    hits = _days(hit_times) if hit_times else np.array([])
    return [
        age_days,
        days_since_update,
        importance,
        float(len(ev)),
        float(np.sum(ev >= now_d - 14)) if len(ev) else 0.0,
        float(now_d - ev.max()) if len(ev) else age_days,
        float(len(hits)),
        float(np.sum(hits >= now_d - 14)) if len(hits) else 0.0,
        float(now_d - hits.max()) if len(hits) else age_days,
    ]
