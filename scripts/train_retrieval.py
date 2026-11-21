"""Train and evaluate the retrieval models: the query gate and the learned ranker.

    uv run python scripts/train_retrieval.py gate
    uv run python scripts/train_retrieval.py ranker

Needs data/synthetic/embeddings.joblib (see scripts/synthetic/embed_data.py). The ranker also
learns from thumbs up/down votes in the Train memories tab when the database has any.
Models are saved to data/models/{gate,ranker}.joblib.

Assumption baked into the simulated training pools: users ask about what they are doing
now, so a personal query is paired with a memory pool where its topic was recently active."""

import json
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "synthetic"))
import sim  # noqa: E402
from backend.app.ml.gate import gate_features  # noqa: E402

SYN = ROOT / "data" / "synthetic"
MODEL_DIR = ROOT / "data" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
CANDIDATES = 20
rng = np.random.default_rng(7)


def load_data():
    queries = json.loads((SYN / "queries.json").read_text())
    emb = joblib.load(SYN / "embeddings.joblib")
    memories = json.loads((SYN / "memories.json").read_text())
    topics = sorted(emb["memories"])
    M = np.stack([emb["memories"][t] for t in topics])
    texts = [memories[t] for t in topics]
    return queries, emb["queries"], topics, M, texts


def build_pools(personas, topics, snapshots):
    """One pool per (persona, day): which topics have a memory, with importance and recency."""
    index = {t: i for i, t in enumerate(topics)}
    pools = []
    for p in personas:
        for day in snapshots:
            ids, imp, rec = [], [], []
            for topic in p.truth:
                if topic not in index:
                    continue
                obs = sim.topic_obs(p, topic, day - sim.CLUSTER_WINDOW_DAYS, day)
                if len(obs) < 2:
                    continue
                ids.append(index[topic])
                imp.append(sim.baseline_importance(len(obs)))
                rec.append(float(np.exp(-(day - obs[-1]["day"]))))
            if len(ids) >= 5:
                pools.append({"ids": np.array(ids), "importance": np.array(imp), "recency": np.array(rec)})
    return pools


def split_queries(n):
    order = rng.permutation(n)
    cut = int(n * 0.7)
    return set(order[:cut].tolist()), set(order[cut:].tolist())


# ---------------------------------------------------------------- gate -----
def train_gate():
    queries, Q, topics, M, _ = load_data()
    train_q, test_q = split_queries(len(queries))
    train_pools = build_pools(sim.train_personas(), topics, [45, 60, 75, 90])
    test_pools = build_pools(sim.test_personas(), topics, [45, 60, 75, 90])

    def rows(query_ids, pools):
        X, y, max_sim = [], [], []
        for i in query_ids:
            for k in rng.choice(len(pools), size=6, replace=False):
                pool = pools[k]
                sims = M[pool["ids"]] @ Q[i]
                X.append(gate_features(Q[i], sims.tolist()))
                relevant = {topics.index(t) for t in queries[i]["relevant"] if t in topics}
                y.append(int(queries[i]["kind"] == "personal" and bool(relevant & set(pool["ids"].tolist()))))
                max_sim.append(float(sims.max()))
        return np.array(X), np.array(y), np.array(max_sim)

    X_tr, y_tr, ms_tr = rows(sorted(train_q), train_pools)
    X_te, y_te, ms_te = rows(sorted(test_q), test_pools)
    print(f"gate: train {len(y_tr)} rows ({y_tr.mean():.0%} need memory), test {len(y_te)} rows ({y_te.mean():.0%})")

    model = make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=2000))
    model.fit(X_tr, y_tr)
    p = model.predict_proba(X_te)[:, 1]
    thresholds = np.linspace(ms_tr.min(), ms_tr.max(), 100)
    best_t = max(thresholds, key=lambda t: accuracy_score(y_tr, ms_tr >= t))
    print(f"  always inject (current)      accuracy {y_te.mean():.3f}")
    print(f"  similarity threshold {best_t:.2f}   accuracy {accuracy_score(y_te, ms_te >= best_t):.3f}  AUC {roc_auc_score(y_te, ms_te):.3f}")
    print(f"  logistic regression gate     accuracy {accuracy_score(y_te, p >= 0.5):.3f}  AUC {roc_auc_score(y_te, p):.3f}")
    joblib.dump({"model": model}, MODEL_DIR / "gate.joblib")
    print("  saved data/models/gate.joblib")


# -------------------------------------------------------------- ranker -----
def rerank_matrix(queries, topics, texts):
    """Cross-encoder score of every (query, memory) pair, cached on disk."""
    cache = SYN / "rerank_scores.joblib"
    if cache.exists():
        return joblib.load(cache)
    from backend.app.ml.ranking import rerank_scores
    R = np.zeros((len(queries), len(topics)), dtype=np.float32)
    for i, q in enumerate(queries):
        R[i] = rerank_scores(q["text"], texts)
        if i % 50 == 0:
            print(f"  reranking queries {i}/{len(queries)}")
    joblib.dump(R, cache)
    return R


def ranking_groups(query_ids, queries, Q, M, R, topics, pools):
    groups = []
    for i in query_ids:
        if queries[i]["kind"] != "personal":
            continue
        relevant = {topics.index(t) for t in queries[i]["relevant"] if t in topics}
        usable = [p for p in pools if relevant & set(p["ids"].tolist())]
        if not usable:
            continue
        weights = np.array([max(p["recency"][np.isin(p["ids"], list(relevant))]) + 0.1 for p in usable])
        for k in rng.choice(len(usable), size=4, p=weights / weights.sum()):
            pool = usable[k]
            sims = M[pool["ids"]] @ Q[i]
            base = 0.6 * sims + 0.25 * pool["importance"] + 0.15 * pool["recency"]
            top = np.argsort(-base)[:CANDIDATES]
            X = np.stack([sims[top], pool["importance"][top], pool["recency"][top], R[i][pool["ids"][top]]], axis=1)
            y = np.array([int(pool["ids"][j] in relevant) for j in top])
            if y.any():
                groups.append((X, y))
    return groups


def metrics(groups, score_fn):
    hit1 = hit3 = mrr = 0.0
    for X, y in groups:
        order = np.argsort(-score_fn(X))
        ranks = np.where(y[order] == 1)[0]
        hit1 += ranks[0] == 0
        hit3 += ranks[0] < 3
        mrr += 1 / (ranks[0] + 1)
    n = len(groups)
    return hit1 / n, hit3 / n, mrr / n


def load_votes():
    """Thumbs up/down from the Train memories tab, if the database is reachable."""
    try:
        from backend.app import db
        from backend.app.ml.retrain import fetch_vote_rows
        db.init_pool()
        rows = fetch_vote_rows()
        db.close_pool()
        return rows
    except Exception as exc:
        print("  (no votes loaded:", type(exc).__name__, ")")
        return np.zeros((0, 3)), np.zeros(0)


def train_ranker():
    from backend.app.ml.retrain import BASE_FILE, fit_ranker

    queries, Q, topics, M, texts = load_data()
    train_q, test_q = split_queries(len(queries))
    R = rerank_matrix(queries, topics, texts)
    snaps = [45, 60, 75, 90]
    g_tr = ranking_groups(sorted(train_q), queries, Q, M, R, topics, build_pools(sim.train_personas(), topics, snaps))
    g_te = ranking_groups(sorted(test_q), queries, Q, M, R, topics, build_pools(sim.test_personas(), topics, snaps))
    X_tr = np.concatenate([g[0] for g in g_tr])
    y_tr = np.concatenate([g[1] for g in g_tr])
    # Column 3 (cross-encoder score) is only used for the comparison table below.
    joblib.dump({"X": X_tr[:, :3], "y": y_tr}, BASE_FILE)  # the backend refits from these + votes

    X_v, y_v = load_votes()
    if len(y_v):
        print(f"  adding {len(y_v)} thumbs votes ({int(y_v.sum())} up)")
    model = fit_ranker(X_tr[:, :3], y_tr, X_v, y_v)

    print(f"ranker: {len(g_tr)} train / {len(g_te)} test queries; the table shows hit@1, hit@3, MRR on test")
    for name, fn in [
        ("current weights 0.6/0.25/0.15", lambda X: 0.6 * X[:, 0] + 0.25 * X[:, 1] + 0.15 * X[:, 2]),
        ("cosine only", lambda X: X[:, 0]),
        ("cross-encoder only", lambda X: X[:, 3]),
        ("learned (LR on sim, importance, recency)", lambda X: model.predict_proba(X[:, :3])[:, 1]),
    ]:
        h1, h3, mrr = metrics(g_te, fn)
        print(f"  {name:42s} hit@1 {h1:.3f}  hit@3 {h3:.3f}  MRR {mrr:.3f}")
    coef = model[-1].coef_[0]
    print("  learned weights (standardized):", ", ".join(f"{n} {c:+.2f}" for n, c in zip(["sim", "importance", "recency"], coef)))
    joblib.dump({"model": model}, MODEL_DIR / "ranker.joblib")
    print("  saved data/models/ranker.joblib and ranker_base.joblib")


if __name__ == "__main__":
    {"gate": train_gate, "ranker": train_ranker}[sys.argv[1]]()
