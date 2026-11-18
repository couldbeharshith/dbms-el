"""MongoDB access (demo-grade): user accounts + screenshot blacklist.

Postgres holds the relational/vector data; MongoDB holds these two small,
schema-free collections.
  accounts  {username (unique), pin_salt, pin_hash, created_at}
  blacklist {phrase (unique, lowercase), created_at}
"""

import datetime as dt

from pymongo import ASCENDING, MongoClient

from backend.app import config

_client: MongoClient | None = None


def init() -> None:
    global _client
    if _client is None:
        _client = MongoClient(config.MONGO_URL, serverSelectionTimeoutMS=3000)
        _client.admin.command("ping")  # fail fast if Mongo isn't up
        db().accounts.create_index([("username", ASCENDING)], unique=True)
        db().blacklist.create_index([("phrase", ASCENDING)], unique=True)


def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def db():
    return _client[config.MONGO_DB]


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
