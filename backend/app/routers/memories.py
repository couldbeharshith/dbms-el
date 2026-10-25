"""Memory management (view / edit / delete + evidence tracing)."""

import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app import db
from backend.app.embed import service as embed_service

router = APIRouter(prefix="/api", tags=["memories"])


@router.get("/memories")
async def list_memories(request: Request, limit: int = 200):
    user = request.app.state.user
    rows = db.query(
        """
        SELECT m.id, m.content, m.importance, m.hit_count, m.last_retrieved_at,
               m.created_at, m.updated_at, m.metadata,
               count(mo.observation_id) AS evidence_count
        FROM memories m
        LEFT JOIN memory_observations mo ON mo.memory_id = m.id
        WHERE m.user_id = %s
        GROUP BY m.id
        ORDER BY m.updated_at DESC
        LIMIT %s
        """,
        [user["id"], min(limit, 500)],
    )
    return {"memories": rows}


@router.get("/memories/{mid}/evidence")
async def memory_evidence(mid: uuid.UUID, request: Request):
    user = request.app.state.user
    rows = db.query(
        """
        SELECT observation_id, observation_kind, observation_caption, observed_at,
               contribution, screenshot_id, source_app, domain, url, page_title,
               time_spent_sec
        FROM v_memory_evidence
        WHERE memory_id = %s AND memory_id IN
              (SELECT id FROM memories WHERE user_id = %s)
        ORDER BY observed_at DESC
        """,
        [mid, user["id"]],
    )
    return {"evidence": rows}


class MemoryPatch(BaseModel):
    content: str | None = None
    importance: float | None = None


@router.patch("/memories/{mid}")
async def patch_memory(mid: uuid.UUID, payload: MemoryPatch, request: Request):
    user = request.app.state.user
    existing = db.query_one(
        "SELECT id, content, importance FROM memories WHERE id = %s AND user_id = %s",
        [mid, user["id"]],
    )
    if existing is None:
        raise HTTPException(404, "Memory not found")

    if payload.content is not None:
        vec = await embed_service.embed_document(payload.content)
        db.execute(
            "UPDATE memories SET content = %s, embedding = %s::vector, "
            "importance = %s WHERE id = %s",
            [payload.content, db.vec_lit(vec),
             payload.importance if payload.importance is not None else existing["importance"],
             mid],
        )
    elif payload.importance is not None:
        db.execute(
            "UPDATE memories SET importance = %s WHERE id = %s",
            [payload.importance, mid],
        )
    return {"updated": str(mid)}


@router.delete("/memories/{mid}")
async def delete_memory(mid: uuid.UUID, request: Request):
    user = request.app.state.user
    row = db.query_one(
        "DELETE FROM memories WHERE id = %s AND user_id = %s RETURNING id",
        [mid, user["id"]],
    )
    if row is None:
        raise HTTPException(404, "Memory not found")
    return {"deleted": str(mid)}
