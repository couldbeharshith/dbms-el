"""Observations -> Memories.

consolidate: cluster recent screenshot observations (ml.Clusterer); each cluster
is merged into an existing memory (matched by shared evidence first, then by
centroid similarity) or, if the ImportanceScorer promotes it, summarized into a
new memory. Evidence links go in memory_observations.

Deliberately simple, explainable scoring: see config for the thresholds and
backend/app/ml for the swappable components.
"""

import asyncio
import logging
import uuid

from backend.app import caption, config, db, lifecycle, ml, observations
from backend.app.embed import service as embed_service
from backend.app.ml import habits
from backend.app.ml.vectors import centroid, cosine, parse_vec

log = logging.getLogger("dbms-el.consolidation")


async def consolidate(user_id: uuid.UUID, *, fast: bool = False) -> dict:
    """fast=True skips the flex tier on LLM calls (manual button: interactive
    latency matters). The background loop uses fast=False for the flex discount.

    Token discipline: LLM calls happen only when a cluster carries genuinely
    NEW evidence for a memory. Unchanged clusters cost zero API calls."""
    stats = {"backfilled_urls": 0, "deduped_memories": 0, "clusters": 0,
             "memories_created": 0, "memories_merged": 0, "clusters_unchanged": 0,
             "clusters_pending": 0, "evidence_links": 0, "habits": 0, "archived": 0}

    scorer = ml.get_importance_scorer()
    stats["backfilled_urls"] = observations.backfill_urls(user_id)
    stats["deduped_memories"] = _dedupe_memories(user_id)

    for members in _find_clusters(user_id):
        stats["clusters"] += 1
        center = centroid([m["vec"] for m in members])
        if config.HABITS:
            await _update_habit(user_id, members, stats)

        # Existing memory covering this cluster? First by evidence: the memory
        # already linked to most of these observations (stable across passes).
        existing = db.query_one(
            "SELECT m.id, m.content, m.importance FROM memories m "
            "JOIN memory_observations mo ON mo.memory_id = m.id "
            "WHERE m.user_id = %s AND m.source <> 'habit' AND mo.observation_id = ANY(%s::uuid[]) "
            "GROUP BY m.id ORDER BY count(*) DESC, m.created_at LIMIT 1",
            [user_id, [str(m["id"]) for m in members]],
        )
        if existing is None:  # else by meaning (centroid vs memory embedding)
            existing = db.query_one(
                "SELECT id, content, importance FROM memories "
                "WHERE user_id = %s AND source <> 'habit' AND embedding <=> %s::vector <= %s "
                "ORDER BY embedding <=> %s::vector LIMIT 1",
                [user_id, db.vec_lit(center), 1 - config.MEMORY_MERGE_SIMILARITY,
                 db.vec_lit(center)],
            )

        if existing:
            await _merge_into_existing(existing, members, scorer, fast, stats)
            continue

        assessment = scorer.assess_new(user_id, members)
        if not assessment.promote:
            stats["clusters_pending"] += 1
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
            "SELECT id FROM memories WHERE user_id = %s AND source <> 'habit' AND embedding <=> %s::vector <= %s "
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

        row = db.query_one(
            "INSERT INTO memories (user_id, content, embedding, importance, source) "
            "VALUES (%s, %s, %s::vector, %s, 'auto') RETURNING id",
            [user_id, content, db.vec_lit(vec), assessment.importance],
        )
        _link_evidence(row["id"], members)
        stats["evidence_links"] += len(members)
        stats["memories_created"] += 1
        log.info("Memory created: %s", content[:80])

    if config.DECAY != "off":
        stats["archived"] = lifecycle.archive_stale_memories(user_id)
    return stats


async def _update_habit(user_id: uuid.UUID, members: list[dict], stats: dict) -> None:
    habit = habits.detect_habit([m["observed_at"].astimezone() for m in members])
    if habit is None:
        return
    content = habits.describe_habit(habits.dominant_label(members), habit)
    importance = 0.5 + 0.3 * habit["concentration"]
    existing = db.query_one(
        "SELECT m.id, m.content FROM memories m JOIN memory_observations mo ON mo.memory_id = m.id "
        "WHERE m.user_id = %s AND m.source = 'habit' AND mo.observation_id = ANY(%s::uuid[]) "
        "GROUP BY m.id ORDER BY count(*) DESC LIMIT 1",
        [user_id, [str(m["id"]) for m in members]],
    )
    if existing and existing["content"] == content:
        _link_evidence(existing["id"], members)
        return
    vec = db.vec_lit(await embed_service.embed_document(content))
    if existing:
        db.execute("UPDATE memories SET content = %s, embedding = %s::vector, importance = %s, "
                   "archived_at = NULL WHERE id = %s", [content, vec, importance, existing["id"]])
        memory_id = existing["id"]
    else:
        memory_id = db.query_one(
            "INSERT INTO memories (user_id, content, embedding, importance, source) "
            "VALUES (%s, %s, %s::vector, %s, 'habit') RETURNING id",
            [user_id, content, vec, importance])["id"]
    _link_evidence(memory_id, members)
    stats["habits"] += 1
    log.info("Habit memory: %s", content)


async def _merge_into_existing(existing: dict, members: list[dict], scorer, fast: bool,
                               stats: dict) -> None:
    # LLM-merge only when some evidence is not already linked: recurring
    # identical activity costs zero API calls.
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
        return
    try:
        content = await asyncio.to_thread(
            caption.merge_memory, existing["content"],
            [m["caption"] or "" for m in new_members],
            prefer_flex=not fast)
        vec = await embed_service.embed_document(content)
    except Exception:
        log.exception("Memory merge failed — keeping existing content")
        return
    db.execute(
        "UPDATE memories SET content = %s, embedding = %s::vector, "
        "importance = %s, archived_at = NULL WHERE id = %s",
        [content, db.vec_lit(vec),
         scorer.after_merge(existing["importance"], members, new_members), existing["id"]],
    )
    stats["memories_merged"] += 1
    log.info("Memory merged: %s", content[:80])


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
        "FROM memories WHERE user_id = %s AND source <> 'habit' ORDER BY created_at",
        [user_id],
    )
    mems = [{**r, "vec": parse_vec(r["embedding"])} for r in rows]

    removed = 0
    keepers: list[dict] = []
    for m in mems:  # oldest first
        target = next(
            (k for k in keepers if cosine(k["vec"], m["vec"]) >= config.MEMORY_DUPLICATE_SIMILARITY),
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


def _find_clusters(user_id: uuid.UUID) -> list[list[dict]]:
    clusterer = ml.get_clusterer()
    rows = db.query(
        """
        SELECT id, caption, topics, observed_at, importance_hint, embedding::text AS embedding
        FROM v_observations
        WHERE user_id = %s AND observed_at > now() - make_interval(hours => %s)
              AND embedding IS NOT NULL
        ORDER BY observed_at DESC
        LIMIT %s
        """,
        [user_id, clusterer.window_hours, clusterer.max_observations],
    )
    obs = [{**r, "vec": parse_vec(r["embedding"])} for r in rows]
    return clusterer.cluster(obs)
