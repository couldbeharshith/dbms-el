"""Factories that turn the config.py choices into component instances.

If a trained model file is missing (fresh clone), the component falls back to its baseline
and logs how to train it."""

import logging
from functools import lru_cache

from backend.app import config
from backend.app.ml.clustering import build_agglomerative, build_greedy, build_hdbscan
from backend.app.ml.decay import ModelDecay, NoDecay
from backend.app.ml.importance import HeuristicImportance, build_model_importance
from backend.app.ml.interfaces import (Clusterer, DecayModel, ImportanceScorer, MemoryRanker,
                                       QueryGate)
from backend.app.ml.registry import load_model
from backend.app.ml.retrieval_stages import AlwaysInjectGate, KeepOrderRanker

log = logging.getLogger("dbms-el.ml")


def _unknown(kind: str, name: str):
    raise ValueError(f"Unknown {kind} {name!r} in config.py")


def _or_baseline(build, baseline, what: str):
    try:
        return build()
    except FileNotFoundError as exc:
        log.warning("%s; using the baseline for %s", exc, what)
        return baseline()


def get_clusterer() -> Clusterer:
    if config.CLUSTERER == "greedy":
        return build_greedy()
    if config.CLUSTERER == "agglomerative":
        return build_agglomerative()
    if config.CLUSTERER == "hdbscan":
        return build_hdbscan()
    _unknown("CLUSTERER", config.CLUSTERER)


def get_importance_scorer() -> ImportanceScorer:
    if config.IMPORTANCE == "heuristic":
        return HeuristicImportance()
    if config.IMPORTANCE == "model":
        return _or_baseline(build_model_importance, HeuristicImportance, "importance")
    _unknown("IMPORTANCE", config.IMPORTANCE)


def get_decay() -> DecayModel:
    if config.DECAY == "off":
        return NoDecay()
    if config.DECAY == "model":
        return _or_baseline(lambda: ModelDecay(load_model("decay")), NoDecay, "decay")
    _unknown("DECAY", config.DECAY)


@lru_cache(maxsize=1)
def get_gate() -> QueryGate:
    if config.GATE == "always":
        return AlwaysInjectGate()
    if config.GATE == "model":
        from backend.app.ml.gate import build_gate
        return _or_baseline(build_gate, AlwaysInjectGate, "the query gate")
    _unknown("GATE", config.GATE)


@lru_cache(maxsize=1)
def get_ranker() -> MemoryRanker:
    if config.RANKER == "baseline":
        return KeepOrderRanker()
    if config.RANKER == "cross_encoder":
        from backend.app.ml.ranking import CrossEncoderRanker
        return CrossEncoderRanker()
    if config.RANKER == "learned":
        from backend.app.ml.ranking import build_learned
        return _or_baseline(build_learned, KeepOrderRanker, "the ranker")
    _unknown("RANKER", config.RANKER)
