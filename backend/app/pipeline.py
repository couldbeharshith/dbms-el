"""Screenshot -> Observation -> Memory pipeline.

process_screenshot: decrypt pixels -> gpt-6-luna caption/OCR -> embed caption+OCR
(text) and the raw image (image_embedding) -> insert observations row.

consolidate: (1) turn significant, not-yet-observed browsing events into
observations; (2) greedy cosine clustering of the last 24h of observations;
clusters of >= CLUSTER_MIN_OBSERVATIONS similar observations become a memory
(new, or merged into an existing >= MEMORY_MERGE_SIMILARITY memory) with
evidence links in memory_observations.

Deliberately simple, explainable scoring — see config for the thresholds.
"""

import asyncio
import io
import logging
import uuid
from datetime import datetime, timezone

import numpy as np
from PIL import Image

from backend.app import caption, config, crypto, db
from backend.app.embed import service as embed_service

log = logging.getLogger("dbms-el.pipeline")


# ---------------------------------------------------------------------------
# Screenshot -> Observation
# ---------------------------------------------------------------------------
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

    db.execute(
        """
        INSERT INTO observations
            (user_id, screenshot_id, kind, caption, ocr_encrypted, topics,
             observed_at, embedding, image_embedding, metadata)
        VALUES (%s, %s, 'screenshot', %s, %s, %s, %s, %s::vector, %s::vector, %s::jsonb)
        """,
        [
            row["user_id"],
            screenshot_id,
            result["caption"],
            crypto.encrypt(result["ocr_text"].encode()) if result["ocr_text"] else None,
            result["topics"],
            row["captured_at"],
            db.vec_lit(text_vec),
            db.vec_lit(image_vec),
            db._jsonb({"importance_hint": result["importance_hint"], "source_app": row["source_app"]}),
        ],
    )
    log.info("Observation created for screenshot %s: %s", screenshot_id, result["caption"][:80])


# ---------------------------------------------------------------------------
# Memory consolidation
# ---------------------------------------------------------------------------
async def consolidate(user_id: uuid.UUID, *, fast: bool = False) -> dict:
    """fast=True skips the flex tier on LLM calls (manual button: interactive
    latency matters). The background loop uses fast=False for the flex discount."""
    stats = {"browsing_observations": 0, "clusters": 0, "memories_created": 0,
             "memories_merged": 0, "evidence_links": 0}

    stats["browsing_observations"] = await _create_browsing_observations(user_id)
    clusters = _find_clusters(user_id)

    for members in clusters:
        stats["clusters"] += 1
        captions = [m["caption"] or "" for m in members]
        centroid = _centroid([m["vec"] for m in members])

        # Existing similar memory? (cosine distance <= 1 - MEMORY_MERGE_SIMILARITY)
        existing = db.query_one(
            "SELECT id, content, importance FROM memories "
            "WHERE user_id = %s AND embedding <=> %s::vector <= %s "
            "ORDER BY embedding <=> %s::vector LIMIT 1",
            [user_id, db.vec_lit(centroid), 1 - config.MEMORY_MERGE_SIMILARITY,
             db.vec_lit(centroid)],
        )

        try:
            if existing:
                content = await asyncio.to_thread(
                    caption.merge_memory, existing["content"], captions,
                    prefer_flex=not fast)
                importance = min(existing["importance"] + 0.05, 0.95)
                vec = await embed_service.embed_document(content)
                db.execute(
                    "UPDATE memories SET content = %s, embedding = %s::vector, "
                    "importance = %s, metadata = %s::jsonb WHERE id = %s",
                    [content, db.vec_lit(vec), importance,
                     db._jsonb({"topics": _union_topics(members), "source": "auto"}),
                     existing["id"]],
                )
                memory_id = existing["id"]
                stats["memories_merged"] += 1
            else:
                content = await asyncio.to_thread(
                    caption.summarize_observations, captions, prefer_flex=not fast)
                importance = min(
                    0.3 + 0.08 * len(members) + 0.2 * _recency_bonus(members), 0.95
                )
                vec = await embed_service.embed_document(content)
                row = db.query_one(
                    "INSERT INTO memories (user_id, content, embedding, importance, metadata) "
                    "VALUES (%s, %s, %s::vector, %s, %s::jsonb) RETURNING id",
                    [user_id, content, db.vec_lit(vec), importance,
                     db._jsonb({"topics": _union_topics(members), "source": "auto"})],
                )
                memory_id = row["id"]
                stats["memories_created"] += 1
        except Exception:
            log.exception("Memory creation failed for a cluster — skipping cluster")
            continue

        for m in members:
            db.execute(
                "INSERT INTO memory_observations (memory_id, observation_id) "
                "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                [memory_id, m["id"]],
            )
            stats["evidence_links"] += 1
        log.info("Memory %s: %s", "merged" if existing else "created", content[:80])

    return stats


async def _create_browsing_observations(user_id: uuid.UUID) -> int:
    """Significant (>= BROWSING_EVENT_MIN_SECONDS) events from the last 24h that
    have no observation yet become one 'browsing' observation each (max 10/run)."""
    rows = db.query(
        """
        SELECT b.id, b.domain, b.title, b.url, b.time_spent_sec, b.visited_at
        FROM browsing_events b
        LEFT JOIN observations o ON o.browsing_event_id = b.id
        WHERE b.user_id = %s AND o.id IS NULL
          AND b.visited_at > now() - interval '24 hours'
          AND b.time_spent_sec >= %s
        ORDER BY b.time_spent_sec DESC
        LIMIT 10
        """,
        [user_id, config.BROWSING_EVENT_MIN_SECONDS],
    )
    created = 0
    for b in rows:
        text = (
            f"User spent {b['time_spent_sec']}s on {b['domain']}"
            + (f" — {b['title']}" if b["title"] else "")
        )
        try:
            vec = await embed_service.embed_document(text)
        except Exception:
            log.exception("Could not embed browsing event %s", b["id"])
            continue
        db.execute(
            """
            INSERT INTO observations
                (user_id, browsing_event_id, kind, caption, topics, observed_at,
                 embedding, metadata)
            VALUES (%s, %s, 'browsing', %s, %s, %s, %s::vector, %s::jsonb)
            """,
            [user_id, b["id"], text, [b["domain"]], b["visited_at"], db.vec_lit(vec),
             db._jsonb({"time_spent_sec": b["time_spent_sec"], "url": b["url"]})],
        )
        created += 1
    return created


def _find_clusters(user_id: uuid.UUID) -> list[list[dict]]:
    rows = db.query(
        """
        SELECT id, caption, topics, observed_at, embedding::text AS embedding
        FROM observations
        WHERE user_id = %s AND observed_at > now() - interval '24 hours'
              AND embedding IS NOT NULL
        ORDER BY observed_at DESC
        LIMIT 300
        """,
        [user_id],
    )
    obs = []
    for r in rows:
        vec = np.array([float(x) for x in r["embedding"].strip("[]").split(",")], dtype=np.float32)
        obs.append({**r, "vec": vec})

    unassigned = list(obs)
    clusters: list[list[dict]] = []
    while unassigned:
        seed = unassigned.pop(0)
        members = [seed]
        rest = []
        for o in unassigned:
            if _cosine(seed["vec"], o["vec"]) >= config.CLUSTER_SIMILARITY_THRESHOLD:
                members.append(o)
            else:
                rest.append(o)
        unassigned = rest
        if len(members) >= config.CLUSTER_MIN_OBSERVATIONS:
            clusters.append(members)
    return clusters


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _centroid(vecs: list[np.ndarray]) -> list[float]:
    c = np.mean(np.stack(vecs), axis=0)
    c = c / (np.linalg.norm(c) or 1.0)
    return c.tolist()


def _recency_bonus(members: list[dict]) -> float:
    now = datetime.now(timezone.utc)
    ages_h = [(now - m["observed_at"]).total_seconds() / 3600 for m in members]
    return float(np.mean([np.exp(-a / 48.0) for a in ages_h]))


def _union_topics(members: list[dict]) -> list[str]:
    seen: list[str] = []
    for m in members:
        for t in m.get("topics") or []:
            if t not in seen:
                seen.append(t)
    return seen[:5]
