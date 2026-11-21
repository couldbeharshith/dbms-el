import numpy as np

from backend.app.ml.registry import load_model


def gate_features(query_vec, sims: list[float]) -> list[float]:
    """Query embedding (what kind of question) + how close the nearest memories are."""
    top = sorted(sims, reverse=True)[:3] or [0.0]
    return [*map(float, query_vec), float(top[0]), float(np.mean(top))]


class ModelGate:
    """Trained logistic regression (scripts/train_retrieval.py gate): does this chat
    message need personal memory at all? "What is 17 x 23?" gets nothing injected."""

    def __init__(self, model, threshold: float = 0.5):
        self.model = model
        self.threshold = threshold

    def should_inject(self, query: str, query_vec, candidates: list[dict]) -> bool:
        feats = gate_features(query_vec, [c["sim"] for c in candidates])
        return float(self.model.predict_proba([feats])[0][1]) >= self.threshold


def build_gate() -> ModelGate:
    return ModelGate(load_model("gate"))
