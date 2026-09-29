"""Distinct-cluster ratio on the sample: exact, normalized, MinHash LSH (PRD §4 S4). Throwaway code.

Run: uv run python -m m0.dedup
"""
import hashlib
import json
import random
import re
from collections import Counter
from dataclasses import dataclass

from datasketch import MinHash, MinHashLSH

from m0.paths import blob_path, data_path, read_jsonl, write_metrics
from m0.sample import COMPONENTS

THRESHOLDS = [0.5, 0.7, 0.8, 0.9]
CHOSEN = "0.8"


@dataclass(frozen=True)
class Doc:
    kind: str
    repo: str
    path: str
    oid: str
    text: str


class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def roots(self) -> set[int]:
        return {self.find(i) for i in range(len(self.parent))}

    def copy(self) -> "UnionFind":
        uf = UnionFind(0)
        uf.parent = list(self.parent)
        return uf


def normalize(text: str, repo: str) -> str:
    owner, name = repo.split("/", 1)
    t = text.lower()
    for token, placeholder in ((name.lower(), "{repo}"), (owner.lower(), "{owner}")):
        if len(token) >= 3:
            t = re.sub(rf"(?<![\w-]){re.escape(token)}(?![\w-])", placeholder, t)
    return re.sub(r"\s+", " ", t).strip()


def shingles(text: str, k: int = 5) -> set[str]:
    toks = re.findall(r"\w+", text)
    if len(toks) <= k:
        return {" ".join(toks)}
    return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def cluster_kind(docs: list[Doc], thresholds: list[float] = THRESHOLDS,
                 num_perm: int = 128) -> tuple[dict, dict[str, list[Doc]]]:
    uf = UnionFind(len(docs))
    first_oid: dict[str, int] = {}
    for i, d in enumerate(docs):
        uf.union(i, first_oid.setdefault(d.oid, i))
    exact = len(uf.roots())
    normed = [normalize(d.text, d.repo) for d in docs]
    first_norm: dict[str, int] = {}
    for i, t in enumerate(normed):
        uf.union(i, first_norm.setdefault(hashlib.sha256(t.encode()).hexdigest(), i))
    groups = sorted(uf.roots())
    hashes: dict[int, MinHash] = {}
    for g in groups:
        m = MinHash(num_perm=num_perm, seed=1)
        for s in shingles(normed[g]):
            m.update(s.encode())
        hashes[g] = m
    counts = {"n": len(docs), "exact": exact, "normalized": len(groups), "minhash": {}}
    reps: dict[str, list[Doc]] = {}
    for t in thresholds:
        uft = uf.copy()
        lsh = MinHashLSH(threshold=t, num_perm=num_perm)
        for g, m in hashes.items():
            lsh.insert(str(g), m)
        for g, m in hashes.items():
            for other in lsh.query(m):
                o = int(other)
                if o != g and m.jaccard(hashes[o]) >= t:
                    uft.union(g, o)
        roots = sorted(uft.roots())
        counts["minhash"][str(t)] = len(roots)
        reps[str(t)] = [docs[r] for r in roots]
    return counts, reps


def load_docs() -> tuple[list[Doc], dict[str, int]]:
    seen: set[tuple[str, str]] = set()
    docs: list[Doc] = []
    skipped: Counter[str] = Counter()
    for f in read_jsonl(data_path("files.jsonl")):
        key = (f["repo"], f["path"])
        if key in seen:
            continue
        seen.add(key)
        if f["missing"]:
            skipped["missing"] += 1
            continue
        if not f.get("stored"):
            skipped["binary_or_null_text"] += 1
            continue
        text = blob_path(f["oid"]).read_text()
        if not text.strip():
            skipped["empty"] += 1
            continue
        docs.append(Doc(f["kind"], f["repo"], f["path"], f["oid"], text))
    return docs, dict(skipped)


def by_kind(docs: list[Doc]) -> dict[str, list[Doc]]:
    out: dict[str, list[Doc]] = {}
    for d in docs:
        out.setdefault(d.kind, []).append(d)
    return out


def main() -> None:
    docs, skipped = load_docs()
    kinds: dict[str, dict] = {}
    reps_out: list[Doc] = []
    for kind, kdocs in sorted(by_kind(docs).items()):
        counts, reps = cluster_kind(kdocs)
        counts["projected_distinct_0.8"] = round(counts["minhash"][CHOSEN] / counts["n"] * COMPONENTS[kind][1])
        kinds[kind] = counts
        reps_out += reps[CHOSEN]
        print(kind, json.dumps(counts))
    rarefaction = {}
    rng = random.Random(0)
    for n in (1000, 2500):
        if n < len(docs):
            sub = rng.sample(docs, n)
            distinct = sum(cluster_kind(k, [0.8])[0]["minhash"][CHOSEN] for k in by_kind(sub).values())
            rarefaction[str(n)] = distinct / n
    rarefaction[str(len(docs))] = sum(k["minhash"][CHOSEN] for k in kinds.values()) / len(docs)
    reps_p = data_path("representatives.jsonl")
    reps_p.write_text("".join(json.dumps({"kind": d.kind, "repo": d.repo, "path": d.path, "oid": d.oid}) + "\n"
                              for d in reps_out))
    write_metrics("dedup", {
        "kinds": kinds,
        "skipped": skipped,
        "rarefaction": rarefaction,
        "projected_distinct_total_0.8": sum(k["projected_distinct_0.8"] for k in kinds.values()),
    })


if __name__ == "__main__":
    main()
