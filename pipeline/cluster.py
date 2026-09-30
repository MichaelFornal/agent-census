"""Two-level clustering for the use-case taxonomy (PRD §4 S7): UMAP then HDBSCAN, then HDBSCAN again
inside each level-1 domain. Below SMALL_N points UMAP is skipped and HDBSCAN uses min_samples=1: a probe
showed the default merging distinct groups of duplicate points (M1 ruling)."""
import warnings

import hdbscan
import numpy as np

SMALL_N = 50


def reduce(X: np.ndarray) -> np.ndarray:
    if len(X) < SMALL_N:
        return X
    import umap
    with warnings.catch_warnings():
        # A fixed random_state makes UMAP single-threaded and it warns about that. Reproducible
        # labels matter more than speed here, so the warning carries no information.
        warnings.filterwarnings("ignore", message="n_jobs value 1 overridden", category=UserWarning)
        return umap.UMAP(n_components=5, n_neighbors=min(15, len(X) - 1), min_dist=0.0, metric="cosine",
                         random_state=0).fit_transform(X)


def cluster_points(Z: np.ndarray, min_size: int) -> np.ndarray:
    n = len(Z)
    if n < max(2, min_size):
        return np.full(n, -1)
    min_samples = 1 if n < SMALL_N else None
    return hdbscan.HDBSCAN(min_cluster_size=min_size, min_samples=min_samples).fit_predict(Z)


def _level1_min(n: int) -> int:
    return 2 if n < SMALL_N else max(5, n // 100)


def two_level(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    Z = reduce(X)
    l1 = cluster_points(Z, _level1_min(len(X)))
    l2 = np.full(len(X), -1)
    for c in sorted(set(l1.tolist()) - {-1}):
        idx = np.where(l1 == c)[0]
        m2 = max(2, len(idx) // 10)
        if len(idx) < 2 * m2:
            continue
        sub = cluster_points(Z[idx], m2)
        if len(set(sub.tolist()) - {-1}) >= 2:
            l2[idx] = sub
    return l1, l2


def nn_distance(X: np.ndarray, chunk: int = 1024) -> np.ndarray:
    """Cosine distance from each unit-norm row to its nearest other row, in chunks to bound memory."""
    X = X.astype(np.float32)
    n = len(X)
    out = np.ones(n, dtype=np.float32)
    if n < 2:
        return out.astype(float)
    for s in range(0, n, chunk):
        S = X[s:s + chunk] @ X.T
        for i in range(S.shape[0]):
            S[i, s + i] = -np.inf
        out[s:s + chunk] = 1.0 - S.max(axis=1)
    return out.astype(float)
