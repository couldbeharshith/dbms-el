"""Train the pattern models on simulated usage and report them against baselines.

    uv run python scripts/train_models.py importance
    uv run python scripts/train_models.py decay
Models are saved to data/models/*.joblib."""

import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "synthetic"))
import sim  # noqa: E402
from backend.app.ml.features import DECAY_FEATURES, IMPORTANCE_FEATURES  # noqa: E402

MODEL_DIR = ROOT / "data" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


def fit_and_report(name: str, features: list[str], rows_fn, snapshots, baseline_fn, baseline_name: str):
    X_tr, y_tr, _ = sim.build(sim.train_personas(), rows_fn, snapshots)
    X_te, y_te, _ = sim.build(sim.test_personas(), rows_fn, snapshots)
    print(f"{name}: train {len(y_tr)} rows ({y_tr.mean():.0%} positive), "
          f"held-out test {len(y_te)} rows ({y_te.mean():.0%} positive)")

    candidates = {
        "gradient_boosting": GradientBoostingClassifier(n_estimators=150, max_depth=3, random_state=0),
        "logistic_regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)),
    }
    fitted = {}
    for label, model in candidates.items():
        model.fit(X_tr, y_tr)
        p = model.predict_proba(X_te)[:, 1]
        fitted[label] = (model, roc_auc_score(y_te, p))
        print(f"  {label:20s} test AUC {fitted[label][1]:.3f}  accuracy@0.5 {accuracy_score(y_te, p >= 0.5):.3f}")
    base = baseline_fn(X_te)
    print(f"  {baseline_name:20s} test AUC {roc_auc_score(y_te, base):.3f}")

    best_label = max(fitted, key=lambda k: fitted[k][1])
    model = fitted[best_label][0]
    joblib.dump({"model": model, "features": features, "kind": best_label}, MODEL_DIR / f"{name}.joblib")
    print(f"  saved {best_label} -> data/models/{name}.joblib")
    if best_label == "gradient_boosting":
        top = sorted(zip(features, model.feature_importances_), key=lambda x: -x[1])[:5]
        print("  top features:", ", ".join(f"{n} {v:.2f}" for n, v in top))


def train_importance():
    n_obs = IMPORTANCE_FEATURES.index("n_obs")
    # Current baseline: promotes every cluster; its only signal is cluster size.
    fit_and_report("importance", IMPORTANCE_FEATURES, sim.importance_rows, sim.IMPORTANCE_SNAPSHOTS,
                   lambda X: X[:, n_obs], "baseline (size only)")


def train_decay():
    days_since = DECAY_FEATURES.index("days_since_last_evidence")
    fit_and_report("decay", DECAY_FEATURES, sim.decay_rows, sim.DECAY_SNAPSHOTS,
                   lambda X: -X[:, days_since], "baseline (recency)")


if __name__ == "__main__":
    {"importance": train_importance, "decay": train_decay}[sys.argv[1]]()
