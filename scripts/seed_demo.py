"""Seed a realistic 'laptop research' demo session into the database.

Works fully offline: canned captions/OCR (no OpenAI needed), but REAL local
embeddings from the GPU model for observations (text + image), memories, and
evidence links. Run from the repo root:

    uv run python scripts/seed_demo.py            # additive
    uv run python scripts/seed_demo.py --reset    # wipe all data first
"""

import argparse
import io
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from backend.app import crypto, db, config  # noqa: E402
from backend.app.embed import service as embed  # noqa: E402

NOW = datetime.now(timezone.utc)

# (url, domain, title, minutes_ago, dwell_seconds)
EVENTS = [
    ("https://www.amazon.in/dp/B0CX23V2ZK", "www.amazon.in", "ASUS TUF Gaming F15 RTX 4060 — Amazon", 3100, 210),
    ("https://www.amazon.in/dp/B0CX23V2ZK", "www.amazon.in", "ASUS TUF Gaming F15 RTX 4060 — Amazon", 2900, 145),
    ("https://www.amazon.in/dp/B0D3QL2FRD", "www.amazon.in", "Lenovo LOQ 15 RTX 4060 — Amazon", 2870, 180),
    ("https://www.flipkart.com/lenovo-loq-rtx4060/p/itm123", "www.flipkart.com", "Lenovo LOQ RTX 4060 — Flipkart", 2500, 95),
    ("https://www.reddit.com/r/IndianGaming/comments/laptop/", "www.reddit.com", "Best gaming laptop under 90k? — r/IndianGaming", 2450, 420),
    ("https://www.reddit.com/r/IndianGaming/comments/tuf_vs_loq/", "www.reddit.com", "ASUS TUF vs Lenovo LOQ — which one? — r/IndianGaming", 2420, 350),
    ("https://www.youtube.com/watch?v=RTX4060laptops", "www.youtube.com", "RTX 4060 Gaming Laptops Compared (2026)", 2100, 660),
    ("https://www.youtube.com/watch?v=tuff15review", "www.youtube.com", "ASUS TUF F15 long-term review", 2050, 480),
    ("https://www.flipkart.com/asus-tuf-f15/p/itm456", "www.flipkart.com", "ASUS TUF F15 price — Flipkart", 1500, 88),
    ("https://www.amazon.in/dp/B0HPYZLXNC", "www.amazon.in", "HP Victus RTX 4060 — Amazon", 1450, 60),
    ("https://www.reddit.com/r/laptops/comments/victus_build/", "www.reddit.com", "HP Victus build quality thread", 1400, 240),
    ("https://www.youtube.com/watch?v=laptopvalue2026", "www.youtube.com", "Best value gaming laptops India 2026", 900, 540),
]

# (title, caption, ocr_text, topics, source_app, minutes_ago)
SHOTS = [
    ("Amazon ASUS TUF page",
     "User is viewing the ASUS TUF Gaming F15 product page on Amazon, comparing the RTX 4060 variant priced at Rs 89,990.",
     "ASUS TUF Gaming F15\nRTX 4060 8GB | i7-13650HX | 16GB DDR5\nRs 89,990 (22% off)",
     ["laptops", "shopping", "asus"], "chrome.exe — Amazon", 3095),
    ("Amazon Lenovo LOQ page",
     "User is comparing the Lenovo LOQ 15 with RTX 4060 at Rs 86,499 against the ASUS TUF on Amazon.",
     "Lenovo LOQ 15IAX9\nRTX 4060 | i5-13450HX | 16GB\nRs 86,499",
     ["laptops", "shopping", "lenovo"], "chrome.exe — Amazon", 2860),
    ("Reddit thread under 90k",
     "User is reading an r/IndianGaming thread asking for the best gaming laptop under Rs 90,000; answers recommend TUF and LOQ.",
     "Best gaming laptop under 90k?\nTop: TUF F15 or LOQ, both RTX 4060\nAvoid Victus hinges",
     ["laptops", "reddit", "advice"], "chrome.exe — Reddit", 2440),
    ("YouTube comparison video",
     "User is watching a 2026 comparison of RTX 4060 gaming laptops with FPS benchmarks for TUF, LOQ and Victus.",
     "RTX 4060 Laptops Compared\nTUF: 87 fps | LOQ: 85 fps | Victus: 84 fps",
     ["laptops", "youtube", "benchmarks"], "chrome.exe — YouTube", 2080),
    ("Flipkart price check",
     "User is checking the ASUS TUF F15 price on Flipkart, which lists it slightly above the Amazon price.",
     "ASUS TUF F15 FX507\nRs 91,990 + bank offer Rs 3,000 off",
     ["laptops", "shopping", "prices"], "chrome.exe — Flipkart", 1490),
    ("HP Victus page",
     "User briefly looked at the HP Victus RTX 4060 but comment sections criticised its build quality.",
     "HP Victus 16\nRTX 4060 | Rs 84,990",
     ["laptops", "shopping"], "chrome.exe — Amazon", 1440),
]

MEMORIES = [
    ("User is researching gaming laptops in India with a budget around ₹90,000, focused on RTX 4060 models.",
     0.82, ["laptops", "shopping"]),
    ("User is comparing ASUS TUF F15 (₹89,990 on Amazon) and Lenovo LOQ (₹86,499) as the top two choices.",
     0.74, ["asus", "lenovo", "laptops"]),
    ("User has watched RTX 4060 benchmark videos; performance differences between TUF, LOQ and Victus are within a few fps.",
     0.62, ["benchmarks", "youtube"]),
]


def make_fake_screenshot(title: str) -> bytes:
    """Draw a simple product-page-like image so image embeddings have real pixels."""
    random.seed(hash(title) & 0xFFFF)
    img = Image.new("RGB", (960, 600), (22 + random.randint(0, 8), 24, 31))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 960, 70), fill=(38, 42, 56))
    d.text((30, 25), f"{title}  —  demo product page", fill=(235, 238, 245))
    for i in range(6):
        y = 110 + i * 78
        d.rounded_rectangle((30, y, 930, y + 62), 8, fill=(31, 35, 48))
        d.text((52, y + 18), f"Spec row {i+1}:  RTX 4060 laptop detail #{random.randint(10,99)}", fill=(200, 206, 220))
    d.rounded_rectangle((30, 540, 300, 585), 8, fill=(46, 110, 84))
    d.text((60, 552), "Rs 8x,990  BUY NOW", fill=(240, 245, 240))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="delete all data first")
    args = parser.parse_args()

    db.init_pool()
    if args.reset:
        for table in ("chat_queries", "memory_observations", "memories", "observations",
                      "screenshots", "browsing_events"):
            db.execute(f"DELETE FROM {table}")
        for f in config.SCREENSHOT_DIR.glob("*.enc"):
            f.unlink(missing_ok=True)
        print("Cleared existing data.")

    user = db.get_default_user()
    uid = user["id"]
    print("Waiting for embedding model…")
    assert embed.wait_ready(240), f"embedding model failed: {embed.error}"
    print(f"Embedding ready on {embed.device}")

    # --- browsing events -----------------------------------------------------
    rows = [[uid, u, dom, t, NOW - timedelta(minutes=m), dwell, "seed-demo",
             db._jsonb({"seed": True})] for (u, dom, t, m, dwell) in EVENTS]
    db.executemany(
        "INSERT INTO browsing_events (user_id, url, domain, title, visited_at, "
        "time_spent_sec, session_id, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
        rows,
    )
    print(f"Inserted {len(rows)} browsing events")

    # --- screenshots + observations -------------------------------------------
    event_ids = [r["id"] for r in db.query(
        "SELECT id FROM browsing_events WHERE session_id='seed-demo' ORDER BY id")]
    obs_ids: list[uuid.UUID] = []
    shot_event = 0
    for (title, caption, ocr, topics, src, minutes_ago) in SHOTS:
        sid = uuid.uuid4()
        png = make_fake_screenshot(title)
        (config.SCREENSHOT_DIR / f"{sid}.enc").write_bytes(crypto.encrypt(png))
        import hashlib
        db.execute(
            "INSERT INTO screenshots (id, user_id, captured_at, file_path, sha256, "
            "size_bytes, width, height, source_app, metadata) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            [sid, uid, NOW - timedelta(minutes=minutes_ago), f"screenshots/{sid}.enc",
             hashlib.sha256(png).hexdigest(), len(png), 960, 600, src,
             db._jsonb({"seed": True})],
        )
        doc = f"{caption}\n\nVisible text: {ocr}"
        text_vec = embed.embed_document_sync(doc)
        image_vec = embed.embed_image_sync(Image.open(io.BytesIO(png)))
        row = db.query_one(
            "INSERT INTO observations (user_id, screenshot_id, kind, caption, "
            "ocr_encrypted, topics, observed_at, embedding, image_embedding, metadata) "
            "VALUES (%s,%s,'screenshot',%s,%s,%s,%s,%s::vector,%s::vector,%s::jsonb) RETURNING id",
            [uid, sid, caption, crypto.encrypt(ocr.encode()), topics,
             NOW - timedelta(minutes=minutes_ago), db.vec_lit(text_vec),
             db.vec_lit(image_vec), db._jsonb({"seed": True})],
        )
        obs_ids.append(row["id"])
        shot_event = min(shot_event + 1, len(event_ids) - 1)
        print(f"  observation: {caption[:70]}…")

    # also one browsing observation linked to an event (demonstrates that kind)
    browsed = db.query_one(
        "SELECT id, domain, title, time_spent_sec, visited_at FROM browsing_events "
        "WHERE session_id='seed-demo' ORDER BY time_spent_sec DESC LIMIT 1")
    text = f"User spent {browsed['time_spent_sec']}s on {browsed['domain']} — {browsed['title']}"
    vec = embed.embed_document_sync(text)
    row = db.query_one(
        "INSERT INTO observations (user_id, browsing_event_id, kind, caption, topics, "
        "observed_at, embedding, metadata) VALUES (%s,%s,'browsing',%s,%s,%s,%s::vector,%s::jsonb) RETURNING id",
        [uid, browsed["id"], text, [browsed["domain"]], browsed["visited_at"],
         db.vec_lit(vec), db._jsonb({"time_spent_sec": browsed["time_spent_sec"], "seed": True})],
    )
    obs_ids.append(row["id"])
    print(f"  browsing observation: {text[:70]}…")

    # --- memories + evidence ---------------------------------------------------
    # spread evidence links across memories
    per = max(1, len(obs_ids) // len(MEMORIES))
    for i, (content, importance, topics) in enumerate(MEMORIES):
        vec = embed.embed_document_sync(content)
        row = db.query_one(
            "INSERT INTO memories (user_id, content, embedding, importance, metadata) "
            "VALUES (%s,%s,%s::vector,%s,%s::jsonb) RETURNING id",
            [uid, content, db.vec_lit(vec), importance, db._jsonb({"topics": topics, "source": "seed"})],
        )
        evidence = obs_ids[i * per: (i + 1) * per] or [obs_ids[-1]]
        for oid in evidence:
            db.execute(
                "INSERT INTO memory_observations (memory_id, observation_id) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING", [row["id"], oid])
        print(f"  memory: {content[:70]}…")

    print("\nSeed complete. Counts:")
    for t in ("browsing_events", "screenshots", "observations", "memories", "memory_observations"):
        n = db.query_one(f"SELECT count(*) AS n FROM {t}")["n"]
        print(f"  {t}: {n}")
    db.close_pool()


if __name__ == "__main__":
    main()
