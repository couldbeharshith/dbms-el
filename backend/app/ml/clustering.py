import numpy as np

from backend.app import config
from backend.app.ml.vectors import cosine


class GreedyCentroidClusterer:
    """Baseline. Each observation joins the cluster whose running centroid it is
    closest to (if >= threshold), else starts a new cluster. Oldest first so
    cluster membership is stable across passes."""

    window_hours = 24
    max_observations = 300

    def __init__(self, threshold: float, min_size: int):
        self.threshold = threshold
        self.min_size = min_size

    def cluster(self, observations: list[dict]) -> list[list[dict]]:
        groups: list[dict] = []  # {"members": [...], "sum": vector}
        for o in reversed(observations):
            best, best_sim = None, self.threshold
            for g in groups:
                sim = cosine(g["sum"], o["vec"])
                if sim >= best_sim:
                    best, best_sim = g, sim
            if best is None:
                groups.append({"members": [o], "sum": o["vec"].copy()})
            else:
                best["members"].append(o)
                best["sum"] = best["sum"] + o["vec"]
        return [g["members"] for g in groups if len(g["members"]) >= self.min_size]


def build_greedy() -> GreedyCentroidClusterer:
    return GreedyCentroidClusterer(config.CLUSTER_SIMILARITY_THRESHOLD, config.CLUSTER_MIN_OBSERVATIONS)

class HDBSCANClusterer:
    """Density-based clustering (scikit-learn). Finds the number of clusters itself and
    leaves stray observations as noise. Looks back 30 days so long-term patterns show up.
    Vectors are unit length, so euclidean distance ranks the same as cosine."""

    window_hours = config.CLUSTER_WINDOW_DAYS * 24
    max_observations = 3000

    def __init__(self, min_cluster_size: int, min_samples: int, selection: str = "eom",
                 pca_dims: int | None = None):
        self.pca_dims = pca_dims
        self.min_cluster_size = min_cluster_size
        self.min_samples = min_samples
        self.selection = selection

    def cluster(self, observations: list[dict]) -> list[list[dict]]:
        if len(observations) < max(self.min_cluster_size, 2):
            return []
        from sklearn.cluster import HDBSCAN

        vectors = np.stack([o["vec"] / (np.linalg.norm(o["vec"]) or 1.0) for o in observations])
        if self.pca_dims and len(vectors) > self.pca_dims:  # density estimation is poor in 1024 dimensions
            from sklearn.decomposition import PCA

            vectors = PCA(n_components=self.pca_dims, random_state=0).fit_transform(vectors)
        labels = HDBSCAN(min_cluster_size=self.min_cluster_size, min_samples=self.min_samples,
                         cluster_selection_method=self.selection, copy=True).fit_predict(vectors)
        groups: dict[int, list[dict]] = {}
        for obs, label in zip(observations, labels):
            if label >= 0:
                groups.setdefault(int(label), []).append(obs)
        return list(groups.values())


def build_hdbscan() -> HDBSCANClusterer:
    return HDBSCANClusterer(config.HDBSCAN_MIN_CLUSTER_SIZE, config.HDBSCAN_MIN_SAMPLES,
                            config.HDBSCAN_SELECTION, config.HDBSCAN_PCA_DIMS)


class AgglomerativeClusterer:
    """Average-linkage hierarchical clustering on cosine distance, cut at a fixed distance."""

    window_hours = config.CLUSTER_WINDOW_DAYS * 24
    max_observations = 3000

    def __init__(self, distance_threshold: float, min_size: int):
        self.distance_threshold = distance_threshold
        self.min_size = min_size

    def cluster(self, observations: list[dict]) -> list[list[dict]]:
        if len(observations) < 2:
            return []
        from sklearn.cluster import AgglomerativeClustering

        vectors = np.stack([o["vec"] for o in observations])
        labels = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                         distance_threshold=self.distance_threshold).fit_predict(vectors)
        groups: dict[int, list[dict]] = {}
        for obs, label in zip(observations, labels):
            groups.setdefault(int(label), []).append(obs)
        return [g for g in groups.values() if len(g) >= self.min_size]


def build_agglomerative() -> AgglomerativeClusterer:
    return AgglomerativeClusterer(config.AGGLOMERATIVE_DISTANCE, config.CLUSTER_MIN_OBSERVATIONS)
