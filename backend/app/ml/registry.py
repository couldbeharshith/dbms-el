import joblib

from backend.app import config


def load_model(name: str):
    """Load data/models/<name>.joblib written by scripts/train_models.py."""
    path = config.MODEL_DIR / f"{name}.joblib"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: uv run python scripts/train_models.py {name}")
    return joblib.load(path)["model"]
