"""Recall — semantic search across the user's own observations.

One text query ("code", "python project", "that laptop page") is embedded with
the model's query prefix and matched against BOTH observation embeddings:
the text embedding (caption + OCR text) and the image embedding of the raw
screenshot (shared retrieval space, so text->image works). The combined score
is the better of the two. Results link back to the screenshot for viewing.
"""

from fastapi import APIRouter, Request

from backend.app import db
from backend.app.embed import service as embed_service

router = APIRouter(prefix="/api", tags=["recall"])

MIN_SCORE = 0.25


@router.get("/recall")
async def recall(request: Request, q: str, limit: int = 24):
    user = request.app.state.user
    q = q.strip()
    if not q:
        return {"results": []}
    if not embed_service.ready and not embed_service.wait_ready(timeout=60):
        return {"results": [], "error": "embedding model not ready"}

    qvec = await embed_service.embed_query(q)
    rows = db.query(
        """
        SELECT o.id, o.caption, o.topics, o.observed_at, o.url, o.screenshot_id,
               s.source_app, s.captured_at,
               1 - (o.embedding <=> %s::vector) AS text_sim,
               CASE WHEN o.image_embedding IS NOT NULL
                    THEN 1 - (o.image_embedding <=> %s::vector) END AS image_sim
        FROM observations o
        LEFT JOIN screenshots s ON s.id = o.screenshot_id
        WHERE o.user_id = %s AND o.embedding IS NOT NULL
        ORDER BY GREATEST(1 - (o.embedding <=> %s::vector),
                          coalesce(1 - (o.image_embedding <=> %s::vector), 0)) DESC
        LIMIT %s
        """,
        [db.vec_lit(qvec), db.vec_lit(qvec), user["id"], db.vec_lit(qvec),
         db.vec_lit(qvec), min(limit, 100)],
    )
    results = []
    for r in rows:
        text_sim = float(r["text_sim"])
        image_sim = float(r["image_sim"]) if r["image_sim"] is not None else 0.0
        score = max(text_sim, image_sim)
        if score < MIN_SCORE:
            continue
        results.append({
            "observation_id": str(r["id"]),
            "screenshot_id": str(r["screenshot_id"]) if r["screenshot_id"] else None,
            "caption": r["caption"],
            "topics": r["topics"],
            "observed_at": r["observed_at"],
            "url": r["url"],
            "source_app": r["source_app"],
            "score": round(score, 4),
            "text_sim": round(text_sim, 4),
            "image_sim": round(image_sim, 4),
        })
    return {"results": results}
