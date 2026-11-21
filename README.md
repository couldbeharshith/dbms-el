# DBMS-EL — Personalized AI Memory Layer

A hobby DBMS project that gives AI chatbots a **personal memory**. It observes your
computer activity (browsing via a Chrome extension + periodic screenshots via an
Electron app), converts useful activity into searchable **memories** in PostgreSQL,
and **injects relevant context** into chat sites (chatgpt.com, claude.ai,
gemini.google.com) right before your message is sent.

```
USER COMPUTER
     |                                   Chrome Extension ──> browsing events ─┐
     │                                                                    │
     └── Windows App (Electron) ──> periodic encrypted screenshots ────────┤
                                                                          v
                                              Backend API (FastAPI, port 8000)
                                                                          v
                                     PostgreSQL 17 (docker, pgvector + PostGIS)
                                                                          v
                     Screenshot ──> gpt-6-luna caption/OCR ──> observation ──> embedding
                     (local GPU: jinaai/jina-embeddings-v5-omni-small, 1024-d)          │
                                                                                       v
                     recurring activity ──> memory (ML: clustering, importance model, habits) ┘
                                                                          v
                     chat query ──> gate (needs memory?) ──> pgvector candidates ──> learned ranker
                                ──> context appended after your message, then send
```

The database design (hand-written parameterized SQL, BCNF schema, views) is the core of
the project. Six decisions in the pipeline are made by small ML models, each with the
original rule-based logic kept as a switchable baseline. See [ML.md](ML.md) for the
numbers and [ML section](#machine-learning) below.

## Components

| Folder         | What it is                                                                    |
|----------------|-------------------------------------------------------------------------------|
| `backend/`     | FastAPI app: observations, consolidation, lifecycle, retrieval, encryption    |
| `backend/app/ml/` | Swappable ML components (clustering, importance, decay, habits, gate, ranker) |
| `extension/`   | Chrome MV3 extension: dwell-time tracking + chat context injection            |
| `desktop/`     | Electron + React + Tailwind app: screenshots + Dashboard/History/Recall/Memories/Train memories/Settings |
| `sql/`         | `schema.sql` (DDL) and `demo.sql` (17 demonstrable DBMS queries)              |
| `docker/`      | Dockerfile for `postgres:17 + pgvector + PostGIS`                             |
| `scripts/`     | `setup-db.sh`, `seed_demo.py`, ML scripts (`synthetic/`, `train_models.py`, `train_retrieval.py`, `evaluate.py`) |
| `data/`        | runtime artifacts (gitignored): `secret.key`, encrypted screenshots, HF cache, `models/`, `synthetic/` |

## One-time setup

```bash
# 1. Database — rebuilds my-postgres with pgvector+PostGIS (same name/creds/volume;
#    existing data survives). Docker Desktop must be running.
bash scripts/setup-db.sh

# 1b. MongoDB (desktop login PINs + screenshot blacklist). The backend refuses to
#     start without it. Any local instance works, default URL mongodb://localhost:27017
#     (override with MONGO_URL in .env), e.g.: docker run -d --name my-mongo -p 27017:27017 mongo

# 2. Backend deps are managed by uv (the repo-root .venv is used — never system python).
#    Already installed if you ran the project; otherwise:
uv sync

# 3. Configure the API key (only used for image captioning — gpt-6-luna):
#    edit .env and set OPENAI_API_KEY=sk-...   (template in .env.example)
#    EMBED_MODEL_DIR already points at D:\AI\jina_omni_test\model from your benchmark.

# 4. Desktop app deps:
cd desktop && npm install && npm run build && cd ..
```

## Running (3 processes)

```bash
# Terminal 1 — backend (loads the Jina model onto the GPU, ~10 s; docs at /docs)
uv run uvicorn backend.app.main:app --port 8000

# Terminal 2 — Windows app
cd desktop && npm run start          # built version (or: npm run dev for hot reload)

# Chrome — load the extension
# chrome://extensions -> Developer mode -> Load unpacked -> select the extension/ folder
```

## Demo walkthrough (spec §13)

The database already contains a seeded "laptop research" session if you ran:

```bash
uv run python scripts/seed_demo.py          # add demo data
uv run python scripts/seed_demo.py --reset  # or wipe + reseed
```

1. **Dashboard** (Windows app) shows events, screenshots, observations, memories.
2. Open Chrome and browse some product pages — events appear in **History** within
   ~30 s (the extension batches every 30 s; dwell time is only counted while you
   are active).
3. The Windows app captures an encrypted screenshot every `screenshot_interval_sec`
   (default 30 s, Settings) with the foreground window title attached.
   **Duplicate suppression** (two layers): byte-identical captures are dropped
   globally by SHA-256; near-identical ones (idle screen, cursor blink) are
   dropped within a 10-minute window by a perceptual fingerprint (64×36
   grayscale thumbnail, mean-pixel-difference ≤ 0.5%). Nothing stored, nothing
   sent to any API. When a browser was in the foreground, the page URL is
   attached to the observation (display-only provenance).
4. Each screenshot is captioned + OCR'd by `gpt-6-luna` (flex tier first, auto
   fallback) and embedded **locally on your GPU** (text *and* image embeddings,
   `vector(1024)`).
5. Every 2 minutes (or via **Memories → Consolidate now**) the backend clusters the
   last 30 days of observations (agglomerative clustering on cosine distance 0.70).
   An importance model decides from the time pattern whether a cluster deserves a
   memory: recurring activity does, a one-off search stays "pending" (no memory, no
   LLM call). Promoted clusters become or merge into memories with linked evidence
   rows. Daily routines become `habit` memories, and memories the decay model
   expects to go unused are archived (hidden, not deleted).
6. Open a fresh chat on **chatgpt.com** (or claude.ai / gemini) and type
   `Which laptop should I buy?` then hit send.
7. The extension calls `POST /api/context`. SQL picks 20 candidates
   (`0.6·cosine + 0.25·importance + 0.15·recency`), a gate model decides whether the
   message needs memory at all (`What is 17 × 23?` gets nothing), and a learned ranker
   orders the rest. The top memories are appended **after your text** and sent.
   A toast confirms: `🧠 DBMS-EL: injected`.
8. The chatbot answers using your previously captured research.

## Recall (search your own screen history)

The **Recall** page in the Windows app is a semantic search over every
observation — caption + OCR text embedding *and* the screenshot image
embedding (shared retrieval space, so text queries match images too).
Describe what you half-remember ("code", "python project", "laptop prices"),
open the hit, and the modal shows the decrypted screenshot, its OCR text and
the page URL it came from. Same engine as demo.sql §18.

## Machine learning

| Decision | Baseline | ML (default) |
|---|---|---|
| Clustering observations | greedy centroid, 24 h | agglomerative, 30 days (`hdbscan` also available) |
| Is a cluster worth a memory? | always | gradient boosting on time-pattern features |
| Routines | none | Rayleigh test on time of day, `habit` memories |
| Forgetting | never | logistic regression, P(used in next 14 days), archives |
| Does this message need memory? | always | logistic regression gate |
| Memory ordering | fixed SQL weights | learned logistic regression (`cross_encoder` also available) |

`backend/app/config.py` selects each one (`CLUSTERER`, `IMPORTANCE`, `DECAY`, `HABITS`,
`GATE`, `RANKER`). Trained models live in `data/models/*.joblib`; a missing file falls back
to the baseline with a log warning. The clustering window needs `retention_days >= 30`.

**Train memories tab:** type example prompts, see which memories the app would pick, and
give each a thumbs up or down. The ranker retrains from your votes when you click
Finish, switch tabs, or restart the backend.

**Training data is simulated** (no labelled data existed): `scripts/synthetic/` generates
topic captions, queries and memories with OpenAI (flex tier, batch fallback) and simulates
90-day users with known ground truth. Reported numbers are therefore optimistic.

```bash
uv run python scripts/synthetic/generate.py        # OpenAI data (resumable; --check polls a batch job)
uv run python scripts/synthetic/embed_data.py      # embed it locally with the Jina model
uv run python scripts/train_models.py importance   # also: decay
uv run python scripts/train_retrieval.py gate      # also: ranker
uv run python scripts/evaluate.py                  # every component vs its baseline
```

Held-out results (simulated users): importance AUC 0.92 vs 0.86, clustering ARI 0.66 vs
0.33, gate accuracy 0.90 vs 0.25, ranker hit@1 0.92 vs 0.76, habit precision 0.97.
Full tables, caveats and pros/cons are in [ML.md](ML.md).

## SQL demo (DBMS concepts)

```bash
bash scripts/run-demo-sql.sh     # or: docker exec -i my-postgres psql -U harshith -d mydb < sql/demo.sql
```

17 numbered queries demonstrate: foreign keys (catalog + cascade), joins up to six
tables (incl. the `v_memory_evidence` view), filtering, aggregation, window
functions, `EXPLAIN ANALYZE` with the btree + HNSW indexes actually used,
`<=>` vector similarity, **cross-modal** text→image search, PostGIS geography distance, chat-query traceability (`query_memories` joins), the\n`updated_at` trigger, ciphertext-at-rest, and CRUD. (`sql/demo.sql` predates the ML\nchanges; check it for the exact list.)

## Schema

```
users ─┬─ browsing_events ──┐
       ├─ screenshots ──────┼─> observations (embedding + image_embedding) ─< observation_topics
       ├─ memories ─────────┼─< memory_observations >─ observations   (M:N evidence links)
       │    (source: auto|manual|seed|habit, archived_at)
       ├─ chat_queries ─────< query_memories >─ memories              (retrieval log)
       └─ training_prompts ─< memory_feedback >─ memories             (thumbs up/down)
```

The schema is kept in BCNF: derived data lives in views (`v_observations`,
`v_browsing_events`, `v_memory_usage`, `v_memory_topics`, `v_memory_evidence`) instead of
columns. `schema.sql` runs on every backend start, so it stays idempotent.

All foreign keys `ON DELETE CASCADE`; HNSW (`vector_cosine_ops`) indexes on the
three embedding columns; location is an
optional `geography(Point,4326)` column populated only when you enable it in
Settings (cached GeoIP, off by default).

## Privacy / encryption model (hobby grade, local only)

- Screenshot **pixels** are AES-256-GCM encrypted before touching disk
  (`data/screenshots/*.enc`); the key is `data/secret.key` (auto-generated).
- **OCR text** is encrypted into the `observations.ocr_encrypted` bytea column —
  the DB never stores raw screen text in plain sight (see demo.sql §15).
- Captions, memories and URLs stay readable **on purpose**: they are the data the
  SQL/DBMS demos operate on, and everything runs locally on your PC.
- Controls: Settings → tracking / screenshots / injection / location toggles,
  screenshot interval, retention days, and one-click clear (browsing /
  screenshots / memories / everything).

## Troubleshooting

- **Backend offline (extension popup / app sidebar)** — start uvicorn first; the
  model load takes ~10 s (`/api/status` shows `embed_ready`).
- **Screenshots stay "processing…"** — the captioning step needs a valid
  `OPENAI_API_KEY` in `.env`; embeddings/observations for seeds still work offline.
- **`docker exec` psql password** — the container trust config uses `harshith`;
  the connection string is in `.env` (`DATABASE_URL`).
- **Injection doesn't fire on a chat site** — chat UIs change often; selectors
  live at the top of `extension/content.js`. Gemini is best-effort.
- **Reset everything** — `uv run python scripts/seed_demo.py --reset`, or Settings →
  Delete everything.
- **Backend exits at startup** — MongoDB must be running (see setup step 1b).
- **Gate/ranker/importance seem inactive** — a missing `data/models/*.joblib` makes that
  component fall back to its baseline (see the backend log). Train it with the commands in
  the Machine learning section.
- **No new memories from a first-time search** — intended: the importance model keeps
  one-off activity "pending" until it recurs over several days.
- **Dashboard shows 0 screenshots** — capture is off if Settings → screenshots/tracking are
  disabled, and "24h" counts only the last day.
