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
                                     PostgreSQL 17 (docker, pgvector + PostGIS + JSONB)
                                                                          v
                     Screenshot ──> gpt-6-luna caption/OCR ──> observation ──> embedding
                     (local GPU: jinaai/jina-embeddings-v5-omni-small, 1024-d)          │
                                                                                       v
                     repeated activity ──> memory (consolidation, cosine clustering) ──┘
                                                                          v
                     chat query ──> pgvector similarity + importance + recency
                                ──> context appended after your message, then send
```

## Components

| Folder         | What it is                                                                    |
|----------------|-------------------------------------------------------------------------------|
| `backend/`     | FastAPI app: schema, pipeline, consolidation, retrieval, encryption           |
| `extension/`   | Chrome MV3 extension: dwell-time tracking + chat context injection            |
| `desktop/`     | Electron + React + Tailwind app: screenshots + Dashboard/History/Memories/Settings |
| `sql/`         | `schema.sql` (DDL) and `demo.sql` (17 demonstrable DBMS queries)              |
| `docker/`      | Dockerfile for `postgres:17 + pgvector + PostGIS`                             |
| `scripts/`     | `setup-db.sh` (rebuild container), `seed_demo.py` (instant demo data)         |
| `data/`        | runtime artifacts (gitignored): `secret.key`, encrypted screenshots, HF cache |

## One-time setup

```bash
# 1. Database — rebuilds my-postgres with pgvector+PostGIS (same name/creds/volume;
#    existing data survives). Docker Desktop must be running.
bash scripts/setup-db.sh

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
   last 24 h of observations by cosine similarity (≥ 0.78; ≥ 3 observations per
   cluster) and creates/merges memories, linking evidence rows.
6. Open a fresh chat on **chatgpt.com** (or claude.ai / gemini) and type
   `Which laptop should I buy?` then hit send.
7. The extension calls `POST /api/context`, retrieves top memories
   (score = `0.6·cosine + 0.25·importance + 0.15·recency`), appends the context
   block **after your text**, and sends. A toast confirms: `🧠 DBMS-EL: injected`.
8. The chatbot answers using your previously captured research.

## Recall (search your own screen history)

The **Recall** page in the Windows app is a semantic search over every
observation — caption + OCR text embedding *and* the screenshot image
embedding (shared retrieval space, so text queries match images too).
Describe what you half-remember ("code", "python project", "laptop prices"),
open the hit, and the modal shows the decrypted screenshot, its OCR text and
the page URL it came from. Same engine as demo.sql §18.

## SQL demo (DBMS concepts)

```bash
bash scripts/run-demo-sql.sh     # or: docker exec -i my-postgres psql -U harshith -d mydb < sql/demo.sql
```

17 numbered queries demonstrate: foreign keys (catalog + cascade), joins up to six
tables (incl. the `v_memory_evidence` view), filtering, aggregation, window
functions, `EXPLAIN ANALYZE` with the btree + HNSW indexes actually used,
`<=>` vector similarity, **cross-modal** text→image search, JSONB containment /
extraction / array unnesting, PostGIS geography distance, chat-query
traceability (JSONB → join), the `updated_at` trigger, ciphertext-at-rest, and
CRUD.

## Schema

```
users ─┬─ browsing_events ──┐
       ├─ screenshots ──────┼─> observations (kind: screenshot|browsing, embedding + image_embedding, url)
       ├─ memories ─────────┼─< memory_observations >─ observations   (M:N evidence links)
       └─ chat_queries      (query embedding + retrieved memory ids in JSONB)
```

All foreign keys `ON DELETE CASCADE`; HNSW (`vector_cosine_ops`) indexes on the
three embedding columns; GIN index on `browsing_events.metadata`; location is an
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
