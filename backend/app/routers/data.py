"""Privacy data controls + manual consolidation trigger."""

import uuid

from fastapi import APIRouter, HTTPException, Request

from backend.app import config, db, pipeline

router = APIRouter(prefix="/api", tags=["data"])


@router.post("/consolidate")
async def consolidate_now(request: Request):
    user = request.app.state.user
    # Manual trigger: skip the flex tier — the user is waiting on this call.
    stats = await pipeline.consolidate(user["id"], fast=True)
    return stats


@router.delete("/data/{scope}")
async def clear_data(scope: str, request: Request):
    user = request.app.state.user
    uid = user["id"]

    if scope == "browsing":
        row = db.query_one(
            "WITH del AS (DELETE FROM browsing_events WHERE user_id = %s RETURNING 1) "
            "SELECT count(*) AS n FROM del", [uid])
        return {"deleted_browsing_events": row["n"]}
    if scope == "screenshots":
        paths = db.query(
            "DELETE FROM screenshots WHERE user_id = %s RETURNING file_path", [uid])
        for p in paths:
            try:
                (config.DATA_DIR / p["file_path"]).unlink(missing_ok=True)
            except OSError:
                pass
        return {"deleted_screenshots": len(paths)}
    if scope == "memories":
        row = db.query_one(
            "WITH del AS (DELETE FROM memories WHERE user_id = %s RETURNING 1) "
            "SELECT count(*) AS n FROM del", [uid])
        return {"deleted_memories": row["n"]}
    if scope == "chat_queries":
        row = db.query_one(
            "WITH del AS (DELETE FROM chat_queries WHERE user_id = %s RETURNING 1) "
            "SELECT count(*) AS n FROM del", [uid])
        return {"deleted_chat_queries": row["n"]}
    if scope == "all":
        counts = {}
        for table in ("chat_queries", "memories", "observations", "screenshots",
                      "browsing_events"):
            row = db.query_one(
                f"WITH del AS (DELETE FROM {table} WHERE user_id = %s RETURNING 1) "
                "SELECT count(*) AS n FROM del", [uid])
            counts[table] = row["n"]
        for p in config.SCREENSHOT_DIR.glob("*.enc"):
            try:
                p.unlink()
            except OSError:
                pass
        return counts
    raise HTTPException(400, "scope must be browsing|screenshots|memories|chat_queries|all")
