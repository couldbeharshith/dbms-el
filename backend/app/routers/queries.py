"""Chat query retrieval — called by the Chrome extension right before a
message is sent on a chatbot site."""

import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app import db, retrieval
from backend.app.embed import service as embed_service

router = APIRouter(prefix="/api", tags=["queries"])


class ContextIn(BaseModel):
    query: str
    site: str | None = None


@router.post("/context")
async def get_context(payload: ContextIn, request: Request):
    user = request.app.state.user
    if not user["settings"].get("injection_enabled", True):
        return {"context": "", "count": 0, "injection_enabled": False, "memories": []}
    if not embed_service.ready:
        if embed_service.error is not None or not embed_service.wait_ready(timeout=30):
            raise HTTPException(503, "Embedding model is not available")
    result = await retrieval.retrieve_context(user["id"], payload.query, payload.site)
    return result


@router.get("/queries")
async def list_queries(request: Request, limit: int = 50):
    user = request.app.state.user
    rows = db.query(
        """
        SELECT id, query_text, site, context_text, retrieved_memory_ids, created_at
        FROM chat_queries WHERE user_id = %s
        ORDER BY created_at DESC LIMIT %s
        """,
        [user["id"], min(limit, 200)],
    )
    return {"queries": rows}
