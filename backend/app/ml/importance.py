import uuid
from datetime import datetime, timezone

import numpy as np

from backend.app import config
from backend.app.ml.features import importance_features
from backend.app.ml.interfaces import Assessment
from backend.app.ml.registry import load_model


class HeuristicImportance:
    """Baseline. Every cluster is promoted; importance grows with cluster size and recency."""

    def assess_new(self, user_id: uuid.UUID, members: list[dict]) -> Assessment:
        importance = min(0.3 + 0.08 * len(members) + 0.2 * recency_bonus(members), 0.95)
        return Assessment(importance=importance, promote=True)

    def after_merge(self, existing_importance: float, members: list[dict],
                    new_members: list[dict]) -> float:
        return min(existing_importance + 0.05, 0.95)


def recency_bonus(members: list[dict]) -> float:
    now = datetime.now(timezone.utc)
    ages_h = [(now - m["observed_at"]).total_seconds() / 3600 for m in members]
    return float(np.mean([np.exp(-a / 48.0) for a in ages_h]))

class ModelImportance:
    """Trained classifier (scripts/train_models.py importance): probability that a
    cluster is a real, recurring interest, from its time pattern. Clusters below
    IMPORTANCE_PROMOTE_THRESHOLD stay pending and cost no LLM call."""

    def __init__(self, model):
        self.model = model

    def _probability(self, members: list[dict]) -> float:
        feats = importance_features([m["observed_at"] for m in members])
        return float(self.model.predict_proba([feats])[0][1])

    def assess_new(self, user_id: uuid.UUID, members: list[dict]) -> Assessment:
        p = self._probability(members)
        return Assessment(importance=min(max(p, 0.05), 0.95),
                          promote=p >= config.IMPORTANCE_PROMOTE_THRESHOLD)

    def after_merge(self, existing_importance: float, members: list[dict],
                    new_members: list[dict]) -> float:
        return min(max(self._probability(members), 0.05), 0.95)


def build_model_importance() -> ModelImportance:
    return ModelImportance(load_model("importance"))
