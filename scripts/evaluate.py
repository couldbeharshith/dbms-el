"""One report comparing every ML component with its baseline.

    uv run python scripts/evaluate.py            everything that has its data ready
    uv run python scripts/evaluate.py habits     one section: clustering | habits | importance | decay | gate | ranker

Importance, decay, gate and ranker are retrained, so their saved models are refreshed too."""

import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import adjusted_rand_score, silhouette_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "synthetic"))
import sim  # noqa: E402
import train_models  # noqa: E402
import train_retrieval  # noqa: E402
from backend.app.ml.clustering import (AgglomerativeClusterer, GreedyCentroidClusterer,
                                           HDBSCANClusterer)  # noqa: E402
from backend.app.ml.habits import detect_habit  # noqa: E402

EMBEDDINGS = ROOT / "data" / "synthetic" / "embeddings.joblib"


def clustering():
    if not EMBEDDINGS.exists():
        print("clustering: skipped (run scripts/synthetic/embed_data.py first)")
        return
    captions = joblib.load(EMBEDDINGS)["captions"]
    rng = np.random.default_rng(3)
    clusterers = {
        "greedy centroid, current (0.50)": GreedyCentroidClusterer(0.50, 2),
        "greedy centroid, tuned (0.35)": GreedyCentroidClusterer(0.35, 2),
        "agglomerative (distance 0.70)": AgglomerativeClusterer(0.70, 2),
        "HDBSCAN (default settings)": HDBSCANClusterer(2, 1),
        "HDBSCAN (tuned, PCA 10)": HDBSCANClusterer(5, 1, "eom", 10),
    }
    scores = {name: [] for name in clusterers}
    for persona in sim.test_personas():
        obs = [o for o in persona.obs if 60 < o["day"] <= 90 and o["topic"] in captions]
        items = [{"id": i, "topic": o["topic"], "vec": captions[o["topic"]][rng.integers(len(captions[o["topic"]]))]}
                 for i, o in enumerate(obs)]
        truth = {it["id"]: it["topic"] for it in items}
        for name, clusterer in clusterers.items():
            found = clusterer.cluster(items[::-1])
            label = {it["id"]: -1 for it in items}
            for c, members in enumerate(found):
                for m in members:
                    label[m["id"]] = c
            ids = [it["id"] for it in items]
            ari = adjusted_rand_score([truth[i] for i in ids], [label[i] for i in ids])
            grouped = [i for i in ids if label[i] >= 0]
            sil = (silhouette_score(np.stack([items[i]["vec"] for i in grouped]), [label[i] for i in grouped])
                   if len(set(label[i] for i in grouped)) > 1 else float("nan"))
            scores[name].append((ari, sil, len(found), len(set(truth.values())), len(grouped) / len(ids)))
    print("clustering (30 days of simulated observations, true labels = topics):")
    for name, rows in scores.items():
        a, s, n, t, cov = np.mean(rows, axis=0)
        print(f"  {name:24s} ARI {a:.3f}  silhouette {s:.3f}  clusters {n:.1f} (true topics {t:.1f})  observations clustered {cov:.0%}")


def habits():
    tp = fp = fn = detectable_total = 0
    for persona in sim.test_personas():
        for topic, truth in persona.truth.items():
            obs = sim.topic_obs(persona, topic, 60, 90)
            times = [sim.to_dt(o["day"]) for o in obs]
            days = {t.date() for t in times}
            detectable = len(times) >= 5 and len(days) >= 4
            found = detect_habit(times) is not None
            detectable_total += detectable and truth.habit
            if truth.habit and detectable and found:
                tp += 1
            elif truth.habit and detectable and not found:
                fn += 1
            elif not truth.habit and found:
                fp += 1
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    print(f"habits (test personas, last 30 days): precision {precision:.3f}  recall {recall:.3f}  "
          f"(true positives {tp}, false positives {fp}, missed {fn})")


SECTIONS = {
    "clustering": clustering,
    "habits": habits,
    "importance": train_models.train_importance,
    "decay": train_models.train_decay,
    "gate": train_retrieval.train_gate,
    "ranker": train_retrieval.train_ranker,
}

if __name__ == "__main__":
    for name in sys.argv[1:] or SECTIONS:
        print(f"\n=== {name} ===")
        try:
            SECTIONS[name]()
        except FileNotFoundError as exc:
            print(f"skipped: {exc}")
