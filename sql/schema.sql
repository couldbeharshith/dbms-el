-- ============================================================================
-- DBMS-EL — Personalized AI Memory Layer : PostgreSQL schema
-- Idempotent: safe to re-run on every backend startup.
-- Extensions: pgvector (semantic search), PostGIS (optional geo), pgcrypto.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ----------------------------------------------------------------------------
-- users — single local user for this hobby deployment, but a proper table so
-- the relational design (and multi-user capability) is demonstrated.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username    TEXT NOT NULL UNIQUE,
    email       TEXT,
    settings    JSONB NOT NULL DEFAULT '{"tracking_enabled": true, "screenshots_enabled": true, "screenshot_interval_sec": 30, "location_enabled": false, "injection_enabled": true, "retention_days": 14}'::jsonb,
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- browsing_events — one row per page visit, sent in batches by the Chrome
-- extension. location is only populated when the user enables it (GeoIP).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS browsing_events (
    id             BIGSERIAL PRIMARY KEY,
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url            TEXT NOT NULL,
    domain         TEXT NOT NULL,
    title          TEXT,
    visited_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    time_spent_sec INTEGER NOT NULL DEFAULT 0 CHECK (time_spent_sec >= 0),
    session_id     TEXT,
    location       GEOGRAPHY(Point, 4326),
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_browsing_events_user_visited
    ON browsing_events (user_id, visited_at DESC);
CREATE INDEX IF NOT EXISTS idx_browsing_events_domain
    ON browsing_events (domain);
CREATE INDEX IF NOT EXISTS idx_browsing_events_metadata
    ON browsing_events USING GIN (metadata jsonb_path_ops);

-- ----------------------------------------------------------------------------
-- screenshots — captured by the Electron app. Pixels live in an AES-256-GCM
-- encrypted file on disk; this table holds metadata + the reference.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS screenshots (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    file_path   TEXT NOT NULL,
    sha256      CHAR(64),
    size_bytes  INTEGER,
    width       INTEGER,
    height      INTEGER,
    source_app  TEXT,
    encrypted   BOOLEAN NOT NULL DEFAULT TRUE,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_screenshots_user_captured
    ON screenshots (user_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_screenshots_user_sha
    ON screenshots (user_id, sha256);

-- ----------------------------------------------------------------------------
-- observations — what was understood about one screenshot or browsing event:
-- caption (gpt-6-luna), OCR text (encrypted bytea), topic tags, and its
-- embedding in the shared 1024-d Jina v5 text/image space. Exactly one of
-- screenshot_id / browsing_event_id must be set, matching kind.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS observations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    screenshot_id     UUID REFERENCES screenshots(id) ON DELETE CASCADE,
    browsing_event_id BIGINT REFERENCES browsing_events(id) ON DELETE CASCADE,
    kind              TEXT NOT NULL CHECK (kind IN ('screenshot', 'browsing')),
    caption           TEXT,
    ocr_encrypted     BYTEA,
    topics            TEXT[] NOT NULL DEFAULT '{}',
    observed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding         vector(1024),
    image_embedding   vector(1024),
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT obs_single_source CHECK (
        (kind = 'screenshot' AND screenshot_id IS NOT NULL AND browsing_event_id IS NULL) OR
        (kind = 'browsing'   AND browsing_event_id IS NOT NULL AND screenshot_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_observations_user_observed
    ON observations (user_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_observations_embedding
    ON observations USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_observations_image_embedding
    ON observations USING hnsw (image_embedding vector_cosine_ops);

-- ----------------------------------------------------------------------------
-- memories — consolidated, higher-level facts with importance. Each memory
-- links back to its evidence (observations) via memory_observations.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content            TEXT NOT NULL,
    embedding          vector(1024) NOT NULL,
    importance         REAL NOT NULL DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
    hit_count          INTEGER NOT NULL DEFAULT 0,
    last_retrieved_at  TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_memories_user_updated
    ON memories (user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON memories USING hnsw (embedding vector_cosine_ops);

-- M:N evidence link: a memory traces back to the observations that produced it.
CREATE TABLE IF NOT EXISTS memory_observations (
    memory_id      UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    observation_id UUID NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    contribution   REAL NOT NULL DEFAULT 1.0,
    linked_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, observation_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_observations_obs
    ON memory_observations (observation_id);

-- ----------------------------------------------------------------------------
-- chat_queries — every chatbot query that asked for memory context, with its
-- embedding and which memories were retrieved (JSONB) for full traceability.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_queries (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query_text           TEXT NOT NULL,
    query_embedding      vector(1024) NOT NULL,
    site                 TEXT,
    context_text         TEXT,
    retrieved_memory_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_queries_user_created
    ON chat_queries (user_id, created_at DESC);

-- ----------------------------------------------------------------------------
-- Idempotent column additions (tables above may predate these features)
-- ----------------------------------------------------------------------------
ALTER TABLE observations ADD COLUMN IF NOT EXISTS url TEXT;
ALTER TABLE screenshots  ADD COLUMN IF NOT EXISTS phash TEXT;
ALTER TABLE screenshots  ALTER COLUMN phash TYPE TEXT;  -- may predate as CHAR(16)

-- ----------------------------------------------------------------------------
-- updated_at trigger + memory-evidence view
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_touch_updated_at ON users;
CREATE TRIGGER trg_users_touch_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

DROP TRIGGER IF EXISTS trg_memories_touch_updated_at ON memories;
CREATE TRIGGER trg_memories_touch_updated_at BEFORE UPDATE ON memories
    FOR EACH ROW
    WHEN (OLD.content IS DISTINCT FROM NEW.content OR OLD.importance IS DISTINCT FROM NEW.importance)
    EXECUTE FUNCTION touch_updated_at();

-- dropped+recreated (not CREATE OR REPLACE) so new columns may be inserted freely
DROP VIEW IF EXISTS v_memory_evidence;
CREATE VIEW v_memory_evidence AS
SELECT
    m.id                AS memory_id,
    m.content           AS memory_content,
    m.importance        AS importance,
    mo.contribution     AS contribution,
    o.id                AS observation_id,
    o.kind              AS observation_kind,
    o.caption           AS observation_caption,
    o.url               AS observation_url,
    o.observed_at       AS observed_at,
    s.id                AS screenshot_id,
    s.captured_at       AS screenshot_captured_at,
    s.source_app        AS source_app,
    b.id                AS browsing_event_id,
    b.domain            AS domain,
    b.url               AS url,
    b.title             AS page_title,
    b.time_spent_sec    AS time_spent_sec
FROM memories m
JOIN memory_observations mo ON mo.memory_id = m.id
JOIN observations o         ON o.id = mo.observation_id
LEFT JOIN screenshots s     ON s.id = o.screenshot_id
LEFT JOIN browsing_events b ON b.id = o.browsing_event_id;
