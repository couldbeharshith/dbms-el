"""DBMS-EL backend entry point.

Startup: apply schema -> seed default user -> start loading the local embedding
model on the GPU -> run the consolidation loop (every 2 min) and the retention
cleanup loop (every 6 h).
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app import config, db, pipeline
from backend.app.embed import service as embed_service
from backend.app.routers import data, events, memories, queries, screenshots, settings, stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("dbms-el")


async def _consolidation_loop(user_id):
    # Wait for the embedding model before the first pass (background load ~10s).
    embed_service.wait_ready(timeout=240)
    while True:
        try:
            await asyncio.wait_for(
                pipeline.consolidate(user_id), timeout=600
            )
        except asyncio.TimeoutError:
            log.warning("Consolidation pass timed out")
        except Exception:
            log.exception("Consolidation pass failed")
        await asyncio.sleep(config.CONSOLIDATION_INTERVAL_SEC)


async def _retention_loop(user_id):
    while True:
        try:
            days = db.query_one(
                "SELECT (settings->>'retention_days')::int AS d FROM users WHERE id = %s",
                [user_id],
            )["d"]
            db.execute(
                "DELETE FROM browsing_events WHERE visited_at < now() - make_interval(days => %s)",
                [days],
            )
            old = db.query(
                "DELETE FROM screenshots WHERE captured_at < now() - make_interval(days => %s) "
                "RETURNING file_path",
                [days],
            )
            for p in old:
                try:
                    (config.DATA_DIR / p["file_path"]).unlink(missing_ok=True)
                except OSError:
                    pass
            db.execute(
                "DELETE FROM chat_queries WHERE created_at < now() - make_interval(days => %s)",
                [days],
            )
            if old:
                log.info("Retention cleanup removed %d old screenshots", len(old))
        except Exception:
            log.exception("Retention cleanup failed")
        await asyncio.sleep(config.RETENTION_CHECK_INTERVAL_SEC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    db.apply_schema(config.REPO_ROOT / "sql" / "schema.sql")
    app.state.user = db.get_default_user()
    log.info("Schema applied; default user %s", app.state.user["username"])

    # Fire-and-forget async pipeline tasks (screenshot processing etc.)
    app.state.pending_tasks = set()

    loops = [
        asyncio.create_task(_consolidation_loop(app.state.user["id"]), name="consolidate"),
        asyncio.create_task(_retention_loop(app.state.user["id"]), name="retention"),
    ]
    yield
    for t in loops:
        t.cancel()
    for t in list(app.state.pending_tasks):
        t.cancel()
    db.close_pool()


app = FastAPI(title="DBMS-EL — Personalized AI Memory Layer", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local hobby deployment: extension + electron + browser tools
    allow_methods=["*"],
    allow_headers=["*"],
    allow_private_network=True,  # https pages (chat sites) fetching http://localhost
)

app.include_router(events.router)
app.include_router(screenshots.router)
app.include_router(memories.router)
app.include_router(queries.router)
app.include_router(stats.router)
app.include_router(settings.router)
app.include_router(data.router)


@app.get("/")
async def root():
    return {"service": "DBMS-EL backend", "docs": "/docs"}
