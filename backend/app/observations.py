"""Screenshot -> Observation.

process_screenshot: decrypt pixels -> gpt-6-luna caption/OCR -> embed caption+OCR
(text) and the raw image (image_embedding) -> insert observations row. When the
screenshot was taken with a browser in the foreground, the URL of the page the
user was on is attached (display-only; helps the user browse their observations).
"""

import asyncio
import io
import logging
import uuid
from datetime import datetime

from psycopg.errors import ForeignKeyViolation
from PIL import Image

from backend.app import caption, config, crypto, db
from backend.app.embed import service as embed_service

log = logging.getLogger("dbms-el.observations")


def is_browser_title(title: str | None) -> bool:
    if not title:
        return False
    tail = title.rsplit("-", 1)[-1].strip().lower()
    return tail in config.BROWSER_TITLE_TAILS


def recent_event_id(user_id, at: datetime) -> int | None:
    """Browsing event of the page the user was on at `at` (browser events
    within 15 min before the capture; the extension flushes dwell in 30s
    batches)."""
    row = db.query_one(
        "SELECT id FROM browsing_events "
        "WHERE user_id = %s AND visited_at <= %s "
        "AND visited_at > %s - interval '15 minutes' "
        "ORDER BY visited_at DESC LIMIT 1",
        [user_id, at, at],
    )
    return row["id"] if row else None


def backfill_urls(user_id: uuid.UUID) -> int:
    """Link screenshots to the page that was on screen when the link was missed
    at processing time (the covering browsing_events row arrives up to 30s later)."""
    rows = db.query(
        "SELECT id, captured_at, source_app FROM screenshots "
        "WHERE user_id = %s AND browsing_event_id IS NULL "
        "AND captured_at > now() - interval '24 hours'",
        [user_id],
    )
    filled = 0
    for r in rows:
        if not is_browser_title(r["source_app"]):
            continue
        event_id = recent_event_id(user_id, r["captured_at"])
        if event_id:
            db.execute("UPDATE screenshots SET browsing_event_id = %s WHERE id = %s",
                       [event_id, r["id"]])
            filled += 1
    return filled


async def process_screenshot(screenshot_id: uuid.UUID) -> None:
    row = db.query_one("SELECT * FROM screenshots WHERE id = %s", [screenshot_id])
    if row is None:
        return
    existing = db.query_one(
        "SELECT 1 FROM observations WHERE screenshot_id = %s", [screenshot_id]
    )
    if existing:
        return

    image_path = config.DATA_DIR / row["file_path"]
    try:
        image_bytes = crypto.decrypt(image_path.read_bytes())
    except Exception:
        log.exception("Failed to decrypt screenshot %s", screenshot_id)
        return

    # Captioning (OpenAI gpt-6-luna) and local embedding both run in threads.
    try:
        result = await asyncio.to_thread(caption.caption_image, image_bytes)
    except Exception:
        log.exception("Captioning failed for %s — screenshot kept, will not retry", screenshot_id)
        return

    try:
        pil = Image.open(io.BytesIO(image_bytes))
        image_vec = await embed_service.embed_image(pil)
        doc_text = result["caption"]
        if result["ocr_text"]:
            doc_text += "\n\nVisible text: " + result["ocr_text"][:2000]
        text_vec = await embed_service.embed_document(doc_text)
    except Exception:
        log.exception("Embedding failed for %s", screenshot_id)
        return

    # Display-only provenance: which page was on screen (browser in foreground).
    if is_browser_title(row["source_app"]):
        event_id = recent_event_id(row["user_id"], row["captured_at"])
        if event_id:
            db.execute("UPDATE screenshots SET browsing_event_id = %s WHERE id = %s",
                       [event_id, screenshot_id])

    try:
        obs = db.query_one(
            """
            INSERT INTO observations
                (user_id, screenshot_id, caption, ocr_encrypted, importance_hint,
                 observed_at, embedding, image_embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s::vector)
            ON CONFLICT (screenshot_id) WHERE screenshot_id IS NOT NULL DO NOTHING
            RETURNING id
            """,
            [
                row["user_id"],
                screenshot_id,
                result["caption"],
                crypto.encrypt(result["ocr_text"].encode()) if result["ocr_text"] else None,
                result["importance_hint"],
                row["captured_at"],
                db.vec_lit(text_vec),
                db.vec_lit(image_vec),
            ],
        )
        if obs is None:  # another task already processed this screenshot
            return
        topics = sorted({t.strip() for t in result["topics"] if t and t.strip()})
        if topics:
            db.executemany(
                "INSERT INTO observation_topics (observation_id, topic) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                [[obs["id"], t] for t in topics],
            )
    except ForeignKeyViolation:
        # The screenshot was deleted (History / data controls) while this task
        # was captioning — discarding the observation is the correct outcome.
        log.info("Screenshot %s was deleted during processing — observation discarded",
                 screenshot_id)
        return
    log.info("Observation created for screenshot %s: %s", screenshot_id, result["caption"][:80])
