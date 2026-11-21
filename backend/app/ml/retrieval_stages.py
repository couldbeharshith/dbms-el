class AlwaysInjectGate:
    """Baseline. Every chat message gets memory context."""

    def should_inject(self, query: str, query_vec, candidates: list[dict]) -> bool:
        return True


class KeepOrderRanker:
    """Baseline. The SQL already ordered candidates by 0.6*cosine + 0.25*importance + 0.15*recency."""

    def rank(self, query: str, candidates: list[dict]) -> list[dict]:
        return candidates
