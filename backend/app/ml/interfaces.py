"""Swappable decision points of the memory pipeline.

Each interface has a baseline implementation (the original hand-written logic)
and ML implementations. config.py picks which one runs."""

from dataclasses import dataclass
from typing import Protocol
import uuid


@dataclass
class Assessment:
    importance: float
    promote: bool  # False -> cluster stays "pending" and is not turned into a memory yet


class Clusterer(Protocol):
    window_hours: float      # how far back observations are loaded
    max_observations: int

    def cluster(self, observations: list[dict]) -> list[list[dict]]:
        """observations: newest first, each with id, caption, topics, observed_at, vec.
        Returns groups worth considering as memories."""


class ImportanceScorer(Protocol):
    def assess_new(self, user_id: uuid.UUID, members: list[dict]) -> Assessment:
        """Importance of a cluster that has no memory yet, and whether to create one."""

    def after_merge(self, existing_importance: float, members: list[dict],
                    new_members: list[dict]) -> float:
        """Importance of an existing memory after new evidence was merged in.
        members = the whole cluster, new_members = the part not linked before."""


class QueryGate(Protocol):
    def should_inject(self, query: str, query_vec, candidates: list[dict]) -> bool:
        """Whether this chat message needs personal memory at all."""


class MemoryRanker(Protocol):
    def rank(self, query: str, candidates: list[dict]) -> list[dict]:
        """candidates: dicts with id, content, importance, sim, recency, already ordered
        by the baseline score. Returns them best first."""

class DecayModel(Protocol):
    def keep_probability(self, features: list[float]) -> float:
        """Chance the memory is retrieved again within the next 2 weeks."""
