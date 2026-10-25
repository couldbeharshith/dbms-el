"""gpt-6-luna vision captioning + text completion — the ONLY OpenAI usage.

Fallback ladder per the project spec: try service_tier="flex" first (50% cost,
variable latency); on error/timeout retry with service_tier="auto"; as a last
resort drop response_format and extract JSON from the raw text.
"""

import base64
import json
import logging
import re

from openai import OpenAI

from backend.app import config

log = logging.getLogger("dbms-el.caption")

_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set in .env")
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


CAPTION_SYSTEM = "You observe screenshots of the user's screen."

CAPTION_PROMPT = (
    'Return JSON only: {"caption": "1-2 sentences: what is the user viewing/doing '
    '(name concrete products, brands, prices)", "ocr_text": "key visible text, condensed", '
    '"topics": ["short", "lowercase", "tags"], "importance_hint": 0.0-1.0 '
    "(0=idle/generic, 1=clearly meaningful research or decision)}"
)

MEMORY_SYSTEM = (
    "You write terse, self-contained memories about one user for a chatbot's context."
)


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _create(messages: list[dict], *, json_mode: bool, tier: str, timeout: float, max_tokens: int = 600):
    # Newer OpenAI models (reasoning generation) require max_completion_tokens;
    # the legacy max_tokens name is rejected with a 400.
    kwargs: dict = {
        "model": config.OPENAI_MODEL,
        "messages": messages,
        "service_tier": tier,
        "timeout": timeout,
        "max_completion_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return client().chat.completions.create(**kwargs)


def _complete(messages: list[dict], *, json_mode: bool = False, max_tokens: int = 600,
              prefer_flex: bool = True) -> str:
    """flex-first (cheap, queued) or auto-first (interactive latency)."""
    attempts: list[dict] = []
    if prefer_flex:
        attempts.append({"tier": "flex", "json_mode": json_mode, "timeout": 120.0})
    attempts.append({"tier": "auto", "json_mode": json_mode, "timeout": 60.0})
    if json_mode:
        attempts.append({"tier": "auto", "json_mode": False, "timeout": 60.0})
    last_exc: Exception | None = None
    for attempt in attempts:
        try:
            resp = _create(messages, max_tokens=max_tokens, **attempt)
            log.debug("openai call served via tier=%s", attempt["tier"])
            return resp.choices[0].message.content or ""
        except Exception as exc:
            last_exc = exc
            log.warning("openai attempt failed (tier=%s): %s", attempt["tier"], exc)
    raise RuntimeError(f"All OpenAI attempts failed: {last_exc}")


def _prepare_image(image_bytes: bytes) -> tuple[bytes, str]:
    """Downscale to <=720p JPEG — vision tokens (and upload size) scale with
    pixel area, so this is the main API cost control."""
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    scale = min(1280 / img.width, 720 / img.height, 1.0)
    if scale < 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


def caption_image(image_bytes: bytes, mime: str = "image/png") -> dict:
    """Screenshot -> {caption, ocr_text, topics, importance_hint}."""
    try:
        jpeg, mime = _prepare_image(image_bytes)
    except Exception:
        jpeg, mime = image_bytes, mime  # send original if PIL fails
    b64 = base64.b64encode(jpeg).decode()
    content = [
        {"type": "text", "text": CAPTION_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]
    messages = [
        {"role": "system", "content": CAPTION_SYSTEM},
        {"role": "user", "content": content},
    ]
    data = _extract_json(_complete(messages, json_mode=True, max_tokens=500))
    return {
        "caption": str(data.get("caption", "")).strip(),
        "ocr_text": str(data.get("ocr_text", "")).strip(),
        "topics": [str(t).lower() for t in data.get("topics", [])][:5],
        "importance_hint": max(0.0, min(1.0, float(data.get("importance_hint", 0.3)))),
    }


def summarize_observations(captions: list[str], *, prefer_flex: bool = True) -> str:
    """Cluster captions -> one concise memory (1-3 sentences)."""
    listed = "\n".join(f"- {c}" for c in captions)
    user = (
        "Recent observations of the user's activity:\n"
        f"{listed}\n\n"
        "Write 1-3 concise sentences summarizing the stable, useful facts about "
        "what the user is interested in or doing (products compared, preferences, "
        "budget, decisions). Output only the memory text, no preamble."
    )
    return _complete(
        [{"role": "system", "content": MEMORY_SYSTEM}, {"role": "user", "content": user}],
        max_tokens=220,
        prefer_flex=prefer_flex,
    ).strip()


def merge_memory(existing: str, captions: list[str], *, prefer_flex: bool = True) -> str:
    """Existing memory + new evidence -> updated memory text."""
    listed = "\n".join(f"- {c}" for c in captions)
    user = (
        f"Existing memory:\n{existing}\n\nNew observations:\n{listed}\n\n"
        "Rewrite the memory as 1-3 concise sentences that merge the new "
        "information. Output only the memory text, no preamble."
    )
    return _complete(
        [{"role": "system", "content": MEMORY_SYSTEM}, {"role": "user", "content": user}],
        max_tokens=220,
        prefer_flex=prefer_flex,
    ).strip()
