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


def _recent_event_id(user_id, at: datetime) -> int | None:
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
    if _is_browser_title(row["source_app"]):
        event_id = _recent_event_id(row["user_id"], row["captured_at"])
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

        # Existing memory covering this cluster? First by evidence: the memory
        # already linked to most of these observations (stable across passes).
        existing = db.query_one(
            "SELECT m.id, m.content, m.importance FROM memories m "
            "JOIN memory_observations mo ON mo.memory_id = m.id "
            "WHERE m.user_id = %s AND mo.observation_id = ANY(%s::uuid[]) "
            "GROUP BY m.id ORDER BY count(*) DESC, m.created_at LIMIT 1",
            [user_id, [str(m["id"]) for m in members]],
        )
        if existing is None:  # else by meaning (centroid vs memory embedding)
            existing = db.query_one(
                "SELECT id, content, importance FROM memories "
                "WHERE user_id = %s AND embedding <=> %s::vector <= %s "
                "ORDER BY embedding <=> %s::vector LIMIT 1",
                [user_id, db.vec_lit(centroid), 1 - config.MEMORY_MERGE_SIMILARITY,
                 db.vec_lit(centroid)],
            )

        if existing:
            # LLM-merge only when some evidence is not already linked —
            # recurring identical activity costs zero API calls.
            already = {
                r["observation_id"]
                for r in db.query(
                    "SELECT observation_id FROM memory_observations WHERE memory_id = %s",
                    [existing["id"]])
            }
            new_members = [m for m in members if m["id"] not in already]
            _link_evidence(existing["id"], new_members)
            stats["evidence_links"] += len(new_members)
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
            "INSERT INTO memories (user_id, content, embedding, importance, source) "
            "VALUES (%s, %s, %s::vector, %s, 'auto') RETURNING id",
            [user_id, content, db.vec_lit(vec), importance],
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
        "SELECT id, content, importance, created_at, embedding::text AS embedding "
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
            "UPDATE memories SET importance = greatest(importance, %s) WHERE id = %s",
            [m["importance"], target["id"]],
        )
        # Carry retrieval history over so the keeper's derived hit count stays right.
        db.execute(
            "INSERT INTO query_memories (query_id, memory_id, rank, similarity) "
            "SELECT query_id, %s, rank, similarity FROM query_memories WHERE memory_id = %s "
            "ON CONFLICT DO NOTHING",
            [target["id"], m["id"]],
        )
        db.execute("DELETE FROM memories WHERE id = %s", [m["id"]])
        removed += 1
        log.info("Deduped memory %s into %s", str(m["id"])[:8], str(target["id"])[:8])
    return removed


def _backfill_urls(user_id: uuid.UUID) -> int:
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
        if not _is_browser_title(r["source_app"]):
            continue
        event_id = _recent_event_id(user_id, r["captured_at"])
        if event_id:
            db.execute("UPDATE screenshots SET browsing_event_id = %s WHERE id = %s",
                       [event_id, r["id"]])
            filled += 1
    return filled


def _find_clusters(user_id: uuid.UUID) -> list[list[dict]]:
    rows = db.query(
        """
        SELECT id, caption, topics, observed_at, embedding::text AS embedding
        FROM v_observations
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

    # Average-linkage: each observation joins the cluster whose running centroid
    # it is closest to (if >= threshold), else starts a new cluster. Oldest
    # first so cluster membership is stable across passes.
    groups: list[dict] = []  # {"members": [...], "sum": vector}
    for o in reversed(obs):
        best, best_sim = None, config.CLUSTER_SIMILARITY_THRESHOLD
        for g in groups:
            sim = _cosine(g["sum"], o["vec"])
            if sim >= best_sim:
                best, best_sim = g, sim
        if best is None:
            groups.append({"members": [o], "sum": o["vec"].copy()})
        else:
            best["members"].append(o)
            best["sum"] = best["sum"] + o["vec"]
    return [g["members"] for g in groups
            if len(g["members"]) >= config.CLUSTER_MIN_OBSERVATIONS]


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

