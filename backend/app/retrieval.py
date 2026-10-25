"""Query -> Memory retrieval: embed the chat query locally, score memories by
0.6*cosine + 0.25*importance + 0.15*recency, and build the context block the
Chrome extension appends after the user's message."""

import json
import logging
import uuid

from backend.app import db
from backend.app.embed import service as embed_service

log = logging.getLogger("dbms-el.retrieval")

CONTEXT_HEADER = "[Personal memory context — retrieved automatically by DBMS-EL]"
CONTEXT_FOOTER = "[End of memory context]"


async def retrieve_context(user_id: uuid.UUID, query: str, site: str | None) -> dict:
    qvec = await embed_service.embed_query(query)
    qlit = db.vec_lit(qvec)

    rows = db.query(
        """
        SELECT id, content, importance,
               1 - (embedding <=> %s::vector) AS sim,
               exp(-extract(epoch from (now() - updated_at)) / 86400.0) AS recency
        FROM memories
        WHERE user_id = %s
        ORDER BY 0.6 * (1 - (embedding <=> %s::vector))
               + 0.25 * importance
               + 0.15 * exp(-extract(epoch from (now() - updated_at)) / 86400.0) DESC
        LIMIT 5
        """,
        [qlit, user_id, qlit],
    )

    strong = [r for r in rows if r["sim"] >= 0.30]
    used = strong if strong else rows[:3]
    confident = bool(strong)

    if used:
        body = "\n".join(f"- {r['content']}" for r in used)
        context_text = f"{CONTEXT_HEADER}\n{body}\n{CONTEXT_FOOTER}"
    else:
        context_text = ""

    used_ids = [r["id"] for r in used]
    db.execute(
        """
        INSERT INTO chat_queries (user_id, query_text, query_embedding, site,
                                  context_text, retrieved_memory_ids)
        VALUES (%s, %s, %s::vector, %s, %s, %s::jsonb)
        """,
        [user_id, query, qlit, site, context_text, json.dumps([str(i) for i in used_ids])],
    )
    if used_ids:
        db.execute(
            "UPDATE memories SET hit_count = hit_count + 1, last_retrieved_at = now() "
            "WHERE id = ANY(%s::uuid[])",
            [[str(i) for i in used_ids]],
        )

    log.info("Retrieved %d memories for %r (confident=%s)", len(used), query[:60], confident)
    return {
        "context": context_text,
        "count": len(used),
        "confident": confident,
        "memories": [
            {"id": str(r["id"]), "content": r["content"],
             "similarity": round(float(r["sim"]), 4), "importance": r["importance"]}
            for r in used
        ],
    }
