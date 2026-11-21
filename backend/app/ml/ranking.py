"""Retrieval rankers that reorder the SQL candidates.

CrossEncoderRanker: a pretrained reranker reads (query, memory) together and scores relevance.
LearnedRanker: logistic regression over [cosine, importance, recency], replacing the
hand-set 0.6/0.25/0.15 weights (no reranker at query time, so it stays fast). Trained on simulated usage plus the thumbs up/down votes from the
Train memories tab (scripts/train_retrieval.py)."""

import threading

import numpy as np

from backend.app import config  # noqa: F401  (sets the Hugging Face cache env vars before transformers)
from backend.app.ml.registry import load_model

RERANK_MODEL = "BAAI/bge-reranker-base"
RANK_FEATURES = ["sim", "importance", "recency"]

_lock = threading.Lock()
_loaded: dict = {}


def rerank_scores(query: str, texts: list[str]) -> np.ndarray:
    """Relevance probability of each text for the query (0-1)."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    with _lock:
        if not _loaded:
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            tokenizer = AutoTokenizer.from_pretrained(RERANK_MODEL)
            model = AutoModelForSequenceClassification.from_pretrained(RERANK_MODEL).eval()
            if device != "cpu":
                model = model.half()
            _loaded.update(tokenizer=tokenizer, model=model.to(device), device=device)
        tokenizer, model, device = _loaded["tokenizer"], _loaded["model"], _loaded["device"]
        out = []
        for i in range(0, len(texts), 16):
            batch = tokenizer([query] * len(texts[i:i + 16]), texts[i:i + 16], padding=True,
                              truncation=True, max_length=512, return_tensors="pt").to(device)
            with torch.inference_mode():
                out.append(model(**batch).logits.view(-1).float().cpu())
    return torch.sigmoid(torch.cat(out)).numpy()


class CrossEncoderRanker:
    def rank(self, query: str, candidates: list[dict]) -> list[dict]:
        if not candidates:
            return candidates
        scores = rerank_scores(query, [c["content"] for c in candidates])
        for c, s in zip(candidates, scores):
            c["rerank"] = float(s)
        return sorted(candidates, key=lambda c: -c["rerank"])


class LearnedRanker:
    def __init__(self, model):
        self.model = model

    def rank(self, query: str, candidates: list[dict]) -> list[dict]:
        if not candidates:
            return candidates
        X = [[c["sim"], c["importance"], c["recency"]] for c in candidates]
        probs = self.model.predict_proba(X)[:, 1]
        for c, p in zip(candidates, probs):
            c["learned"] = float(p)
        return sorted(candidates, key=lambda c: -c["learned"])


def build_learned() -> LearnedRanker:
    return LearnedRanker(load_model("ranker"))
