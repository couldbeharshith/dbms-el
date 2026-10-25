"""Dashboard aggregates — pure SQL (GROUP BY, jsonb functions, subselects)."""

from fastapi import APIRouter, Request

from backend.app import db

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats")
async def stats(request: Request):
    user = request.app.state.user
    uid = user["id"]

    counts = db.query_one(
        """
        SELECT
          (SELECT count(*) FROM browsing_events
            WHERE user_id = %(u)s AND visited_at > now() - interval '24 hours') AS events_today,
          (SELECT count(*) FROM screenshots
            WHERE user_id = %(u)s AND captured_at > now() - interval '24 hours') AS screenshots_24h,
          (SELECT count(*) FROM observations WHERE user_id = %(u)s) AS observations,
          (SELECT count(*) FROM memories WHERE user_id = %(u)s) AS memories,
          (SELECT count(*) FROM chat_queries WHERE user_id = %(u)s) AS queries,
          (SELECT coalesce(sum(time_spent_sec), 0) FROM browsing_events
            WHERE user_id = %(u)s AND visited_at > now() - interval '24 hours') AS dwell_sec_24h
        """,
        {"u": uid},
    )

    top_domains = db.query(
        """
        SELECT domain, count(*) AS visits, sum(time_spent_sec) AS total_sec
        FROM browsing_events
        WHERE user_id = %s AND visited_at > now() - interval '7 days'
        GROUP BY domain
        ORDER BY total_sec DESC NULLS LAST
        LIMIT 8
        """,
        [uid],
    )

    hourly = db.query(
        """
        SELECT date_trunc('hour', visited_at) AS hour, count(*) AS events,
               coalesce(sum(time_spent_sec), 0) AS seconds
        FROM browsing_events
        WHERE user_id = %s AND visited_at > now() - interval '24 hours'
        GROUP BY 1 ORDER BY 1
        """,
        [uid],
    )

    recent_memories = db.query(
        """
        SELECT id, content, importance, updated_at, hit_count
        FROM memories WHERE user_id = %s
        ORDER BY updated_at DESC LIMIT 8
        """,
        [uid],
    )

    recent_events = db.query(
        """
        SELECT id, domain, title, time_spent_sec, visited_at
        FROM browsing_events WHERE user_id = %s
        ORDER BY visited_at DESC LIMIT 15
        """,
        [uid],
    )

    topics = db.query(
        """
        SELECT t.topic, count(*) AS uses
        FROM memories m, jsonb_array_elements_text(m.metadata->'topics') AS t(topic)
        WHERE m.user_id = %s
        GROUP BY t.topic
        ORDER BY uses DESC LIMIT 10
        """,
        [uid],
    )

    pipeline = db.query_one(
        """
        SELECT
          (SELECT max(captured_at) FROM screenshots WHERE user_id = %(u)s) AS last_screenshot,
          (SELECT max(observed_at) FROM observations WHERE user_id = %(u)s) AS last_observation,
          (SELECT max(updated_at) FROM memories WHERE user_id = %(u)s) AS last_memory,
          (SELECT max(created_at) FROM chat_queries WHERE user_id = %(u)s) AS last_query
        """,
        {"u": uid},
    )

    return {
        "counts": counts,
        "top_domains": top_domains,
        "hourly": hourly,
        "recent_memories": recent_memories,
        "recent_events": recent_events,
        "topics": topics,
        "pipeline": pipeline,
    }
