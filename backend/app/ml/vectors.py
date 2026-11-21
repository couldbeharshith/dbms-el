import numpy as np


def parse_vec(text: str) -> np.ndarray:
    """pgvector text literal '[0.1,0.2,...]' -> float32 array."""
    return np.array([float(x) for x in text.strip("[]").split(",")], dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def centroid(vecs: list[np.ndarray]) -> list[float]:
    c = np.mean(np.stack(vecs), axis=0)
    c = c / (np.linalg.norm(c) or 1.0)
    return c.tolist()
