"""Usage simulator: fake users living 90 days with known ground truth.

Training data for the importance and decay models, and test data for habit
detection. Behaviour per topic nature (see catalog.py):
  interest  sustained (recurs for weeks), abandoned (recurs then stops), casual (a few visits)
  trivia    one-off lookups, sometimes a one-day binge
  errand    a short episode, sometimes monthly
  routine   near-daily at a fixed hour
Ground truth `interest` = "worth a memory". Observations only show the pattern, so
the model has to infer the label from timing, like the real app would.
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).parent))
from backend.app.ml.features import decay_features, importance_features  # noqa: E402
from catalog import CATALOG  # noqa: E402

DAYS = 90
EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)
CLUSTER_WINDOW_DAYS = 30
IMPORTANCE_SNAPSHOTS = [30, 45, 60, 75, 90]
DECAY_SNAPSHOTS = [30, 45, 60, 75]
DECAY_HORIZON_DAYS = 14


def to_dt(day: float) -> datetime:
    return EPOCH + timedelta(days=float(day))


@dataclass
class Truth:
    kind: str
    interest: bool
    habit: bool = False
    hour_pref: float | None = None
    weekday_only: bool = False
    active_from: float = 0.0
    active_until: float = DAYS


@dataclass
class Persona:
    seed: int
    truth: dict[str, Truth] = field(default_factory=dict)
    obs: list[dict] = field(default_factory=list)   # topic, day, hint
    hits: list[tuple[str, float]] = field(default_factory=list)  # chat queries about a topic


def _sample_time_of_day(rng, truth: Truth) -> float:
    if truth.hour_pref is not None:
        return float(np.clip(rng.normal(truth.hour_pref, 0.7), 0, 23.9)) / 24
    return float(rng.choice([rng.uniform(8, 12), rng.uniform(13, 18), rng.uniform(19, 24)],
                            p=[0.25, 0.3, 0.45])) / 24 % 1.0


def _add_session(rng, persona: Persona, topic: str, day: int, truth: Truth, hint_mean: float,
                 n_obs: int | None = None) -> None:
    n = n_obs or 1 + rng.poisson(1.5)
    start = day + _sample_time_of_day(rng, truth)
    for _ in range(n):
        t = start + rng.uniform(0, 25) / 1440
        persona.obs.append({"topic": topic, "day": t,
                            "hint": float(np.clip(rng.normal(hint_mean, 0.2), 0, 1))})


def _poisson_days(rng, start: float, end: float, rate: float, weekday_only: bool) -> list[int]:
    days = []
    for d in range(int(start), int(min(end, DAYS))):
        if weekday_only and to_dt(d).weekday() >= 5:
            continue
        if rng.random() < 1 - np.exp(-rate):
            days.append(d)
    return days


def _poisson_hits(rng, persona: Persona, topic: str, start: float, end: float, rate: float) -> None:
    for d in range(int(start), int(min(end, DAYS))):
        for _ in range(rng.poisson(rate)):
            persona.hits.append((topic, d + rng.random()))


def simulate_persona(seed: int, activity: float = 1.0, n_topics: int = 26) -> Persona:
    rng = np.random.default_rng(seed)
    persona = Persona(seed=seed)
    chosen = rng.choice(len(CATALOG), size=n_topics, replace=False)
    for idx in chosen:
        topic, nature, _ = CATALOG[idx]
        if nature == "interest":
            _simulate_interest(rng, persona, topic, activity)
        elif nature == "trivia":
            truth = Truth("trivia", False)
            persona.truth[topic] = truth
            for _ in range(1 + rng.poisson(1.2)):
                binge = rng.random() < 0.2
                _add_session(rng, persona, topic, int(rng.integers(0, DAYS)), truth, 0.25,
                             n_obs=int(rng.integers(6, 13)) if binge else int(rng.integers(1, 4)))
            _poisson_hits(rng, persona, topic, 0, DAYS, 0.01)
        elif nature == "errand":
            truth = Truth("errand", False)
            persona.truth[topic] = truth
            starts = [int(rng.integers(0, DAYS - 3))]
            if rng.random() < 0.2:
                starts = [s for s in (starts[0], starts[0] + 30, starts[0] + 60) if s < DAYS - 3]
            for s in starts:
                for d in range(s, s + int(rng.integers(1, 4))):
                    _add_session(rng, persona, topic, d, truth, 0.3, n_obs=int(rng.integers(2, 5)))
            _poisson_hits(rng, persona, topic, 0, DAYS, 0.01)
        else:
            _simulate_routine(rng, persona, topic, activity)
    persona.obs.sort(key=lambda o: o["day"])
    persona.hits.sort(key=lambda h: h[1])
    return persona


def _simulate_interest(rng, persona: Persona, topic: str, activity: float) -> None:
    r = rng.random()
    habit = rng.random() < 0.35
    hour = float(rng.choice([7, 9, 13, 16, 20, 22])) if habit else None
    weekday_only = habit and rng.random() < 0.4
    if r < 0.62:      # sustained
        start = float(rng.uniform(0, 55))
        truth = Truth("sustained", True, habit, hour, weekday_only, start, DAYS)
        rate = rng.uniform(0.04, 0.5) * activity
        end = DAYS
        hit_rate_after = None
    elif r < 0.80:    # abandoned
        start = float(rng.uniform(0, 35))
        end = start + float(rng.uniform(12, 30))
        truth = Truth("abandoned", True, habit, hour, weekday_only, start, end)
        rate = rng.uniform(0.1, 0.6) * activity
        hit_rate_after = 0.01
    else:             # casual
        truth = Truth("casual", False)
        persona.truth[topic] = truth
        for d in rng.choice(DAYS, size=int(rng.integers(2, 8)), replace=False):
            _add_session(rng, persona, topic, int(d), truth, 0.35)
        _poisson_hits(rng, persona, topic, 0, DAYS, 0.01)
        return
    persona.truth[topic] = truth
    for d in _poisson_days(rng, start, end, rate, weekday_only):
        _add_session(rng, persona, topic, d, truth, 0.65)
    _poisson_hits(rng, persona, topic, start + 2, end, 0.12 * activity)
    if hit_rate_after:
        _poisson_hits(rng, persona, topic, end, DAYS, hit_rate_after)


def _simulate_routine(rng, persona: Persona, topic: str, activity: float) -> None:
    hour = float(rng.choice([7, 8, 13, 18, 21, 23]))
    weekday_only = rng.random() < 0.5
    start = float(rng.uniform(0, 10))
    truth = Truth("routine", True, True, hour, weekday_only, start, DAYS)
    persona.truth[topic] = truth
    for d in _poisson_days(rng, start, DAYS, -np.log(1 - rng.uniform(0.6, 0.95)) * min(activity, 1.2), weekday_only):
        _add_session(rng, persona, topic, d, truth, 0.45, n_obs=int(rng.integers(1, 4)))
    _poisson_hits(rng, persona, topic, start + 2, DAYS, 0.03)


def topic_obs(persona: Persona, topic: str, lo: float, hi: float) -> list[dict]:
    return [o for o in persona.obs if o["topic"] == topic and lo < o["day"] <= hi]


def importance_rows(persona: Persona, snapshot_day: int) -> list[tuple[list[float], int]]:
    rows = []
    now = to_dt(snapshot_day)
    for topic, truth in persona.truth.items():
        members = topic_obs(persona, topic, snapshot_day - CLUSTER_WINDOW_DAYS, snapshot_day)
        if len(members) < 2:
            continue
        feats = importance_features([to_dt(m["day"]) for m in members], now)
        rows.append((feats, int(truth.interest)))
    return rows


def baseline_importance(n_members: int) -> float:
    return min(0.3 + 0.08 * n_members + 0.1, 0.95)


def decay_rows(persona: Persona, snapshot_day: int) -> list[tuple[list[float], int]]:
    rows = []
    now = to_dt(snapshot_day)
    for topic in persona.truth:
        history = topic_obs(persona, topic, -1, snapshot_day)
        recent = [o for o in history if o["day"] > snapshot_day - CLUSTER_WINDOW_DAYS]
        if len(recent) < 2:
            continue
        hits_past = [to_dt(d) for t, d in persona.hits if t == topic and d <= snapshot_day]
        hits_future = [d for t, d in persona.hits if t == topic
                       and snapshot_day < d <= snapshot_day + DECAY_HORIZON_DAYS]
        feats = decay_features(
            age_days=snapshot_day - history[0]["day"],
            days_since_update=snapshot_day - history[-1]["day"],
            importance=baseline_importance(len(recent)),
            evidence_times=[to_dt(o["day"]) for o in history],
            hit_times=hits_past, now=now)
        rows.append((feats, int(len(hits_future) > 0)))
    return rows


def build(personas: list[Persona], rows_fn, snapshots: list[int]):
    X, y, groups = [], [], []
    for p in personas:
        for s in snapshots:
            for feats, label in rows_fn(p, s):
                X.append(feats)
                y.append(label)
                groups.append(p.seed)
    return np.array(X), np.array(y), np.array(groups)


TRAIN_SEEDS = list(range(100, 124))
# Held-out personas use different activity levels so the test is not just the training distribution.
TEST_PERSONAS = [(900 + i, a) for i, a in enumerate([0.6, 0.6, 0.8, 1.3, 1.3, 1.6])]


def train_personas() -> list[Persona]:
    return [simulate_persona(s, activity=0.8 + 0.1 * (s % 5)) for s in TRAIN_SEEDS]


def test_personas() -> list[Persona]:
    return [simulate_persona(s, activity=a) for s, a in TEST_PERSONAS]
