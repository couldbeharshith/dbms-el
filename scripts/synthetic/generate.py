"""Generate synthetic training data with OpenAI (flex tier, batch fallback).

Jobs (each is one big call, auto-split in half if the output gets truncated):
  captions          screenshot-style observations per topic  -> data/synthetic/captions.json
  queries_personal  chat queries that need memories          \
  queries_generic   chat queries that need no memory          > data/synthetic/queries.json
  queries_tricky    word-overlap queries that need no memory /
  memories          one consolidated memory text per topic (needs captions.json first) -> memories.json

Every chunk retries flex forever with backoff. After BATCH_AFTER consecutive flex
failures a Batch API job is also submitted (50% off, up to 24 h); whichever
finishes first wins. State lives in data/synthetic/state.json so the script can
be stopped and re-run.

    uv run python scripts/synthetic/generate.py                 run / resume the loop
    uv run python scripts/synthetic/generate.py --check         poll batches, print status
    uv run python scripts/synthetic/generate.py --import-batch results.jsonl
"""

import argparse
import json
import logging
import os
import random
import sys
import threading
import time
from pathlib import Path

import openai
from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent))
from catalog import TOPIC_IDS, catalog_lines  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
OUT = ROOT / "data" / "synthetic"
RESULTS = OUT / "results"
BATCH_INPUTS = OUT / "batch_inputs"
STATE_FILE = OUT / "state.json"
for d in (RESULTS, BATCH_INPUTS):
    d.mkdir(parents=True, exist_ok=True)

MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
BATCH_AFTER = 6
REQUEST_TIMEOUT = 900
BACKOFF_START, BACKOFF_MAX = 30, 300
OBS_PER_TOPIC = 12
QUERIES_PER_TOPIC = 7
GENERIC_QUERIES, TRICKY_QUERIES = 120, 80

log = logging.getLogger("gen")
state_lock = threading.RLock()
threads: list[threading.Thread] = []
args: argparse.Namespace
client: OpenAI


# ---------------------------------------------------------------- state ----
def load_state() -> dict:
    with state_lock:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
        return {"chunks": {}}


def update_chunk(chunk_id: str, **fields) -> dict:
    with state_lock:
        state = load_state()
        state["chunks"][chunk_id].update(fields)
        STATE_FILE.write_text(json.dumps(state, indent=1))
        return state["chunks"][chunk_id]


def add_chunk(chunk: dict) -> None:
    with state_lock:
        state = load_state()
        state["chunks"].setdefault(chunk["id"], {**chunk, "status": "pending", "batch_id": None})
        STATE_FILE.write_text(json.dumps(state, indent=1))


def get_chunk(chunk_id: str) -> dict:
    return load_state()["chunks"][chunk_id]


# -------------------------------------------------------------- prompts ----
SYSTEM = ("You generate realistic synthetic data for a personal-memory app used by one "
          "Indian college student. Output valid JSON only, no commentary.")


def build_messages(chunk: dict) -> list[dict]:
    job, ids = chunk["job"], chunk.get("topic_ids")
    if job == "captions":
        prompt = (
            f"For EACH topic below write {OBS_PER_TOPIC} different screenshot observations, as a "
            "vision model captioning one screenshot of the user's screen would. Observations of "
            "one topic must differ (different page, product, article, step, site) but clearly "
            "belong to the topic. Use concrete names, prices and numbers (INR or USD).\n"
            "Fields per observation: window_title (OS window title ending in the browser or app "
            "name), caption (1-2 sentences on what the user is viewing or doing), ocr_text "
            "(key visible text, under 40 words), tags (1-4 short lowercase tags), "
            "importance_hint (0.0-1.0, how meaningful this ONE screenshot looks when judged "
            "alone: 0 = idle or generic, 1 = clearly meaningful research or a decision).\n\n"
            f"Topics:\n{catalog_lines(ids)}\n\n"
            'Return {"topics": [{"id": "<topic id>", "observations": [{"window_title": "", '
            '"caption": "", "ocr_text": "", "tags": [], "importance_hint": 0.0}]}]}'
        )
    elif job == "queries_personal":
        prompt = (
            f"For EACH topic below write {QUERIES_PER_TOPIC} messages the user might send to a "
            "chatbot where knowing their remembered activity on that topic would give a more "
            "personalised answer (e.g. 'which one should I buy', 'continue my plan', 'what "
            "did I decide'). Some mention the topic explicitly, some only implicitly. Make about "
            "1 in 7 queries involve two topics (list both ids). Vary length and style.\n\n"
            f"Topics:\n{catalog_lines(ids)}\n\n"
            'Return {"queries": [{"text": "", "relevant": ["<topic id>", ...]}]}'
        )
    elif job == "queries_generic":
        prompt = (
            f"Write {chunk['n']} diverse messages a person might send to a chatbot that need NO "
            "personal context: maths, general knowledge, coding syntax, grammar, translations, "
            "writing generic emails, definitions. Avoid anything related to these user "
            f"topics:\n{catalog_lines()}\n\n"
            'Return {"queries": [{"text": ""}]}'
        )
    elif job == "memories":
        captions = json.loads((OUT / "captions.json").read_text())
        blocks = "\n\n".join(
            f"[{tid}]\n" + "\n".join(f"- {o['caption']}" for o in captions[tid][:6])
            for tid in ids)
        prompt = (
            "Below are screenshot observations grouped by topic for one user. For EACH topic write "
            "the memory a personal-memory app would store: 1-3 terse, self-contained sentences "
            "about the user's interests, products compared, preferences or decisions. No "
            "preamble.\n\n" + blocks + '\n\nReturn {"memories": [{"id": "<topic id>", "content": ""}]}')
    else:  # queries_tricky
        prompt = (
            f"Write {chunk['n']} chatbot messages that share words or themes with one of the "
            "user topics below, but where the user's remembered activity on that topic would NOT "
            "help (example: 'apple pie recipe' versus a user researching Apple products; "
            "'python snake habitat' versus a user studying Python for ML).\n\n"
            f"Topics:\n{catalog_lines()}\n\n"
            'Return {"queries": [{"text": "", "looks_like": "<topic id>"}]}'
        )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]


def validate(chunk: dict, data: dict) -> str | None:
    job = chunk["job"]
    if job == "captions":
        got = {t.get("id"): t.get("observations", []) for t in data.get("topics", [])}
        for tid in chunk["topic_ids"]:
            obs = got.get(tid, [])
            if len(obs) < OBS_PER_TOPIC // 2 or not all(isinstance(o.get("caption"), str) for o in obs):
                return f"topic {tid} missing or short ({len(obs)} observations)"
        return None
    if job == "memories":
        got = {m.get("id"): m.get("content") for m in data.get("memories", [])}
        missing = [t for t in chunk["topic_ids"] if not got.get(t)]
        return f"missing memories for {missing[:5]}" if missing else None
    queries = data.get("queries", [])
    expected = {"queries_personal": QUERIES_PER_TOPIC * len(chunk.get("topic_ids") or []),
                "queries_generic": chunk.get("n", 0), "queries_tricky": chunk.get("n", 0)}[job]
    if len(queries) < expected * 0.6:
        return f"only {len(queries)} queries, expected about {expected}"
    if not all(isinstance(q.get("text"), str) and q["text"].strip() for q in queries):
        return "query without text"
    if job == "queries_personal":
        bad = [q for q in queries if not q.get("relevant") or not set(q["relevant"]) <= set(TOPIC_IDS)]
        if len(bad) > len(queries) * 0.1:
            return f"{len(bad)} queries with missing/unknown topic ids"
    return None


# ---------------------------------------------------------------- chunks ---
def initial_chunks() -> list[dict]:
    chunks = [
        {"id": "captions-0", "job": "captions", "topic_ids": TOPIC_IDS},
        {"id": "queries_personal-0", "job": "queries_personal", "topic_ids": TOPIC_IDS},
        {"id": "queries_generic-0", "job": "queries_generic", "n": GENERIC_QUERIES},
        {"id": "queries_tricky-0", "job": "queries_tricky", "n": TRICKY_QUERIES},
    ]
    if (OUT / "captions.json").exists():
        chunks.append({"id": "memories-0", "job": "memories", "topic_ids": TOPIC_IDS})
    return chunks


def split_chunk(chunk: dict) -> list[dict] | None:
    ids = chunk.get("topic_ids")
    if ids and len(ids) > 1 and chunk["job"] in ("captions", "queries_personal", "memories"):
        mid = len(ids) // 2
        return [{**chunk, "id": chunk["id"] + "a", "topic_ids": ids[:mid]},
                {**chunk, "id": chunk["id"] + "b", "topic_ids": ids[mid:]}]
    if chunk.get("n", 0) >= 40:
        half = chunk["n"] // 2
        return [{**chunk, "id": chunk["id"] + "a", "n": half},
                {**chunk, "id": chunk["id"] + "b", "n": chunk["n"] - half}]
    return None


def ingest(chunk: dict, text: str, finish_reason: str, source: str) -> str:
    """-> 'ok' | 'split' | 'retry'"""
    if finish_reason == "length":
        if split_chunk(chunk):
            log.warning("[%s] %s output truncated (hit token limit) -> splitting chunk", chunk["id"], source)
            return "split"
        log.warning("[%s] %s output truncated and chunk cannot be split", chunk["id"], source)
        return "retry"
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
    except Exception as exc:
        log.warning("[%s] %s returned unparseable JSON (%s)", chunk["id"], source, exc)
        return "retry"
    problem = validate(chunk, data)
    if problem:
        log.warning("[%s] %s output invalid: %s", chunk["id"], source, problem)
        return "retry"
    (RESULTS / f"{chunk['id']}.json").write_text(json.dumps(data, indent=1))
    update_chunk(chunk["id"], status="done", finished_via=source)
    log.info("[%s] DONE via %s", chunk["id"], source)
    return "ok"


# ---------------------------------------------------------------- OpenAI ---
FATAL = (openai.BadRequestError, openai.AuthenticationError, openai.PermissionDeniedError,
         openai.NotFoundError, openai.UnprocessableEntityError)


def is_fatal(exc: Exception) -> bool:
    if isinstance(exc, FATAL):
        return True
    return isinstance(exc, openai.RateLimitError) and getattr(exc, "code", "") == "insufficient_quota"


def call_flex(chunk: dict) -> tuple[str, str]:
    resp = client.chat.completions.create(
        model=MODEL, messages=build_messages(chunk), service_tier="flex",
        max_completion_tokens=args.max_tokens, timeout=REQUEST_TIMEOUT)
    choice = resp.choices[0]
    log.info("[%s] flex ok: finish=%s completion_tokens=%s tier=%s", chunk["id"],
             choice.finish_reason, resp.usage.completion_tokens, getattr(resp, "service_tier", "?"))
    return choice.message.content or "", choice.finish_reason


def submit_batch(chunk: dict) -> None:
    body = {"model": MODEL, "messages": build_messages(chunk),
            "max_completion_tokens": args.max_tokens}
    path = BATCH_INPUTS / f"{chunk['id']}.jsonl"
    path.write_text(json.dumps({"custom_id": chunk["id"], "method": "POST",
                                "url": "/v1/chat/completions", "body": body}) + "\n")
    with path.open("rb") as fh:
        uploaded = client.files.create(file=fh, purpose="batch")
    batch = client.batches.create(input_file_id=uploaded.id, endpoint="/v1/chat/completions",
                                  completion_window="24h", metadata={"chunk": chunk["id"]})
    update_chunk(chunk["id"], batch_id=batch.id)
    log.warning("[%s] BATCH SUBMITTED: %s  (check later: uv run python scripts/synthetic/generate.py --check)",
                chunk["id"], batch.id)


def poll_batch(chunk: dict) -> bool:
    """True when the chunk was completed from the batch result."""
    try:
        batch = client.batches.retrieve(chunk["batch_id"])
    except Exception as exc:
        log.warning("[%s] batch poll failed: %s", chunk["id"], exc)
        return False
    log.info("[%s] batch %s status=%s", chunk["id"], batch.id, batch.status)
    if batch.status in ("failed", "expired", "cancelled"):
        log.warning("[%s] batch %s ended as %s; will resubmit if flex keeps failing",
                    chunk["id"], batch.id, batch.status)
        update_chunk(chunk["id"], batch_id=None)
        return False
    if batch.status != "completed" or not batch.output_file_id:
        return False
    line = client.files.content(batch.output_file_id).text.strip().splitlines()[0]
    return ingest_batch_line(chunk, json.loads(line)) == "ok"


def ingest_batch_line(chunk: dict, line: dict) -> str:
    body = (line.get("response") or {}).get("body") or {}
    if not body.get("choices"):
        log.warning("[%s] batch line had no choices: %s", chunk["id"], line.get("error"))
        return "retry"
    choice = body["choices"][0]
    return ingest(chunk, choice["message"].get("content") or "", choice.get("finish_reason"), "batch")


# ---------------------------------------------------------------- worker ---
def start_worker(chunk_id: str) -> None:
    t = threading.Thread(target=worker, args=(chunk_id,), name=chunk_id, daemon=True)
    with state_lock:
        threads.append(t)
    t.start()


def worker(chunk_id: str) -> None:
    fails, delay = 0, BACKOFF_START
    while True:
        chunk = get_chunk(chunk_id)
        if chunk["status"] != "pending":
            return
        if chunk["batch_id"] and poll_batch(chunk):
            return
        log.info("[%s] flex attempt (consecutive failures so far: %d)", chunk_id, fails)
        outcome = "retry"
        try:
            text, finish = call_flex(chunk)
            outcome = ingest(chunk, text, finish, "flex")
        except Exception as exc:
            if is_fatal(exc):
                log.error("[%s] FATAL, not retrying: %s: %s", chunk_id, type(exc).__name__, exc)
                update_chunk(chunk_id, status="failed", error=str(exc))
                return
            log.warning("[%s] flex failed: %s: %s", chunk_id, type(exc).__name__, str(exc)[:300])
            if isinstance(exc, openai.APITimeoutError) and split_chunk(chunk):
                log.warning("[%s] timed out; a smaller request finishes faster -> splitting chunk", chunk_id)
                outcome = "split"

        if outcome == "ok":
            cancel_batch(chunk_id)
            return
        if outcome == "split":
            cancel_batch(chunk_id)
            update_chunk(chunk_id, status="split")
            for child in split_chunk(chunk):
                add_chunk(child)
                start_worker(child["id"])
            return

        fails += 1
        if fails >= BATCH_AFTER and not get_chunk(chunk_id)["batch_id"]:
            try:
                submit_batch(get_chunk(chunk_id))
            except Exception as exc:
                log.error("[%s] batch submit failed: %s", chunk_id, exc)
        wait = min(delay, BACKOFF_MAX) * random.uniform(0.8, 1.2)
        delay = min(delay * 2, BACKOFF_MAX)
        log.info("[%s] retrying flex in %.0fs", chunk_id, wait)
        time.sleep(wait)


def cancel_batch(chunk_id: str) -> None:
    batch_id = get_chunk(chunk_id)["batch_id"]
    if not batch_id:
        return
    try:
        client.batches.cancel(batch_id)
        log.info("[%s] cancelled now-unneeded batch %s", chunk_id, batch_id)
    except Exception as exc:
        log.warning("[%s] could not cancel batch %s: %s", chunk_id, batch_id, exc)
    update_chunk(chunk_id, batch_id=None)


# ----------------------------------------------------------------- merge ---
def merge_results() -> None:
    chunks = load_state()["chunks"].values()

    def job_done(prefix: str) -> bool:
        mine = [c for c in chunks if c["job"] == prefix]
        return bool(mine) and all(c["status"] in ("done", "split") for c in mine)

    def read(job: str) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(RESULTS.glob(f"{job}-*.json"))]

    if job_done("captions"):
        merged: dict[str, list] = {}
        for part in read("captions"):
            for t in part["topics"]:
                merged.setdefault(t["id"], []).extend(t["observations"])
        (OUT / "captions.json").write_text(json.dumps(merged, indent=1))
        log.info("MERGED captions.json: %d topics, %d observations",
                 len(merged), sum(map(len, merged.values())))
    if job_done("memories"):
        merged_mem = {m["id"]: m["content"] for part in read("memories") for m in part["memories"]}
        (OUT / "memories.json").write_text(json.dumps(merged_mem, indent=1))
        log.info("MERGED memories.json: %d memories", len(merged_mem))
    if all(job_done(j) for j in ("queries_personal", "queries_generic", "queries_tricky")):
        queries = []
        for job, kind in (("queries_personal", "personal"), ("queries_generic", "generic"),
                          ("queries_tricky", "tricky")):
            for part in read(job):
                for q in part["queries"]:
                    queries.append({"text": q["text"].strip(), "kind": kind,
                                    "relevant": q.get("relevant", []) if kind == "personal" else []})
        (OUT / "queries.json").write_text(json.dumps(queries, indent=1))
        log.info("MERGED queries.json: %d queries", len(queries))


def print_status() -> None:
    for c in load_state()["chunks"].values():
        log.info("%-24s %-8s batch=%s via=%s", c["id"], c["status"], c.get("batch_id"),
                 c.get("finished_via", "-"))


# ------------------------------------------------------------------ main ---
def main() -> None:
    global args, client
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="poll pending batches once and exit")
    parser.add_argument("--import-batch", metavar="FILE", help="ingest a downloaded batch output .jsonl")
    parser.add_argument("--max-tokens", type=int, default=64000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(threadName)s] %(message)s",
                        datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(OUT / "gen.log", encoding="utf-8")])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY missing from .env")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=REQUEST_TIMEOUT)

    for c in initial_chunks():
        add_chunk(c)

    if args.import_batch:
        for raw in Path(args.import_batch).read_text(encoding="utf-8").splitlines():
            line = json.loads(raw)
            chunk = get_chunk(line["custom_id"])
            log.info("[%s] importing batch result", chunk["id"])
            ingest_batch_line(chunk, line)
        merge_results()
        return

    if args.check:
        for c in load_state()["chunks"].values():
            if c["status"] == "pending" and c["batch_id"]:
                poll_batch(c)
        merge_results()
        print_status()
        return

    pending = [c["id"] for c in load_state()["chunks"].values() if c["status"] == "pending"]
    log.info("model=%s max_tokens=%d | starting %d chunk(s): %s", MODEL, args.max_tokens,
             len(pending), ", ".join(pending))
    for cid in pending:
        start_worker(cid)
    while True:
        time.sleep(2)
        with state_lock:
            alive = [t for t in threads if t.is_alive()]
        if not alive:
            break
    merge_results()
    print_status()
    left = [c["id"] for c in load_state()["chunks"].values() if c["status"] in ("pending", "failed")]
    log.info("ALL DONE" if not left else f"STOPPED with unfinished chunks: {left}")


if __name__ == "__main__":
    main()
