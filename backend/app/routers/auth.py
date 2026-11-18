"""Demo login: unique username + 6-digit PIN, stored in MongoDB.
Gate only — the other API routes are not protected (single shared data set)."""

import hashlib
import hmac
import os
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from backend.app import mongo

router = APIRouter(prefix="/api/auth", tags=["auth"])

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
PIN_RE = re.compile(r"^\d{6}$")
RESET_PIN = "000000"


def _hash(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 100_000).hex()


def _set_pin(pin: str) -> dict:
    salt = os.urandom(16)
    return {"pin_salt": salt.hex(), "pin_hash": _hash(pin, salt)}


class Credentials(BaseModel):
    username: str
    pin: str


class Forgot(BaseModel):
    username: str


@router.get("/state")
async def state():
    return {"accounts": mongo.db().accounts.count_documents({})}


@router.post("/signup")
async def signup(c: Credentials):
    username = c.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Username: 3-32 letters, digits, . _ -")
    if not PIN_RE.match(c.pin):
        raise HTTPException(400, "PIN must be exactly 6 digits")
    try:
        mongo.db().accounts.insert_one(
            {"username": username, **_set_pin(c.pin), "created_at": mongo.now()})
    except DuplicateKeyError:
        raise HTTPException(409, "Username already taken")
    return {"ok": True, "username": username}


@router.post("/login")
async def login(c: Credentials):
    acct = mongo.db().accounts.find_one({"username": c.username.strip()})
    ok = bool(acct) and hmac.compare_digest(
        acct["pin_hash"], _hash(c.pin, bytes.fromhex(acct["pin_salt"])))
    if not ok:
        raise HTTPException(401, "Incorrect username or PIN")
    return {"ok": True, "username": acct["username"]}


@router.post("/forgot")
async def forgot(f: Forgot):
    """Dummy recovery: resets the PIN to 000000 and says so."""
    res = mongo.db().accounts.update_one(
        {"username": f.username.strip()}, {"$set": _set_pin(RESET_PIN)})
    if res.matched_count == 0:
        raise HTTPException(404, "No such username")
    return {"ok": True, "message": f"PIN reset to {RESET_PIN}. Sign in and use it."}
