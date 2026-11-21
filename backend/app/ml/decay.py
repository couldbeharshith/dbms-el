class NoDecay:
    """Baseline. Memories never expire."""

    def keep_probability(self, features: list[float]) -> float:
        return 1.0


class ModelDecay:
    """Trained classifier (scripts/train_models.py decay): chance a memory is retrieved
    again within 14 days, from its age, evidence recency and retrieval history."""

    def __init__(self, model):
        self.model = model

    def keep_probability(self, features: list[float]) -> float:
        return float(self.model.predict_proba([features])[0][1])
