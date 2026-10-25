"""Perceptual fingerprint for screenshot dedupe.

The fingerprint is a tiny 64x36 grayscale thumbnail stored as hex; similarity
is the mean absolute pixel difference (0..1). Why not a classic bit hash
(dHash/pHash): gradient-comparison bits cannot see text at any workable
resolution — two different product pages on the same site template measure ~0
bits apart while a cursor blink also measures 0, so no threshold separates
them. Direct thumbnail comparison keeps that information: an idle screen with
a blinking cursor ~0.001%, a genuinely different page with a photo ~1.5%.

Threshold lives in config.PHASH_MAX_MAD (skip when distance <= threshold,
against screenshots from the last PHASH_WINDOW_MINUTES only, so a returning
user's unchanged desktop doesn't stay permanently skipped).
"""

import io

from PIL import Image

_SIZE = (64, 36)


def fingerprint(image_bytes: bytes) -> str | None:
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize(_SIZE, Image.LANCZOS)
    except Exception:
        return None
    return img.tobytes().hex()


def distance(a: str, b: str) -> float:
    """Mean absolute difference of two fingerprints, normalized to 0..1."""
    if a is None or b is None or len(a) != len(b):
        return 1.0
    ba, bb = bytes.fromhex(a), bytes.fromhex(b)
    return sum(abs(x - y) for x, y in zip(ba, bb)) / len(ba) / 255
