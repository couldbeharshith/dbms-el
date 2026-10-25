-- ============================================================================
-- DBMS-EL — demonstrable SQL queries (DBMS concepts per spec §13/§14)
-- Run:  docker exec -i my-postgres psql -U harshith -d mydb < sql/demo.sql
-- Assumes seed data:  uv run python scripts/seed_demo.py
-- ============================================================================

\echo == 1. FOREIGN KEY RELATIONSHIPS (catalog) ==
SELECT conrelid::regclass AS child_table,
       confrelid::regclass AS parent_table,
       conname             AS fk_constraint
FROM   pg_constraint
WHERE  contype = 'f'
ORDER  BY 1, 2;

\echo == 2. SIMPLE JOIN — events with their user ==
SELECT u.username, b.domain, b.title, b.time_spent_sec, b.visited_at
FROM   browsing_events b
JOIN   users u ON u.id = b.user_id
ORDER  BY b.visited_at DESC
LIMIT  5;

\echo == 3. SIX-TABLE JOIN via v_memory_evidence view (memory -> evidence) ==
SELECT memory_content, observation_kind, observation_caption,
       COALESCE(source_app, domain) AS evidence_source
FROM   v_memory_evidence
LIMIT  12;

\echo == 4. FILTERING — Amazon pages with real engagement (>60s dwell) ==
SELECT title, time_spent_sec, visited_at
FROM   browsing_events
WHERE  domain = 'www.amazon.in'
  AND  time_spent_sec > 60
ORDER  BY time_spent_sec DESC;

\echo == 5. AGGREGATION — top domains by total dwell time (7 days) ==
SELECT domain,
       count(*)                       AS visits,
       sum(time_spent_sec)            AS total_sec,
       round(avg(time_spent_sec), 1)  AS avg_sec
FROM   browsing_events
WHERE  visited_at > now() - interval '7 days'
GROUP  BY domain
HAVING count(*) >= 1
ORDER  BY total_sec DESC;

\echo == 6. WINDOW FUNCTION — rank pages inside each domain by dwell ==
SELECT DISTINCT ON (domain)
       domain,
       title,
       time_spent_sec,
       rank() OVER (PARTITION BY domain ORDER BY time_spent_sec DESC) AS dwell_rank
FROM   browsing_events
ORDER  BY domain, dwell_rank
LIMIT  10;

\echo == 7. INDEX IN ACTION — planner picks Seq Scan on a tiny table, but the index is there and used when it matters ==
EXPLAIN ANALYZE
SELECT * FROM browsing_events WHERE domain = 'www.amazon.in';

\echo '--- same query with enable_seqscan=off proves the btree index answers it:'
BEGIN;
SET LOCAL enable_seqscan = off;
EXPLAIN ANALYZE
SELECT * FROM browsing_events WHERE domain = 'www.amazon.in';
ROLLBACK;

\echo '--- HNSW vector index in action (similarity search uses idx_memories_embedding):'
BEGIN;
SET LOCAL enable_seqscan = off;
EXPLAIN ANALYZE
SELECT id, content FROM memories
ORDER BY embedding <=> (SELECT embedding FROM memories WHERE content ILIKE '%budget%' LIMIT 1)
LIMIT 3;
ROLLBACK;

\echo == 8. VECTOR SIMILARITY (<=>) — find memories closest to the 'laptop' memory ==
WITH probe AS (
    SELECT embedding, content FROM memories WHERE content ILIKE '%laptop%' LIMIT 1
)
SELECT m.content,
       round((1 - (m.embedding <=> probe.embedding))::numeric, 4) AS cosine_sim
FROM   memories m CROSS JOIN probe
ORDER  BY m.embedding <=> probe.embedding;

\echo == 9. CROSS-MODAL SEARCH — text memory embedding vs screenshot IMAGE embeddings ==
WITH probe AS (
    SELECT embedding FROM memories WHERE content ILIKE '%laptop%' LIMIT 1
)
SELECT s.source_app, o.caption,
       round((1 - (o.image_embedding <=> probe.embedding))::numeric, 4) AS cross_modal_sim
FROM   observations o
JOIN   screenshots s ON s.id = o.screenshot_id
CROSS  JOIN probe
WHERE  o.image_embedding IS NOT NULL
ORDER  BY o.image_embedding <=> probe.embedding
LIMIT  5;

\echo == 10. JSONB — containment and field extraction on metadata ==
SELECT count(*) AS seed_rows
FROM   browsing_events
WHERE  metadata @> '{"seed": true}';

SELECT metadata->>'seed'   AS seed,
       metadata->'tab_id'  AS tab_id,
       count(*)
FROM   browsing_events
GROUP  BY 1, 2
ORDER  BY 3 DESC
LIMIT  10;

\echo == 11. JSONB — memory topics unnested and counted ==
SELECT topic, count(*) AS memories_using
FROM   memories m,
       jsonb_array_elements_text(m.metadata->'topics') AS t(topic)
GROUP  BY topic
ORDER  BY memories_using DESC, topic;

\echo == 12. POSTGIS — geography points and great-circle distance ==
-- If location tracking was enabled, browsing_events.location holds a WGS84 point.
SELECT domain, ST_AsText(location) AS wkt
FROM   browsing_events
WHERE  location IS NOT NULL
LIMIT  5;

-- Distance between two coordinates as geography (meters), no data needed:
SELECT round((ST_Distance(
         ST_SetSRID(ST_MakePoint(77.5946, 12.9716), 4326)::geography,  -- Bengaluru
         ST_SetSRID(ST_MakePoint(78.4867, 17.3850), 4326)::geography   -- Hyderabad
       ) / 1000)::numeric, 1) AS km_between_cities;

\echo == 13. TRACEABILITY — what did the last chat query retrieve? ==
SELECT q.query_text,
       q.site,
       m.content  AS retrieved_memory,
       round((1 - (m.embedding <=> q.query_embedding))::numeric, 4) AS sim_at_retrieval
FROM   chat_queries q
JOIN   LATERAL jsonb_array_elements_text(q.retrieved_memory_ids) AS rid(id)
       ON true
JOIN   memories m ON m.id = rid.id::uuid
ORDER  BY q.created_at DESC
LIMIT  10;

\echo == 14. TRIGGER — a real content change automatically bumps updated_at (no-op updates are ignored) ==
SELECT id, updated_at FROM memories ORDER BY created_at LIMIT 1;

UPDATE memories
SET    content = content || ' (demo edit)'
WHERE  id = (SELECT id FROM memories ORDER BY created_at LIMIT 1);

SELECT id, updated_at FROM memories ORDER BY created_at LIMIT 1;  -- bumped

UPDATE memories  -- restore
SET    content = regexp_replace(content, ' \(demo edit\)$', '')
WHERE  id = (SELECT id FROM memories ORDER BY created_at LIMIT 1);

\echo == 15. ENCRYPTION AT REST — OCR text is ciphertext in the DB, not readable ==
SELECT left(encode(ocr_encrypted, 'hex'), 48) || '…' AS ocr_ciphertext_hex
FROM   observations
WHERE  ocr_encrypted IS NOT NULL
LIMIT  2;

\echo == 16. CRUD DEMO — insert, update, delete a throwaway event ==
INSERT INTO browsing_events (user_id, url, domain, title, time_spent_sec, session_id, metadata)
VALUES ((SELECT id FROM users LIMIT 1),
        'https://example.com/crud-demo', 'example.com', 'CRUD demo row', 12, 'sql-demo',
        '{"demo": true}')
RETURNING id AS inserted_id;

UPDATE browsing_events
SET    time_spent_sec = 99
WHERE  session_id = 'sql-demo'
RETURNING id, time_spent_sec;

DELETE FROM browsing_events WHERE session_id = 'sql-demo';

\echo == 17. CASCADE — deleting a screenshot removes its observations (FK ON DELETE CASCADE) ==
INSERT INTO screenshots (id, user_id, file_path, size_bytes)
VALUES ('11111111-1111-1111-1111-111111111111'::uuid,
        (SELECT id FROM users LIMIT 1), 'screenshots/cascade-demo.enc', 0);

INSERT INTO observations (user_id, screenshot_id, kind, caption, embedding)
SELECT (SELECT id FROM users LIMIT 1),
       '11111111-1111-1111-1111-111111111111'::uuid,
       'screenshot', 'cascade demo', embedding
FROM   observations
WHERE  embedding IS NOT NULL
LIMIT  1;

SELECT count(*) AS obs_before_delete FROM observations WHERE caption = 'cascade demo';

DELETE FROM screenshots WHERE id = '11111111-1111-1111-1111-111111111111'::uuid;

SELECT count(*) AS obs_remaining_after_screenshot_delete
FROM   observations
WHERE  caption = 'cascade demo';

\echo == 18. RECALL — semantic search over observations (text vs text + cross-modal) ==
WITH probe AS (SELECT embedding FROM memories WHERE content ILIKE '%laptop%' LIMIT 1)
SELECT o.kind,
       left(o.caption, 55)                                  AS caption,
       s.source_app,
       o.url,
       round((1 - (o.embedding <=> probe.embedding))::numeric, 4)         AS text_sim,
       round(coalesce(1 - (o.image_embedding <=> probe.embedding), 0)::numeric, 4) AS image_sim
FROM   observations o
LEFT   JOIN screenshots s ON s.id = o.screenshot_id
CROSS  JOIN probe
WHERE  o.embedding IS NOT NULL
ORDER  BY GREATEST(1 - (o.embedding <=> probe.embedding),
                   coalesce(1 - (o.image_embedding <=> probe.embedding), 0)) DESC
LIMIT  5;

\echo == done ==
