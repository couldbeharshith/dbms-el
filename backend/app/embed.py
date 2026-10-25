"""Local embedding service — jinaai/jina-embeddings-v5-omni-small on the GPU.

Loads exactly like temp/test.py (the benchmark the user ran): trust_remote_code,
task="retrieval", modality="vision" (text + image towers), explicit .to(device)
because the PEFT wrapper ignores device_map. Output: 1024-d L2-normalized
vectors in a shared text/image retrieval space.

Prefix rules from the model card (both prefixed forms live in the same space):
  chat queries       -> "Query: ..."
  documents (text)   -> "Document: ..."
  images (documents) -> "Document: <|vision_start|><|image_pad|><|vision_end|>"

The model is loaded in a background thread at startup; every inference is
serialized behind a lock in a worker thread (torch is not asyncio-aware).
"""

import asyncio
import logging
import threading
from typing import Sequence

# backend.app.config sets the HF_* cache env vars at import time, so it MUST be
# imported before torch/transformers (same ordering trick as temp/test.py).
from backend.app import config  # noqa: E402

import torch  # noqa: E402
from PIL import Image  # noqa: E402
from transformers import AutoModel, AutoProcessor  # noqa: E402

log = logging.getLogger("dbms-el.embed")

IMAGE_PROMPT = "Document: <|vision_start|><|image_pad|><|vision_end|>"


class EmbeddingService:
    def __init__(self, model_dir) -> None:
        self._model = None
        self._processor = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._error: Exception | None = None
        self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.model_dir = model_dir
        threading.Thread(target=self._load, daemon=True, name="embed-load").start()

    def _load(self) -> None:
        try:
            log.info("Loading embedding model from %s on %s ...", self.model_dir, self.device)
            model = AutoModel.from_pretrained(
                str(self.model_dir),
                trust_remote_code=True,
                task="retrieval",
                modality="vision",
            ).eval()
            model = model.to(self.device)
            processor = AutoProcessor.from_pretrained(str(self.model_dir), trust_remote_code=True)
            with self._lock:
                self._model, self._processor = model, processor
            log.info("Embedding model ready on %s", self.device)
        except Exception as exc:  # pragma: no cover
            self._error = exc
            log.exception("Embedding model failed to load")
        finally:
            self._ready.set()

    # -- readiness ------------------------------------------------------------
    def wait_ready(self, timeout: float = 180.0) -> bool:
        return self._ready.wait(timeout) and self._error is None

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def error(self) -> Exception | None:
        return self._error

    # -- inference (all under the model lock) ----------------------------------
    def _embed_sync(self, *, text: str | None = None, image: Image.Image | None = None) -> list[float]:
        with self._lock:
            if self._model is None:
                raise RuntimeError("Embedding model not loaded")
            # truncation must stay off: the tokenizer default (512) silently cuts
            # long documents and breaks the image-token count check for big images.
            if image is None:
                inputs = self._processor(text=text, return_tensors="pt", truncation=False)
            else:
                # Downscale huge screenshots: token count grows with pixel area.
                img = image.convert("RGB")
                max_side = max(img.size)
                if max_side > 1024:
                    scale = 1024 / max_side
                    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
                inputs = self._processor(images=img, text=IMAGE_PROMPT,
                                         return_tensors="pt", truncation=False)
            inputs = inputs.to(self._model.device)
            with torch.inference_mode():
                out = self._model.embed(**inputs)
            return out.detach().float().cpu().squeeze(0).tolist()

    def embed_query_sync(self, text: str) -> list[float]:
        return self._embed_sync(text=f"Query: {text}")

    def embed_document_sync(self, text: str) -> list[float]:
        return self._embed_sync(text=f"Document: {text}")

    def embed_image_sync(self, image: Image.Image) -> list[float]:
        return self._embed_sync(image=image.convert("RGB"))

    # -- async wrappers for FastAPI handlers -----------------------------------
    async def embed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query_sync, text)

    async def embed_document(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_document_sync, text)

    async def embed_image(self, image: Image.Image) -> list[float]:
        return await asyncio.to_thread(self.embed_image_sync, image)


service = EmbeddingService(config.EMBED_MODEL_DIR)


def parse_vec_column(value: str | Sequence[float] | None) -> list[float] | None:
    """pgvector returns text like '[0.1,0.2,...]'; parse to floats."""
    if value is None:
        return None
    if isinstance(value, str):
        return [float(x) for x in value.strip("[]()").split(",")] if value else None
    return list(value)
