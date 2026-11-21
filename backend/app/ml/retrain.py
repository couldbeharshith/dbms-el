"""Refit the learned ranker from the Train memories votes (fast: logistic regression, no GPU).

The base rows come from scripts/train_retrieval.py ranker (simulated usage, saved to
data/models/ranker_base.joblib). Votes are added with VOTE_WEIGHT and the model is saved and
hot-swapped, so no restart is needed."""

import logging
import threading

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from backend.app import config, db

log = logging.getLogger("dbms-el.retrain")

VOTE_WEIGHT = 5.0
MIN_VOTES = 4
_lock = threading.Lock()
RANKER_FILE = config.MODEL_DIR / "ranker.joblib"
BASE_FILE = config.MODEL_DIR / "ranker_base.joblib"


def fetch_vote_rows(user_id=None) -> tuple[np.ndarray, np.ndarray]:
    """(sim, importance, recency) and label (1 = thumbs up) for every voted memory."""
    sql = (
        "SELECT p.embedding::text AS pe, m.embedding::text AS me, m.importance, f.vote, "
        "exp(-extract(epoch from (now() - m.updated_at)) / 86400.0) AS recency "
        "FROM memory_feedback f JOIN training_prompts p ON p.id = f.prompt_id "
        "JOIN memories m ON m.id = f.memory_id"
    )
    rows = db.query(sql + (" WHERE p.user_id = %s" if user_id else ""), [user_id] if user_id else [])
    X, y = [], []
    for r in rows:
        pe = np.array([float(x) for x in r["pe"].strip("[]").split(",")])
        me = np.array([float(x) for x in r["me"].strip("[]").split(",")])
        X.append([float(pe @ me), r["importance"], r["recency"]])
        y.append(int(r["vote"] == 1))
    return np.array(X).reshape(-1, 3), np.array(y)


def fit_ranker(X_base, y_base, X_votes, y_votes):
    X = np.concatenate([X_base, X_votes]) if len(X_base) else X_votes
    y = np.concatenate([y_base, y_votes]) if len(y_base) else y_votes
    weights = np.concatenate([np.ones(len(y_base)), np.full(len(y_votes), VOTE_WEIGHT)])
    model = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=1000))
    model.fit(X, y, logisticregression__sample_weight=weights)
    return model


def ranker_is_stale() -> bool:
    """True when a vote is newer than the saved ranker (or votes exist and there is no ranker)."""
    newest = db.query_one("SELECT extract(epoch from max(voted_at)) AS t FROM memory_feedback")["t"]
    if newest is None:
        return False
    return not RANKER_FILE.exists() or newest > RANKER_FILE.stat().st_mtime


def retrain_ranker(user_id=None) -> dict:
    from backend.app import ml

    with _lock:
        X_votes, y_votes = fetch_vote_rows(user_id)
        if len(y_votes) < MIN_VOTES or len(set(y_votes.tolist())) < 2:
            return {"retrained": False, "votes": int(len(y_votes)),
                    "reason": f"needs at least {MIN_VOTES} votes with both thumbs up and thumbs down"}
        base = joblib.load(BASE_FILE) if BASE_FILE.exists() else {"X": np.zeros((0, 3)), "y": np.zeros(0)}
        model = fit_ranker(base["X"], base["y"], X_votes, y_votes)
        config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": model}, RANKER_FILE)
        ml.get_ranker.cache_clear()
        weights = dict(zip(["sim", "importance", "recency"], model[-1].coef_[0].round(2).tolist()))
        log.info("Ranker retrained on %d votes: %s", len(y_votes), weights)
        return {"retrained": True, "votes": int(len(y_votes)), "weights": weights}
