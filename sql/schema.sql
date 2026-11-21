-- ============================================================================
-- DBMS-EL — Personalized AI Memory Layer : PostgreSQL schema (3NF / BCNF)
-- Idempotent: safe to re-run on every backend startup. Also migrates databases
-- created with the earlier (denormalized) schema in place.
-- Extensions: pgvector (semantic search), PostGIS (optional geo), pgcrypto.
--
-- Normalization summary (every relation is in BCNF; each determinant is a key):
--   * users            : settings JSONB exploded into atomic columns
--   * browsing_events  : `domain` (url -> domain) removed; derived by url_domain()
--   * screenshots      : page on screen is an FK (browsing_event_id), not a copy
--   * observations     : kind / url / topics[] / metadata removed; topics live in
--                        observation_topics, kind+url are derived in v_observations
--   * memories         : hit_count / last_retrieved_at / metadata removed; usage is
--                        derived from query_memories (v_memory_usage)
--   * chat_queries     : retrieved_memory_ids JSONB -> query_memories (M:N)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Views are recreated at the bottom; drop first so columns can be migrated.
DROP VIEW IF EXISTS v_memory_evidence;
DROP VIEW IF EXISTS v_memory_usage;
DROP VIEW IF EXISTS v_memory_topics;
DROP VIEW IF EXISTS v_observations;
DROP VIEW IF EXISTS v_browsing_events;

-- Domain (hostname) of a URL. Replaces the stored browsing_events.domain column.
CREATE OR REPLACE FUNCTION url_domain(u TEXT) RETURNS TEXT AS $$
    SELECT lower(substring(u from '^[A-Za-z][A-Za-z0-9+.-]*://(?:[^/?#@]*@)?([^/:?#]+)'))
$$ LANGUAGE sql IMMUTABLE;

-- ----------------------------------------------------------------------------
-- users — single local user for this hobby deployment, but a proper table so
-- the relational design (and multi-user capability) is demonstrated.
-- Settings are atomic columns (1NF), not a JSON blob.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username                TEXT NOT NULL UNIQUE,
    email                   TEXT,
    tracking_enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    screenshots_enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    screenshot_interval_sec INTEGER NOT NULL DEFAULT 30 CHECK (screenshot_interval_sec BETWEEN 10 AND 600),
    location_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
    injection_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    retention_days          INTEGER NOT NULL DEFAULT 14 CHECK (retention_days BETWEEN 1 AND 365),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- browsing_events — one row per page visit, sent in batches by the Chrome
-- extension. location is only populated when the user enables it (GeoIP).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS browsing_events (
    id             BIGSERIAL PRIMARY KEY,
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url            TEXT NOT NULL,
    title          TEXT,
    visited_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    time_spent_sec INTEGER NOT NULL DEFAULT 0 CHECK (time_spent_sec >= 0),
    session_id     TEXT,
    location       GEOGRAPHY(Point, 4326)
);

-- ----------------------------------------------------------------------------
-- screenshots — captured by the Electron app. Pixels live in an AES-256-GCM
-- encrypted file on disk; this table holds metadata + the reference.
-- browsing_event_id = the page that was on screen (browser in foreground).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS screenshots (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    browsing_event_id BIGINT REFERENCES browsing_events(id) ON DELETE SET NULL,
    captured_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    file_path         TEXT NOT NULL,
    sha256            CHAR(64),
    phash             TEXT,
    size_bytes        INTEGER,
    width             INTEGER,
    height            INTEGER,
    source_app        TEXT,
    encrypted         BOOLEAN NOT NULL DEFAULT TRUE
);

-- ----------------------------------------------------------------------------
-- observations — what was understood about ONE screenshot or ONE browsing
-- event: caption, OCR text (encrypted bytea) and its embeddings in the shared
-- 1024-d Jina v5 text/image space. Exactly one source FK is set; each source
-- has at most one observation (UNIQUE), so each source FK is a candidate key.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS observations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    screenshot_id     UUID REFERENCES screenshots(id) ON DELETE CASCADE,
    browsing_event_id BIGINT REFERENCES browsing_events(id) ON DELETE CASCADE,
    caption           TEXT,
    ocr_encrypted     BYTEA,
    importance_hint   REAL CHECK (importance_hint >= 0 AND importance_hint <= 1),
    observed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding         vector(1024),
    image_embedding   vector(1024)
);

-- Topic tags of an observation (was observations.topics TEXT[]).
CREATE TABLE IF NOT EXISTS observation_topics (
    observation_id UUID NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    topic          TEXT NOT NULL CHECK (length(btrim(topic)) > 0),
    PRIMARY KEY (observation_id, topic)
);

-- ----------------------------------------------------------------------------
-- memories — consolidated, higher-level facts with importance. Each memory
-- links back to its evidence (observations) via memory_observations.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    embedding   vector(1024) NOT NULL,
    importance  REAL NOT NULL DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
    source      TEXT NOT NULL DEFAULT 'auto' CHECK (source IN ('auto', 'manual', 'seed', 'habit')),
    archived_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- M:N evidence link: a memory traces back to the observations that produced it.
CREATE TABLE IF NOT EXISTS memory_observations (
    memory_id      UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    observation_id UUID NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    contribution   REAL NOT NULL DEFAULT 1.0,
    linked_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, observation_id)
);

-- ----------------------------------------------------------------------------
-- chat_queries — every chatbot query that asked for memory context, with its
-- embedding. Retrieved memories are rows of query_memories (M:N).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_queries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query_text      TEXT NOT NULL,
    query_embedding vector(1024) NOT NULL,
    site            TEXT,
    context_text    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS query_memories (
    query_id   UUID NOT NULL REFERENCES chat_queries(id) ON DELETE CASCADE,
    memory_id  UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    rank       SMALLINT NOT NULL,
    similarity REAL,
    PRIMARY KEY (query_id, memory_id)
);

-- ============================================================================
-- In-place migration from the earlier denormalized schema (no-ops on fresh DBs)
-- ============================================================================

-- ML additions: memories can be archived (decay model) and can be habits
ALTER TABLE memories ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
ALTER TABLE memories DROP CONSTRAINT IF EXISTS memories_source_check;
ALTER TABLE memories ADD CONSTRAINT memories_source_check
    CHECK (source IN ('auto', 'manual', 'seed', 'habit'));
-- "Train memories" tab: example prompts and the user's thumbs up/down per retrieved memory
CREATE TABLE IF NOT EXISTS training_prompts (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    embedding  vector(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS memory_feedback (
    prompt_id UUID NOT NULL REFERENCES training_prompts(id) ON DELETE CASCADE,
    memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    vote      SMALLINT NOT NULL CHECK (vote IN (-1, 1)),
    voted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (prompt_id, memory_id)
);
-- users.settings JSONB -> atomic columns
ALTER TABLE users ADD COLUMN IF NOT EXISTS tracking_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS screenshots_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS screenshot_interval_sec INTEGER NOT NULL DEFAULT 30
    CHECK (screenshot_interval_sec BETWEEN 10 AND 600);
ALTER TABLE users ADD COLUMN IF NOT EXISTS location_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS injection_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS retention_days INTEGER NOT NULL DEFAULT 14
    CHECK (retention_days BETWEEN 1 AND 365);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'users' AND column_name = 'settings') THEN
        UPDATE users SET
            tracking_enabled        = COALESCE((settings->>'tracking_enabled')::boolean, TRUE),
            screenshots_enabled     = COALESCE((settings->>'screenshots_enabled')::boolean, TRUE),
            screenshot_interval_sec = GREATEST(10, LEAST(600, COALESCE((settings->>'screenshot_interval_sec')::int, 30))),
            location_enabled        = COALESCE((settings->>'location_enabled')::boolean, FALSE),
            injection_enabled       = COALESCE((settings->>'injection_enabled')::boolean, TRUE),
            retention_days          = GREATEST(1, LEAST(365, COALESCE((settings->>'retention_days')::int, 14)));
    END IF;
END $$;
ALTER TABLE users DROP COLUMN IF EXISTS settings;
ALTER TABLE users DROP COLUMN IF EXISTS preferences;

-- browsing_events: domain is derived from url; metadata JSONB dropped
ALTER TABLE browsing_events DROP COLUMN IF EXISTS domain;
ALTER TABLE browsing_events DROP COLUMN IF EXISTS metadata;

-- screenshots: link to the page on screen; phash column from older versions
ALTER TABLE screenshots ADD COLUMN IF NOT EXISTS phash TEXT;
ALTER TABLE screenshots ALTER COLUMN phash TYPE TEXT;
ALTER TABLE screenshots ADD COLUMN IF NOT EXISTS browsing_event_id BIGINT
    REFERENCES browsing_events(id) ON DELETE SET NULL;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'observations' AND column_name = 'url') THEN
        UPDATE screenshots s SET browsing_event_id = (
            SELECT b.id FROM browsing_events b
            WHERE b.user_id = s.user_id AND b.url = o.url AND b.visited_at <= s.captured_at
            ORDER BY b.visited_at DESC LIMIT 1)
        FROM observations o
        WHERE o.screenshot_id = s.id AND o.url IS NOT NULL AND s.browsing_event_id IS NULL;
    END IF;
END $$;
ALTER TABLE screenshots DROP COLUMN IF EXISTS metadata;

-- observations: topics[] -> observation_topics; kind/url/metadata dropped
ALTER TABLE observations ADD COLUMN IF NOT EXISTS importance_hint REAL
    CHECK (importance_hint >= 0 AND importance_hint <= 1);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'observations' AND column_name = 'topics') THEN
        INSERT INTO observation_topics (observation_id, topic)
        SELECT DISTINCT o.id, btrim(t.topic)
        FROM observations o, unnest(o.topics) AS t(topic)
        WHERE length(btrim(t.topic)) > 0
        ON CONFLICT DO NOTHING;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'observations' AND column_name = 'metadata') THEN
        UPDATE observations SET importance_hint =
            GREATEST(0, LEAST(1, (metadata->>'importance_hint')::real))
        WHERE metadata ? 'importance_hint' AND importance_hint IS NULL;
    END IF;
END $$;
ALTER TABLE observations DROP COLUMN IF EXISTS topics;
ALTER TABLE observations DROP COLUMN IF EXISTS metadata;
ALTER TABLE observations DROP COLUMN IF EXISTS url;
ALTER TABLE observations DROP COLUMN IF EXISTS kind;   -- also drops obs_single_source

-- at most one observation per source (rare duplicates are removed first)
DELETE FROM observations a USING observations b
 WHERE a.screenshot_id = b.screenshot_id AND a.ctid < b.ctid;
DELETE FROM observations a USING observations b
 WHERE a.browsing_event_id = b.browsing_event_id AND a.ctid < b.ctid;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'obs_exactly_one_source') THEN
        ALTER TABLE observations ADD CONSTRAINT obs_exactly_one_source
            CHECK ((screenshot_id IS NOT NULL) <> (browsing_event_id IS NOT NULL));
    END IF;
END $$;

-- memories: metadata -> source column; usage stats derived from query_memories
ALTER TABLE memories ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'auto'
    CHECK (source IN ('auto', 'manual', 'seed'));
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'memories' AND column_name = 'metadata') THEN
        UPDATE memories SET source = 'seed' WHERE metadata->>'source' = 'seed';
    END IF;
    -- chat_queries.retrieved_memory_ids JSONB -> query_memories rows
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'chat_queries' AND column_name = 'retrieved_memory_ids') THEN
        INSERT INTO query_memories (query_id, memory_id, rank)
        SELECT q.id, m.id, r.ord::smallint
        FROM chat_queries q
        CROSS JOIN LATERAL jsonb_array_elements_text(q.retrieved_memory_ids)
                           WITH ORDINALITY AS r(mid, ord)
        JOIN memories m ON m.id = r.mid::uuid
        ON CONFLICT DO NOTHING;
    END IF;
END $$;
ALTER TABLE memories     DROP COLUMN IF EXISTS metadata;
ALTER TABLE memories     DROP COLUMN IF EXISTS hit_count;
ALTER TABLE memories     DROP COLUMN IF EXISTS last_retrieved_at;
ALTER TABLE chat_queries DROP COLUMN IF EXISTS retrieved_memory_ids;

-- A reasoning-model bug once stored empty memories; the DB now rejects them.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'memories_content_nonempty') THEN
        ALTER TABLE memories ADD CONSTRAINT memories_content_nonempty
            CHECK (length(btrim(content)) > 0);
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_browsing_events_user_visited
    ON browsing_events (user_id, visited_at DESC);
CREATE INDEX IF NOT EXISTS idx_browsing_events_domain
    ON browsing_events (url_domain(url));

CREATE INDEX IF NOT EXISTS idx_screenshots_user_captured
    ON screenshots (user_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_screenshots_user_sha
    ON screenshots (user_id, sha256);
CREATE INDEX IF NOT EXISTS idx_screenshots_browsing_event
    ON screenshots (browsing_event_id);

CREATE INDEX IF NOT EXISTS idx_observations_user_observed
    ON observations (user_id, observed_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_observations_screenshot
    ON observations (screenshot_id) WHERE screenshot_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_observations_browsing_event
    ON observations (browsing_event_id) WHERE browsing_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_observations_embedding
    ON observations USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_observations_image_embedding
    ON observations USING hnsw (image_embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_observation_topics_topic
    ON observation_topics (topic);

CREATE INDEX IF NOT EXISTS idx_memories_user_updated
    ON memories (user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_memory_observations_obs
    ON memory_observations (observation_id);

CREATE INDEX IF NOT EXISTS idx_chat_queries_user_created
    ON chat_queries (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_query_memories_memory
    ON query_memories (memory_id);

-- ----------------------------------------------------------------------------
-- updated_at trigger
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

-- ----------------------------------------------------------------------------
-- Views: derived attributes live here instead of being stored (keeps BCNF)
-- ----------------------------------------------------------------------------
CREATE VIEW v_browsing_events AS
SELECT b.*, url_domain(b.url) AS domain
FROM browsing_events b;

-- observation + derived kind, page url, and topic array
CREATE VIEW v_observations AS
SELECT o.id, o.user_id, o.screenshot_id, o.browsing_event_id,
       CASE WHEN o.screenshot_id IS NOT NULL THEN 'screenshot' ELSE 'browsing' END AS kind,
       o.caption, o.ocr_encrypted, o.importance_hint, o.observed_at,
       o.embedding, o.image_embedding,
       COALESCE(be.url, sb.url) AS url,
       COALESCE((SELECT array_agg(t.topic ORDER BY t.topic)
                 FROM observation_topics t WHERE t.observation_id = o.id), '{}') AS topics
FROM observations o
LEFT JOIN screenshots s       ON s.id  = o.screenshot_id
LEFT JOIN browsing_events sb  ON sb.id = s.browsing_event_id
LEFT JOIN browsing_events be  ON be.id = o.browsing_event_id;

-- per-memory retrieval usage, derived from the query log
CREATE VIEW v_memory_usage AS
SELECT m.id AS memory_id,
       count(qm.query_id)  AS hit_count,
       max(q.created_at)   AS last_retrieved_at
FROM memories m
LEFT JOIN query_memories qm ON qm.memory_id = m.id
LEFT JOIN chat_queries q    ON q.id = qm.query_id
GROUP BY m.id;

-- per-memory topics, derived from its evidence (uses = evidence count)
CREATE VIEW v_memory_topics AS
SELECT mo.memory_id, ot.topic, count(*) AS uses
FROM memory_observations mo
JOIN observation_topics ot ON ot.observation_id = mo.observation_id
GROUP BY mo.memory_id, ot.topic;

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
JOIN v_observations o       ON o.id = mo.observation_id
LEFT JOIN screenshots s     ON s.id = o.screenshot_id
LEFT JOIN v_browsing_events b ON b.id = COALESCE(o.browsing_event_id, s.browsing_event_id);
