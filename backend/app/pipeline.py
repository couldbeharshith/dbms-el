"""Screenshot -> Observation -> Memory pipeline.

process_screenshot: decrypt pixels -> gpt-6-luna caption/OCR -> embed caption+OCR
(text) and the raw image (image_embedding) -> insert observations row. When the
screenshot was taken with a browser in the foreground, the URL of the page the
user was on is attached (display-only; helps the user browse their observations).

consolidate: greedy cosine clustering of the last 24h of screenshot
observations; clusters of >= CLUSTER_MIN_OBSERVATIONS similar observations
become a memory (new, or merged into an existing >= MEMORY_MERGE_SIMILARITY
memory) with evidence links in memory_observations. Also backfills missing
screenshot URLs now that the extension's event batches have all arrived.

Deliberately simple, explainable scoring — see config for the thresholds.
"""

import asyncio
import io
import logging
import uuid
from datetime import datetime, timezone

import numpy as np
from psycopg.errors import ForeignKeyViolation
from PIL import Image

from backend.app import caption, config, crypto, db
from backend.app.embed import service as embed_service

log = logging.getLogger("dbms-el.pipeline")


def _is_browser_title(title: str | None) -> bool:
    if not title:
        return False
    tail = title.rsplit("-", 1)[-1].strip().lower()
    return tail in config.BROWSER_TITLE_TAILS


def _recent_event_url(user_id, at: datetime) -> str | None:
    """URL of the page the user was on at `at` (browser events within 15 min
    before the capture; the extension flushes dwell in 30s batches)."""
    row = db.query_one(
        "SELECT url FROM browsing_events "
        "WHERE user_id = %s AND visited_at <= %s "
        "AND visited_at > %s - interval '15 minutes' "
        "ORDER BY visited_at DESC LIMIT 1",
        [user_id, at, at],
    )
    return row["url"] if row else None


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

    # Display-only provenance: which page was on screen (browser in foreground).
    url = None
    if _is_browser_title(row["source_app"]):
        url = _recent_event_url(row["user_id"], row["captured_at"])

    try:
        db.execute(
            """
            INSERT INTO observations
                (user_id, screenshot_id, kind, caption, ocr_encrypted, topics,
                 observed_at, embedding, image_embedding, url, metadata)
            VALUES (%s, %s, 'screenshot', %s, %s, %s, %s, %s::vector, %s::vector, %s, %s::jsonb)
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
                url,
                db._jsonb({"importance_hint": result["importance_hint"], "source_app": row["source_app"]}),
            ],
        )
    except ForeignKeyViolation:
        # The screenshot was deleted (History / data controls) while this task
        # was captioning — discarding the observation is the correct outcome.
        log.info("Screenshot %s was deleted during processing — observation discarded",
                 screenshot_id)
        return
    log.info("Observation created for screenshot %s: %s", screenshot_id, result["caption"][:80])


# ---------------------------------------------------------------------------
# Memory consolidation
# ---------------------------------------------------------------------------
async def consolidate(user_id: uuid.UUID, *, fast: bool = False) -> dict:
    """fast=True skips the flex tier on LLM calls (manual button: interactive
    latency matters). The background loop uses fast=False for the flex discount.

    Token discipline: LLM calls happen only when a cluster carries genuinely
    NEW evidence for a memory. Unchanged clusters cost zero API calls."""
    stats = {"backfilled_urls": 0, "deduped_memories": 0, "clusters": 0,
             "memories_created": 0, "memories_merged": 0, "clusters_unchanged": 0,
             "evidence_links": 0}

    stats["backfilled_urls"] = _backfill_urls(user_id)
    stats["deduped_memories"] = _dedupe_memories(user_id)
    clusters = _find_clusters(user_id)

    for members in clusters:
        stats["clusters"] += 1
        centroid = _centroid([m["vec"] for m in members])

        # Existing memory covering this cluster? (centroid pre-filter)
        existing = db.query_one(
            "SELECT id, content, importance FROM memories "
            "WHERE user_id = %s AND embedding <=> %s::vector <= %s "
            "ORDER BY embedding <=> %s::vector LIMIT 1",
            [user_id, db.vec_lit(centroid), 1 - config.MEMORY_MERGE_SIMILARITY,
             db.vec_lit(centroid)],
        )

        if existing:
            _link_evidence(existing["id"], members)
            stats["evidence_links"] += len(members)

            # LLM-merge only when some evidence is not already linked —
            # recurring identical activity costs zero API calls.
            already = {
                r["observation_id"]
                for r in db.query(
                    "SELECT observation_id FROM memory_observations WHERE memory_id = %s",
                    [existing["id"]])
            }
            new_members = [m for m in members if m["id"] not in already]
            if not new_members:
                stats["clusters_unchanged"] += 1
                continue
            try:
                content = await asyncio.to_thread(
                    caption.merge_memory, existing["content"],
                    [m["caption"] or "" for m in new_members],
                    prefer_flex=not fast)
                vec = await embed_service.embed_document(content)
            except Exception:
                log.exception("Memory merge failed — keeping existing content")
                continue
            db.execute(
                "UPDATE memories SET content = %s, embedding = %s::vector, "
                "importance = %s WHERE id = %s",
                [content, db.vec_lit(vec),
                 min(existing["importance"] + 0.05, 0.95), existing["id"]],
            )
            stats["memories_merged"] += 1
            log.info("Memory merged: %s", content[:80])
            continue

        # No existing memory covers the cluster: summarize, then guard against
        # inserting a near-duplicate of another memory (memory-to-memory space).
        try:
            content = await asyncio.to_thread(
                caption.summarize_observations,
                [m["caption"] or "" for m in members], prefer_flex=not fast)
            vec = await embed_service.embed_document(content)
        except Exception:
            log.exception("Memory creation failed for a cluster — skipping cluster")
            continue

        duplicate = db.query_one(
            "SELECT id FROM memories WHERE user_id = %s AND embedding <=> %s::vector <= %s "
            "ORDER BY embedding <=> %s::vector LIMIT 1",
            [user_id, db.vec_lit(vec), 1 - config.MEMORY_DUPLICATE_SIMILARITY,
             db.vec_lit(vec)],
        )
        if duplicate:
            _link_evidence(duplicate["id"], members)
            stats["evidence_links"] += len(members)
            stats["memories_merged"] += 1
            log.info("Cluster folded into existing memory %s (duplicate candidate)",
                     str(duplicate["id"])[:8])
            continue

        importance = min(0.3 + 0.08 * len(members) + 0.2 * _recency_bonus(members), 0.95)
        row = db.query_one(
            "INSERT INTO memories (user_id, content, embedding, importance, metadata) "
            "VALUES (%s, %s, %s::vector, %s, %s::jsonb) RETURNING id",
            [user_id, content, db.vec_lit(vec), importance,
             db._jsonb({"topics": _union_topics(members), "source": "auto"})],
        )
        _link_evidence(row["id"], members)
        stats["evidence_links"] += len(members)
        stats["memories_created"] += 1
        log.info("Memory created: %s", content[:80])

    return stats


def _link_evidence(memory_id: uuid.UUID, members: list[dict]) -> None:
    for m in members:
        db.execute(
            "INSERT INTO memory_observations (memory_id, observation_id) "
            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [memory_id, m["id"]],
        )


def _dedupe_memories(user_id: uuid.UUID) -> int:
    """Fold near-identical memories (>= MEMORY_DUPLICATE_SIMILARITY) into the
    oldest one: evidence links move, importance/hit_count carry over. Pure
    vectors + SQL — zero LLM cost."""
    rows = db.query(
        "SELECT id, content, importance, hit_count, created_at, embedding::text AS embedding "
        "FROM memories WHERE user_id = %s ORDER BY created_at",
        [user_id],
    )
    mems = []
    for r in rows:
        vec = np.array([float(x) for x in r["embedding"].strip("[]").split(",")], dtype=np.float32)
        mems.append({**r, "vec": vec})

    removed = 0
    keepers: list[dict] = []
    for m in mems:  # oldest first
        target = next(
            (k for k in keepers if _cosine(k["vec"], m["vec"]) >= config.MEMORY_DUPLICATE_SIMILARITY),
            None,
        )
        if target is None:
            keepers.append(m)
            continue
        db.execute(
            "INSERT INTO memory_observations (memory_id, observation_id, contribution) "
            "SELECT %s, observation_id, contribution FROM memory_observations "
            "WHERE memory_id = %s ON CONFLICT DO NOTHING",
            [target["id"], m["id"]],
        )
        db.execute(
            "UPDATE memories SET importance = greatest(importance, %s), "
            "hit_count = hit_count + %s WHERE id = %s",
            [m["importance"], m["hit_count"], target["id"]],
        )
        db.execute("DELETE FROM memories WHERE id = %s", [m["id"]])
        removed += 1
        log.info("Deduped memory %s into %s", str(m["id"])[:8], str(target["id"])[:8])
    return removed


def _backfill_urls(user_id: uuid.UUID) -> int:
    """Attach URLs to screenshot observations that missed theirs at processing
    time (the covering browsing_events row arrives up to 30s later)."""
    rows = db.query(
        "SELECT id, observed_at, metadata->>'source_app' AS source_app "
        "FROM observations "
        "WHERE user_id = %s AND kind = 'screenshot' AND url IS NULL "
        "AND observed_at > now() - interval '24 hours'",
        [user_id],
    )
    filled = 0
    for r in rows:
        if not _is_browser_title(r["source_app"]):
            continue
        url = _recent_event_url(user_id, r["observed_at"])
        if url:
            db.execute("UPDATE observations SET url = %s WHERE id = %s", [url, r["id"]])
            filled += 1
    return filled


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
