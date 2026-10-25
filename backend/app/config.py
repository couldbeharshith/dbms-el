"""Central configuration. Imported before torch/transformers anywhere else so
the Hugging Face cache env vars are in place first (see temp/test.py)."""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
SCREENSHOT_DIR = DATA_DIR / "screenshots"
HF_HOME_DIR = DATA_DIR / "hf_home"

for _d in (DATA_DIR, SCREENSHOT_DIR, HF_HOME_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Keep every Hugging Face download/cache on D: instead of the user profile.
os.environ.setdefault("HF_HOME", str(HF_HOME_DIR))
os.environ.setdefault("HF_HUB_CACHE", str(HF_HOME_DIR / "hub"))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_HOME_DIR / "hub"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(HF_HOME_DIR / "transformers"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://harshith:mysecretpassword@localhost:5432/mydb"
)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
EMBED_MODEL_DIR = Path(os.getenv("EMBED_MODEL_DIR", r"D:\AI\jina_omni_test\model"))
EMBED_DIM = 1024

DEFAULT_USERNAME = os.getenv("DEFAULT_USERNAME", "harshith")

# Consolidation tuning (kept here so the "simple rule/score" is visible in one place).
CONSOLIDATION_INTERVAL_SEC = 120
CLUSTER_SIMILARITY_THRESHOLD = 0.78
CLUSTER_MIN_OBSERVATIONS = 3
MEMORY_MERGE_SIMILARITY = 0.85
# Memory-to-memory duplicate suppression (same text granularity, reliable):
# a candidate whose embedding is this close to an existing memory is linked to
# it instead of inserting a near-identical row; the periodic dedupe pass folds
# any that slipped through — both without any LLM calls.
MEMORY_DUPLICATE_SIMILARITY = 0.88
RETENTION_CHECK_INTERVAL_SEC = 6 * 3600

# Perceptual-fingerprint dedupe: skip captures that look identical to a RECENT
# one (idle screens, cursor blinks, tiny edits). Windowed so an unchanged
# desktop isn't permanently skipped. Exact SHA-256 dedupe stays global.
# Empirical distances (mean abs pixel diff): idle/blink ~0.0001, small text
# edit ~0.001-0.0013, different page with photo ~0.015+, different app ~0.7.
PHASH_WINDOW_MINUTES = 10
PHASH_MAX_MAD = 0.005

# A screenshot's URL is attached only when the foreground app was a browser.
BROWSER_TITLE_TAILS = {
    "google chrome", "chrome", "brave", "microsoft edge", "edge",
    "mozilla firefox", "firefox", "chromium",
}
