"""User settings + system status."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app import config, db
from backend.app.embed import service as embed_service

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
async def get_settings(request: Request):
    user = request.app.state.user
    return {"user_id": str(user["id"]), "username": user["username"],
            "settings": user["settings"]}


class SettingsPatch(BaseModel):
    tracking_enabled: bool | None = None
    screenshots_enabled: bool | None = None
    screenshot_interval_sec: int | None = None
    location_enabled: bool | None = None
    injection_enabled: bool | None = None
    retention_days: int | None = None


@router.patch("/settings")
async def patch_settings(payload: SettingsPatch, request: Request):
    user = request.app.state.user
    merged = dict(user["settings"])
    updates = payload.model_dump(exclude_none=True)
    if "screenshot_interval_sec" in updates:
        updates["screenshot_interval_sec"] = max(10, min(600, updates["screenshot_interval_sec"]))
    if "retention_days" in updates:
        updates["retention_days"] = max(1, min(365, updates["retention_days"]))
    merged.update(updates)
    db.execute(
        "UPDATE users SET settings = %s::jsonb WHERE id = %s",
        [db._jsonb(merged), user["id"]],
    )
    request.app.state.user["settings"] = merged
    return {"settings": merged}


@router.get("/status")
async def system_status(request: Request):
    db_ok = db.query_one("SELECT 1 AS ok") is not None
    return {
        "db": db_ok,
        "embed_ready": embed_service.ready,
        "embed_device": embed_service.device,
        "embed_error": str(embed_service.error) if embed_service.error else None,
        "openai_configured": bool(config.OPENAI_API_KEY),
        "model": config.OPENAI_MODEL,
    }
