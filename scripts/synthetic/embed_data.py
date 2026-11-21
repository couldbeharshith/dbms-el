"""Embed the generated captions, queries and memories with the local Jina model.

    uv run python scripts/synthetic/embed_data.py
Writes data/synthetic/embeddings.joblib (captions per topic, queries, memories per topic)."""

import json
import sys
from pathlib import Path

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app.embed import service  # noqa: E402

OUT = ROOT / "data" / "synthetic"

if not service.wait_ready(timeout=300):
    sys.exit(f"embedding model failed to load: {service.error}")

result: dict = {}
captions = json.loads((OUT / "captions.json").read_text())
result["captions"] = {}
for topic, observations in captions.items():
    texts = []
    for o in observations:
        text = o["caption"]
        if o.get("ocr_text"):
            text += "\n\nVisible text: " + o["ocr_text"][:2000]
        texts.append(text)
    result["captions"][topic] = np.array([service.embed_document_sync(t) for t in texts], dtype=np.float32)
    print("captions", topic, len(texts))

queries = json.loads((OUT / "queries.json").read_text())
result["queries"] = np.array([service.embed_query_sync(q["text"]) for q in queries], dtype=np.float32)
print("queries", len(queries))

memories_file = OUT / "memories.json"
if memories_file.exists():
    memories = json.loads(memories_file.read_text())
    result["memories"] = {t: np.array(service.embed_document_sync(c), dtype=np.float32)
                          for t, c in memories.items()}
    print("memories", len(memories))

joblib.dump(result, OUT / "embeddings.joblib")
print("saved", OUT / "embeddings.joblib")
