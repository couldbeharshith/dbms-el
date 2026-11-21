"""Train memories tab: the user types example chat prompts, sees which memories the app
would retrieve, and votes thumbs up/down. The votes become training labels for the
learned ranker (scripts/train_retrieval.py ranker)."""

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app import db, retrieval
from backend.app.embed import service as embed_service
from backend.app.ml.retrain import ranker_is_stale, retrain_ranker

router = APIRouter(prefix="/api/training", tags=["training"])

SHOWN_MEMORIES = 8


class PromptIn(BaseModel):
    text: str


class VoteIn(BaseModel):
    memory_id: uuid.UUID
    vote: int  # 1, -1, or 0 to clear


async def _show(request: Request, prompt_id: uuid.UUID, text: str, embedding: str) -> dict:
    user = request.app.state.user
    qvec = [float(x) for x in embedding.strip("[]").split(",")]
    ranked, gate_open = await retrieval.rank_memories(
        user["id"], text, qvec, apply_gate=False, top_k=SHOWN_MEMORIES)
    votes = {r["memory_id"]: r["vote"] for r in db.query(
        "SELECT memory_id, vote FROM memory_feedback WHERE prompt_id = %s", [prompt_id])}
    return {
        "id": str(prompt_id), "text": text, "gate_open": gate_open,
        "memories": [{"id": str(r["id"]), "content": r["content"],
                      "similarity": round(float(r["sim"]), 3), "importance": r["importance"],
                      "vote": votes.get(r["id"], 0)} for r in ranked],
    }


@router.post("/prompts")
async def add_prompt(payload: PromptIn, request: Request):
    text = payload.text.strip()
    if not text or len(text) > 500:
        raise HTTPException(400, "Prompt must be 1-500 characters")
    qvec = await embed_service.embed_query(text)
    row = db.query_one(
        "INSERT INTO training_prompts (user_id, text, embedding) VALUES (%s, %s, %s::vector) "
        "RETURNING id, embedding::text AS embedding",
        [request.app.state.user["id"], text, db.vec_lit(qvec)])
    return await _show(request, row["id"], text, row["embedding"])


@router.get("/prompts")
async def list_prompts(request: Request):
    rows = db.query(
        "SELECT p.id, p.text, p.created_at, "
        "count(f.*) FILTER (WHERE f.vote = 1) AS up, count(f.*) FILTER (WHERE f.vote = -1) AS down "
        "FROM training_prompts p LEFT JOIN memory_feedback f ON f.prompt_id = p.id "
        "WHERE p.user_id = %s GROUP BY p.id ORDER BY p.created_at DESC LIMIT 100",
        [request.app.state.user["id"]])
    return {"prompts": rows}


@router.get("/prompts/{prompt_id}")
async def open_prompt(prompt_id: uuid.UUID, request: Request):
    row = db.query_one(
        "SELECT id, text, embedding::text AS embedding FROM training_prompts "
        "WHERE id = %s AND user_id = %s", [prompt_id, request.app.state.user["id"]])
    if row is None:
        raise HTTPException(404, "Prompt not found")
    return await _show(request, row["id"], row["text"], row["embedding"])


@router.put("/prompts/{prompt_id}/votes")
async def vote(prompt_id: uuid.UUID, payload: VoteIn, request: Request):
    if payload.vote not in (-1, 0, 1):
        raise HTTPException(400, "vote must be -1, 0 or 1")
    owned = db.query_one("SELECT 1 FROM training_prompts WHERE id = %s AND user_id = %s",
                         [prompt_id, request.app.state.user["id"]])
    if owned is None:
        raise HTTPException(404, "Prompt not found")
    if payload.vote == 0:
        db.execute("DELETE FROM memory_feedback WHERE prompt_id = %s AND memory_id = %s",
                   [prompt_id, payload.memory_id])
    else:
        db.execute(
            "INSERT INTO memory_feedback (prompt_id, memory_id, vote) VALUES (%s, %s, %s) "
            "ON CONFLICT (prompt_id, memory_id) DO UPDATE SET vote = EXCLUDED.vote, voted_at = now()",
            [prompt_id, payload.memory_id, payload.vote])
    return {"ok": True}


@router.delete("/prompts/{prompt_id}")
async def delete_prompt(prompt_id: uuid.UUID, request: Request):
    row = db.query_one("DELETE FROM training_prompts WHERE id = %s AND user_id = %s RETURNING id",
                       [prompt_id, request.app.state.user["id"]])
    if row is None:
        raise HTTPException(404, "Prompt not found")
    return {"deleted": str(prompt_id)}


@router.post("/retrain")
async def retrain(request: Request):
    """Finish button / leaving the tab: refit the learned ranker from the votes, no restart needed."""
    if not await asyncio.to_thread(ranker_is_stale):
        return {"retrained": False, "reason": "no new votes since the last retrain"}
    return await asyncio.to_thread(retrain_ranker, request.app.state.user["id"])
