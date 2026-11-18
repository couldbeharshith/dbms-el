"""Screenshot blacklist: if the foreground window title contains any phrase,
the capture is skipped. Stored in MongoDB."""

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from backend.app import mongo

router = APIRouter(prefix="/api", tags=["blacklist"])


class PhraseIn(BaseModel):
    phrase: str


def _clean(p: str) -> str:
    p = p.strip().lower()
    if not p or len(p) > 80:
        raise HTTPException(400, "Phrase must be 1-80 characters")
    return p


def _oid(i: str) -> ObjectId:
    try:
        return ObjectId(i)
    except InvalidId:
        raise HTTPException(404, "Not found")


def phrases() -> list[str]:
    return [d["phrase"] for d in mongo.db().blacklist.find()]


def is_blacklisted(title: str | None) -> bool:
    t = (title or "").lower()
    return bool(t) and any(p in t for p in phrases())


@router.get("/blacklist")
async def list_blacklist():
    docs = mongo.db().blacklist.find().sort("created_at", 1)
    return {"items": [{"id": str(d["_id"]), "phrase": d["phrase"]} for d in docs]}


@router.post("/blacklist")
async def add_phrase(p: PhraseIn):
    try:
        r = mongo.db().blacklist.insert_one({"phrase": _clean(p.phrase), "created_at": mongo.now()})
    except DuplicateKeyError:
        raise HTTPException(409, "Already in the blacklist")
    return {"id": str(r.inserted_id)}


@router.put("/blacklist/{item_id}")
async def edit_phrase(item_id: str, p: PhraseIn):
    try:
        r = mongo.db().blacklist.update_one({"_id": _oid(item_id)}, {"$set": {"phrase": _clean(p.phrase)}})
    except DuplicateKeyError:
        raise HTTPException(409, "Already in the blacklist")
    if r.matched_count == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.delete("/blacklist/{item_id}")
async def delete_phrase(item_id: str):
    r = mongo.db().blacklist.delete_one({"_id": _oid(item_id)})
    if r.deleted_count == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}
