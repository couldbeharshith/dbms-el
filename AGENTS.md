# AGENTS.md

This file provides guidance to all AI Agents when working with code in this repository.

## Project

DBMS-EL is a local "personal memory layer" for AI chatbots, built as a DBMS course project. Activity capture (Chrome extension + Electron screenshots) → observations → consolidated memories in Postgres → context injected into chatgpt.com / claude.ai / gemini.google.com before send. The demo goal is the DBMS itself: all SQL is hand-written and parameterized (psycopg3, no ORM), and the schema is deliberately kept in BCNF.

Three parts, all talking to `http://localhost:8000` (hard-coded in `desktop/src/api.ts`, `desktop/electron/main.cjs`, `extension/*.js`):

- `backend/`: FastAPI app (Python 3.13, root-level uv project; the `.venv` is at repo root).
- `desktop/`: Electron + React 19 + Vite + Tailwind v4 (TS renderer, CJS main/preload).
- `extension/`: Chrome MV3, plain JS, no build step.

## Commands

```bash
# Postgres 17 + pgvector + PostGIS container "my-postgres" (keeps the postgres_data volume)
bash scripts/setup-db.sh

# Backend (run from repo root; module path matters)
uv sync
uv run uvicorn backend.app.main:app --port 8000      # docs at /docs, health at /api/status

# Demo data
uv run python scripts/seed_demo.py                   # add
uv run python scripts/seed_demo.py --reset           # wipe + reseed

# SQL demo queries (sql/demo.sql)
bash scripts/run-demo-sql.sh

# ML: training data, models, report (all optional; without models the components fall back to baselines)
uv run python scripts/synthetic/generate.py          # OpenAI (flex, batch fallback) -> data/synthetic/*.json; resumable
uv run python scripts/synthetic/embed_data.py        # embed that data with the local Jina model
uv run python scripts/train_models.py importance     # also: decay
uv run python scripts/train_retrieval.py gate        # also: ranker (folds in thumbs votes from the DB)
uv run python scripts/evaluate.py                    # every component vs its baseline; or name one section
```

`generate.py --check` polls any pending Batch job; `--import-batch FILE` ingests a downloaded batch output file.
```bash

# Desktop (desktop/ has an npm package-lock; scripts work the same under bun)
cd desktop && bun install
bun run build        # vite -> desktop/dist (what `start` loads)
bun run start        # electron on the built dist
bun run dev          # vite on :5174 + electron with ELECTRON_START_URL (hot reload)

# Extension: chrome://extensions -> Developer mode -> Load unpacked -> extension/
```

The repo has no test suite and no linter config.

## Runtime Dependencies (backend startup fails without them)

- **PostgreSQL** at `DATABASE_URL`. `sql/schema.sql` runs on **every startup** (`db.apply_schema` in the lifespan hook), so it must stay idempotent.
- **MongoDB** at `MONGO_URL` (default `mongodb://localhost:27017`). `mongo.init()` pings it with a 3 s timeout and aborts startup if it can't connect. No script or container for it exists in the repo, and the README doesn't mention it.
- **Local Jina model** (`jinaai/jina-embeddings-v5-omni-small`) at `EMBED_MODEL_DIR`, outside the repo. It loads in a background thread on the GPU (~10 s). `/api/status` → `embed_ready`.
- **`OPENAI_API_KEY`** is needed only for captioning and memory summarization (`gpt-6-luna`). Without it, screenshots stay unprocessed. `POST /api/screenshots/reprocess` retries them later.
- **Trained models** in `data/models/*.joblib` (importance, decay, gate, ranker; gitignored, built by the scripts above). A missing file makes that component fall back to its baseline with a log warning. `RANKER = "cross_encoder"` downloads `BAAI/bge-reranker-base` (~1 GB) on first use.
- torch/torchvision come from the CUDA 12.6 index pinned in `pyproject.toml` (`[tool.uv.sources]`).

## Architecture

### Data flow

1. **Browsing:** `extension/background.js` tracks dwell time per tab (active only, ≥3 s) and flushes to `POST /api/events` every 30 s via `chrome.alarms`.
2. **Screenshots:** `desktop/electron/main.cjs` captures the primary screen on a timer (`screenshot_interval_sec` from `/api/settings`). It reads the foreground window title through a PowerShell/user32 call and sends it as `source_app` to `POST /api/screenshots`.
3. **Upload** (`routers/screenshots.py`): blacklist check (Mongo phrases matched against the window title), then exact SHA-256 dedupe (global), then perceptual dedupe (`phash.py`, 10-min window). The pixels are AES-GCM encrypted to `data/screenshots/<uuid>.enc` (`crypto.py`, key `data/secret.key`). Processing starts as a fire-and-forget asyncio task tracked in `app.state.pending_tasks`.
4. **`observations.process_screenshot`:** decrypt → `caption.caption_image` (OpenAI, JSON: caption/ocr_text/topics/importance_hint) → embeds the caption+OCR text and the image separately → inserts an `observations` row (OCR encrypted into `ocr_encrypted` bytea) and `observation_topics`. If the title ends in a browser name (`config.BROWSER_TITLE_TAILS`), it links `screenshots.browsing_event_id` to the most recent event within 15 min.
5. **`consolidation.consolidate`:** runs every 2 min from `main.py`, or on demand via `POST /api/consolidate`, which passes `fast=True` and skips the flex tier. Steps: backfill missed URL links, dedupe near-identical memories, cluster observations (`ml.Clusterer`, default agglomerative over the last 30 days), then per cluster:
   - optional habit detection (`ml.habits`): a time-of-day pattern becomes a `source='habit'` memory;
   - merge into an existing memory (matched by shared evidence first, then by centroid similarity; habit memories are excluded from matching, and a merge un-archives);
   - otherwise `ImportanceScorer.assess_new` decides whether to promote the cluster. Unpromoted clusters stay "pending" (no memory, no LLM call) and are reconsidered on later passes as they recur. Promoted ones are summarized.
   After the loop, `lifecycle.archive_stale_memories` sets `archived_at` on memories the decay model expects to go unused (auto/habit sources only, at least 14 days old).
   The LLM is called **only when a cluster has unlinked evidence**. Evidence goes in `memory_observations`.
6. **Retrieval** (`retrieval.py`, `POST /api/context`): `extension/content.js` intercepts Enter/send, asks for context (via the service worker, with a direct-fetch fallback), appends the block after the user''s text, then re-clicks send. `rank_memories`: SQL picks 20 non-archived candidates by `0.6·cosine + 0.25·importance + 0.15·recency`, the `QueryGate` decides whether to inject at all, the `MemoryRanker` reorders, top 5 are used. With the gate on, weak matches are not injected (the baseline injected its best 3 anyway). Each query is logged to `chat_queries` + `query_memories`.7. **Recall** (`routers/recall.py`): semantic search over observations using both text and image embeddings.

A 6-hourly retention loop in `main.py` deletes old events, screenshots (and their `.enc` files) and chat queries.

### ML components (`backend/app/ml/`)

Each swappable decision is an interface in `interfaces.py` with a baseline (the original logic) and ML implementations. `config.py` selects them (`CLUSTERER`, `IMPORTANCE`, `DECAY`, `HABITS`, `GATE`, `RANKER`); `ml/__init__.py` builds them and falls back to the baseline if a model file is missing.

| Component | Baseline | ML |
|---|---|---|
| Clusterer | `greedy` centroid (24 h window) | `agglomerative` (default), `hdbscan` |
| ImportanceScorer | `heuristic` (size + recency) | `model`: classifier on time-pattern features (`ml/features.py`) |
| DecayModel | `off` | `model`: P(retrieved within 14 days) |
| QueryGate | `always` | `model`: logistic regression on query embedding + nearest-memory similarities |
| MemoryRanker | `baseline` (SQL order) | `learned` (LR on sim/importance/recency, default), `cross_encoder` |

- **Training data is simulated.** `scripts/synthetic/` has the topic catalog, the OpenAI generator (captions, queries, memories) and `sim.py` (90-day fake users with ground truth). Importance/decay train on simulated timelines, so reported numbers are optimistic. Real feedback comes from the Train memories tab.
- **Train memories tab** (`routers/training.py`, `desktop/src/pages/TrainMemories.tsx`): the user types prompts and votes thumbs up/down on the memories shown (`training_prompts`, `memory_feedback`). `POST /api/training/retrain` refits the learned ranker from `data/models/ranker_base.joblib` (simulated rows) plus the votes (weight 5) and hot-swaps it. It runs from the Finish button, when the user leaves the tab or closes the app, and at backend startup if a vote is newer than `ranker.joblib` (`ml/retrain.py`). `scripts/train_retrieval.py ranker` does the same offline and also regenerates the base rows.
- The 30-day clustering window needs `users.retention_days >= 30`, otherwise observations are deleted before a pattern forms.
- sklearn 1.9 HDBSCAN crashes with `cluster_selection_epsilon > 0`; do not use it.
- Measured on the simulated held-out personas (`scripts/evaluate.py`): agglomerative ARI 0.66 vs greedy 0.33 at the old 0.50 threshold; importance AUC 0.92 vs 0.86; gate accuracy 0.90.

### Non-obvious details

- **Embeddings:** one shared 1024-d text/image space. Use `embed_service.embed_query` for chat and search queries (`"Query: "` prefix) and `embed_document`/`embed_image` for stored content (`"Document: "` prefix). Mixing them up degrades similarity. Inference is serialized behind a lock in a worker thread.
- **Import order:** `backend.app.config` must be imported before torch/transformers. It sets the `HF_*` cache env vars to `data/hf_home`.
- **Vectors go in as text literals:** `db.vec_lit(vec)` plus a `%s::vector` cast. They come back as text, parsed with `embed.parse_vec_column` or `::text` + split.
- **Single-user design:** `app.state.user` is the default Postgres user (`DEFAULT_USERNAME`), loaded once at startup. Every router reads it from the request. Settings are cached there and updated by `PATCH /api/settings`, so editing the `users` row directly needs a backend restart.
- **Two databases:**
  - Postgres holds all relational and vector data.
  - Mongo (`mongo.py`) holds only `accounts` (username + PBKDF2 6-digit PIN for the desktop `LockScreen`) and `blacklist`.
  - The Mongo login is just a UI gate. It isn't linked to the Postgres user, and the other routes aren't protected.
- **Schema rules** (`sql/schema.sql`):
  - Derived data lives in views instead of columns: `v_observations` (kind, url, topics[]), `v_browsing_events` (domain via `url_domain()`), `v_memory_usage`, `v_memory_topics`, `v_memory_evidence`. Query these views instead of re-adding denormalized columns.
  - Each observation has exactly one source (`obs_exactly_one_source` CHECK).
  - `memories.archived_at` hides a memory from retrieval without deleting it; `memories.source` also allows `habit`.
  - The file also contains in-place migrations from an older denormalized schema. `data/pre_bcnf.dump` is a backup of that older schema.
- **OpenAI calls** (`caption.py`): flex tier → auto tier → no-`response_format` fallback. Use `max_completion_tokens` (the legacy `max_tokens` is rejected). Summaries retry with a larger budget because reasoning tokens can leave the visible output empty. Images are downscaled to ≤720p JPEG before upload.
- **Tuning thresholds:** `backend/app/config.py` is the source of truth. The README's numbers (0.78 / ≥3) are out of date. Greedy clustering uses 0.50 / 2, agglomerative distance 0.70, merge 0.70, duplicate 0.88, importance promotion 0.5, decay archive below 0.25, habits need 5 observations on 4 days (Rayleigh p < 0.01).
- **Chat-site DOM selectors** live in the `SITES` map at the top of `extension/content.js`. Gemini support is best-effort.
- **`dbms-el://` protocol:** Electron registers it so the extension popup can focus the app. The desktop UI auto-locks after 5 min idle.

## General Working Rules (User Preferences)

- **Ignore `./temp/` completely.** Never read from it. It is the User's scratchpad.
- **Use `./agent_temp/` as scartchpad.** This is for your own use. Temp scripts, tests, evals etc. Don't bother clearing it unless needed.
- **Never `git commit` or `git push`.** The User reviews and commits by hand. `git pull` is fine.
- **Keep it simple.** Treat this as a hobby project unless told it is production. Don't overengineer.
- **No tests** unless the user asks. You may ask before a coding session starts.
- **Minimal comments.** Use descriptive names so the code explains itself.
- **Docs:** don't write a new `.md` per change. Ask first. If yes, update an existing `.md` or this file.
- **Python:** use `uv`, not pip, unless the project already uses pip. Keep `pyproject.toml` complete and current.
- **JS/TS:** use Bun for packages and scripts. Prefer TS over JS unless told otherwise.
- **New frontends:** latest Next.js unless told otherwise. **New backend APIs:** FastAPI.
- **Repo layout:** prefer a monorepo unless told otherwise.
- **CLI scaffolding (`bunx`/`npx`, etc.):** pass every option as flags so nothing prompts interactively. Ask the user about choices first if needed.
- **Docker:** use multi-stage builds and small base images. Multiple containers → Dockerfiles plus a `docker-compose.yml`.
- Never use emojis in
- Keep `.gitignore` and `.env.example` in sync with changes.
