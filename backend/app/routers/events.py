"""Browsing events from the Chrome extension."""

import datetime as dt

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app import db, geo

router = APIRouter(prefix="/api", tags=["events"])


class BrowsingEventIn(BaseModel):
    url: str
    domain: str
    title: str | None = None
    visited_at: dt.datetime
    time_spent_sec: int = 0
    session_id: str | None = None
    metadata: dict = {}


class EventsIn(BaseModel):
    events: list[BrowsingEventIn]


_INSERT = """
    INSERT INTO browsing_events
        (user_id, url, domain, title, visited_at, time_spent_sec, session_id,
         location, metadata)
    VALUES %s
"""


@router.post("/events")
async def add_events(payload: EventsIn, request: Request):
    user = request.app.state.user
    if not user["settings"].get("tracking_enabled", True):
        return {"inserted": 0, "tracking": False}

    # GeoIP point is attached only when the user enabled location in Settings.
    point = await geo.get_location() if user["settings"].get("location_enabled") else None

    if point:
        sql = _INSERT % ("(%s, %s, %s, %s, %s, %s, %s, "
                         "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s::jsonb)",)
        params = [[user["id"], e.url, e.domain, e.title, e.visited_at,
                   e.time_spent_sec, e.session_id, point[0], point[1],
                   db._jsonb(e.metadata)] for e in payload.events]
    else:
        sql = _INSERT % ("(%s, %s, %s, %s, %s, %s, %s, NULL, %s::jsonb)",)
        params = [[user["id"], e.url, e.domain, e.title, e.visited_at,
                   e.time_spent_sec, e.session_id, db._jsonb(e.metadata)]
                  for e in payload.events]

    if params:
        db.executemany(sql, params)
    return {"inserted": len(params), "tracking": True}


@router.get("/events")
async def list_events(request: Request, limit: int = 100, domain: str | None = None,
                      hours: int | None = None):
    user = request.app.state.user
    conds = ["user_id = %s"]
    args: list = [user["id"]]
    if domain:
        conds.append("domain = %s")
        args.append(domain)
    if hours:
        conds.append("visited_at > now() - make_interval(hours => %s)")
        args.append(hours)
    args.append(min(limit, 1000))
    rows = db.query(
        f"SELECT id, url, domain, title, visited_at, time_spent_sec, session_id, "
        f"metadata, ST_AsText(location) AS location "
        f"FROM browsing_events WHERE {' AND '.join(conds)} "
        f"ORDER BY visited_at DESC LIMIT %s",
        args,
    )
    for r in rows:
        r["metadata"] = r["metadata"] or {}
    return {"events": rows}
