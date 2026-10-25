"""Screenshots uploaded by the Electron app (pixels encrypted at rest)."""

import asyncio
import base64
import datetime as dt
import hashlib
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from backend.app import config, crypto, db, pipeline
from backend.app.phash import distance, fingerprint

router = APIRouter(prefix="/api", tags=["screenshots"])

MAX_IMAGE_BYTES = 12 * 1024 * 1024


class ScreenshotIn(BaseModel):
    image_b64: str
    width: int | None = None
    height: int | None = None
    source_app: str | None = None
    captured_at: dt.datetime | None = None
    metadata: dict = {}


@router.post("/screenshots")
async def upload_screenshot(payload: ScreenshotIn, request: Request):
    user = request.app.state.user
    if not user["settings"].get("screenshots_enabled", True):
        return {"accepted": False, "screenshots_enabled": False}

    image_bytes = base64.b64decode(payload.image_b64)
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Screenshot too large")

    # Duplicate suppression, two layers:
    # 1) exact SHA-256, global — a byte-identical capture was ever stored;
    # 2) perceptual hash (dHash), windowed — looks identical to a RECENT
    #    capture (cursor blinks, clock ticks). Windowed so a genuinely new
    #    page on a similar layout still gets captured later.
    sha = hashlib.sha256(image_bytes).hexdigest()
    seen = db.query_one(
        "SELECT 1 FROM screenshots WHERE user_id = %s AND sha256 = %s LIMIT 1",
        [user["id"], sha],
    )
    if seen is not None:
        return {"accepted": True, "skipped_duplicate": True, "reason": "exact", "sha256": sha}

    ph = fingerprint(image_bytes)
    if ph:
        recent = db.query(
            "SELECT phash FROM screenshots WHERE user_id = %s "
            "AND captured_at > now() - make_interval(mins => %s) AND phash IS NOT NULL",
            [user["id"], config.PHASH_WINDOW_MINUTES],
        )
        if any(distance(ph, r["phash"]) <= config.PHASH_MAX_MAD for r in recent):
            return {"accepted": True, "skipped_duplicate": True, "reason": "perceptual"}

    sid = uuid.uuid4()
    rel_path = f"screenshots/{sid}.enc"
    (config.SCREENSHOT_DIR / f"{sid}.enc").write_bytes(crypto.encrypt(image_bytes))

    row = db.query_one(
        """
        INSERT INTO screenshots
            (id, user_id, captured_at, file_path, sha256, size_bytes, width, height,
             source_app, encrypted, phash, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s::jsonb)
        RETURNING id, captured_at, source_app
        """,
        [sid, user["id"], payload.captured_at or dt.datetime.now(dt.timezone.utc),
         rel_path, sha, len(image_bytes),
         payload.width, payload.height, payload.source_app, ph,
         db._jsonb(payload.metadata)],
    )

    # Caption + embed + observe happens asynchronously.
    _schedule_processing(request, sid)
    return {"accepted": True, "skipped_duplicate": False, **row}


log = logging.getLogger("dbms-el.screenshots")


def _task_done(task: asyncio.Task) -> None:
    # Calling task.exception() retrieves it — prevents asyncio's
    # "Task exception was never retrieved" noise; real failures log once here.
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning("screenshot processing task failed: %r", exc)


def _schedule_processing(request: Request, sid: uuid.UUID) -> None:
    task = asyncio.create_task(pipeline.process_screenshot(sid))
    request.app.state.pending_tasks.add(task)
    task.add_done_callback(request.app.state.pending_tasks.discard)
    task.add_done_callback(_task_done)


@router.post("/screenshots/reprocess")
async def reprocess_unprocessed(request: Request):
    """Caption + embed every screenshot that has no observation yet (e.g. rows
    left unprocessed by an older bug or by past API failures)."""
    user = request.app.state.user
    rows = db.query(
        "SELECT s.id FROM screenshots s WHERE s.user_id = %s AND NOT EXISTS "
        "(SELECT 1 FROM observations o WHERE o.screenshot_id = s.id)",
        [user["id"]],
    )
    for r in rows:
        _schedule_processing(request, r["id"])
    return {"scheduled": len(rows)}


@router.get("/screenshots")
async def list_screenshots(request: Request, limit: int = 60, hours: int | None = None):
    user = request.app.state.user
    conds = ["s.user_id = %s"]
    args: list = [user["id"]]
    if hours:
        conds.append("s.captured_at > now() - make_interval(hours => %s)")
        args.append(hours)
    args.append(min(limit, 500))
    rows = db.query(
        f"""
        SELECT s.id, s.captured_at, s.width, s.height, s.source_app, s.size_bytes,
               s.metadata,
               (SELECT o.caption FROM observations o
                 WHERE o.screenshot_id = s.id LIMIT 1) AS caption,
               (SELECT o.topics FROM observations o
                 WHERE o.screenshot_id = s.id LIMIT 1) AS topics,
               EXISTS (SELECT 1 FROM observations o WHERE o.screenshot_id = s.id) AS processed
        FROM screenshots s
        WHERE {' AND '.join(conds)}
        ORDER BY s.captured_at DESC
        LIMIT %s
        """,
        args,
    )
    return {"screenshots": rows}


@router.get("/screenshots/{sid}")
async def screenshot_detail(sid: uuid.UUID, request: Request):
    user = request.app.state.user
    row = db.query_one(
        """
        SELECT s.id, s.captured_at, s.width, s.height, s.source_app, s.size_bytes,
               s.encrypted, s.metadata,
               o.id AS observation_id, o.caption, o.topics, o.observed_at, o.url
        FROM screenshots s
        LEFT JOIN observations o ON o.screenshot_id = s.id
        WHERE s.id = %s AND s.user_id = %s
        """,
        [sid, user["id"]],
    )
    if row is None:
        raise HTTPException(404, "Screenshot not found")
    if row.get("observation_id"):
        ocr = db.query_one(
            "SELECT ocr_encrypted FROM observations WHERE id = %s", [row["observation_id"]]
        )
        if ocr and ocr["ocr_encrypted"]:
            try:
                row["ocr_text"] = crypto.decrypt(bytes(ocr["ocr_encrypted"])).decode(
                    "utf-8", errors="replace"
                )
            except Exception:
                row["ocr_text"] = None
    return row


@router.get("/screenshots/{sid}/image")
async def screenshot_image(sid: uuid.UUID, request: Request):
    user = request.app.state.user
    row = db.query_one(
        "SELECT file_path FROM screenshots WHERE id = %s AND user_id = %s",
        [sid, user["id"]],
    )
    if row is None:
        raise HTTPException(404, "Screenshot not found")
    image = crypto.decrypt((config.DATA_DIR / row["file_path"]).read_bytes())
    return Response(content=image, media_type="image/png")


@router.get("/screenshots/{sid}/similar")
async def similar_screenshots(sid: uuid.UUID, request: Request, limit: int = 6):
    """Cross-modal: nearest screenshots by image embedding."""
    user = request.app.state.user
    rows = db.query(
        """
        WITH src AS (
            SELECT image_embedding FROM observations
            WHERE screenshot_id = %s AND image_embedding IS NOT NULL LIMIT 1
        )
        SELECT s.id, s.captured_at, o.caption,
               1 - (o.image_embedding <=> (SELECT image_embedding FROM src)) AS sim
        FROM observations o
        JOIN screenshots s ON s.id = o.screenshot_id
        WHERE s.user_id = %s AND o.image_embedding IS NOT NULL
              AND s.id != %s
              AND (SELECT image_embedding FROM src) IS NOT NULL
        ORDER BY o.image_embedding <=> (SELECT image_embedding FROM src)
        LIMIT %s
        """,
        [sid, user["id"], sid, min(limit, 20)],
    )
    for r in rows:
        r["sim"] = round(float(r["sim"]), 4)
    return {"similar": rows}


@router.delete("/screenshots/{sid}")
async def delete_screenshot(sid: uuid.UUID, request: Request):
    user = request.app.state.user
    row = db.query_one(
        "DELETE FROM screenshots WHERE id = %s AND user_id = %s RETURNING file_path",
        [sid, user["id"]],
    )
    if row is None:
        raise HTTPException(404, "Screenshot not found")
    try:
        (config.DATA_DIR / row["file_path"]).unlink(missing_ok=True)
    except OSError:
        pass
    return {"deleted": str(sid)}
