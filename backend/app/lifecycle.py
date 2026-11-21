"""Memory lifecycle: archive memories the decay model expects to go unused.

Archived memories are hidden from retrieval but kept (and revived when new evidence
for the same topic arrives). Manual and seeded memories are never archived."""

import logging
import uuid
from datetime import datetime, timezone

from backend.app import config, db, ml
from backend.app.ml.features import decay_features

log = logging.getLogger("dbms-el.lifecycle")


def archive_stale_memories(user_id: uuid.UUID) -> int:
    model = ml.get_decay()
    memories = db.query(
        "SELECT id, importance, created_at, updated_at FROM memories "
        "WHERE user_id = %s AND archived_at IS NULL AND source IN ('auto', 'habit')",
        [user_id],
    )
    now = datetime.now(timezone.utc)
    archived = 0
    for m in memories:
        age_days = (now - m["created_at"]).total_seconds() / 86400
        if age_days < config.DECAY_MIN_AGE_DAYS:
            continue
        evidence = [r["observed_at"] for r in db.query(
            "SELECT o.observed_at FROM memory_observations mo "
            "JOIN observations o ON o.id = mo.observation_id WHERE mo.memory_id = %s",
            [m["id"]])]
        hits = [r["created_at"] for r in db.query(
            "SELECT q.created_at FROM query_memories qm "
            "JOIN chat_queries q ON q.id = qm.query_id WHERE qm.memory_id = %s",
            [m["id"]])]
        feats = decay_features(age_days, (now - m["updated_at"]).total_seconds() / 86400,
                               m["importance"], evidence, hits, now)
        if model.keep_probability(feats) < config.DECAY_ARCHIVE_BELOW:
            db.execute("UPDATE memories SET archived_at = now() WHERE id = %s", [m["id"]])
            archived += 1
            log.info("Archived memory %s", str(m["id"])[:8])
    return archived
