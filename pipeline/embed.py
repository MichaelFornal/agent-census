"""Text embeddings for S7. bge-small is the measured choice (PRD §8, §9.1); the hash embedder is a
dependency-free stand-in for fixtures and CI."""
import hashlib
import re

import numpy as np


class HashEmbedder:
    dim = 256

    def encode(self, texts: list[str]) -> np.ndarray:
        X = np.zeros((len(texts), self.dim))
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                X[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        norms = np.linalg.norm(X, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return X / norms


class BgeEmbedder:
    MODEL = "BAAI/bge-small-en-v1.5"

    def __init__(self, device: str = "cpu") -> None:
        from sentence_transformers import SentenceTransformer  # optional extra: uv sync --extra embed
        self.model = SentenceTransformer(self.MODEL, device=device)

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False))


def make_embedder(name: str):
    return {"hash": HashEmbedder, "bge": BgeEmbedder}[name]()
