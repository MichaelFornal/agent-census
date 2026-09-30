"""S4 dedup and lineage (PRD §4 S4).

Tier 1 exact blob SHA, tier 2 normalized hash (case, whitespace, repo/owner names templated out),
tier 3 MinHash LSH at THRESHOLD (provisional until the M3 dedup audit). Skills, agents and commands
also get a family key, because M0 showed edited copies drift apart in text but keep their name.
Origin = the member repo created first (M1 ruling: no per-file history queries yet).
"""
import hashlib
import json
import re
from dataclasses import dataclass

from datasketch import MinHash, MinHashLSH

from pipeline.context import Ctx, Opts
from pipeline.journal import unit_key
from pipeline.runner import RunStats, run_whole

VERSION = 1
THRESHOLD = 0.8
NUM_PERM = 128
FAMILY_KINDS = frozenset({"skill", "agent", "command"})
UPSTREAM_LIBS = frozenset({"anthropics/skills", "anthropics/claude-code", "anthropics/claude-plugins-official",
                           "obra/superpowers", "wshobson/agents", "wshobson/commands"})


@dataclass(frozen=True)
class Doc:
    artifact_id: str
    kind: str
    repo: str
    path: str
    blob_sha: str
    text: str
    created_at: str | None
    family_key: str | None


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


def family_key(kind: str, path: str, parsed: dict) -> str | None:
    if kind not in FAMILY_KINDS:
        return None
    name = (parsed.get("frontmatter") or {}).get("name")
    if isinstance(name, str) and name.strip():
        return re.sub(r"\s+", "-", name.strip().lower())
    parts = path.split("/")
    stem = parts[-2] if kind == "skill" and len(parts) >= 2 else parts[-1].rsplit(".", 1)[0]
    return stem.lower()


def cluster_docs(docs: list[Doc], threshold: float = THRESHOLD) -> list[list[int]]:
    uf = UnionFind(len(docs))
    first: dict[str, int] = {}
    for i, d in enumerate(docs):
        uf.union(i, first.setdefault("sha:" + d.blob_sha, i))
    normed = [normalize(d.text, d.repo) for d in docs]
    for i, t in enumerate(normed):
        uf.union(i, first.setdefault("norm:" + hashlib.sha256(t.encode()).hexdigest(), i))
    hashes: dict[int, MinHash] = {}
    for g in sorted(uf.roots()):
        m = MinHash(num_perm=NUM_PERM, seed=1)
        for s in shingles(normed[g]):
            m.update(s.encode())
        hashes[g] = m
    lsh = MinHashLSH(threshold=threshold, num_perm=NUM_PERM)
    for g, m in hashes.items():
        lsh.insert(str(g), m)
    for g, m in hashes.items():
        for other in lsh.query(m):
            o = int(other)
            if o != g and m.jaccard(hashes[o]) >= threshold:
                uf.union(g, o)
    members: dict[int, list[int]] = {}
    for i in range(len(docs)):
        members.setdefault(uf.find(i), []).append(i)
    return sorted(members.values())


def _ws(t: str) -> str:
    return re.sub(r"\s+", " ", t.lower()).strip()


def mutation_class(copy: Doc, origin: Doc) -> str:
    if copy.blob_sha == origin.blob_sha:
        return "verbatim"
    nc, no = normalize(copy.text, copy.repo), normalize(origin.text, origin.repo)
    if nc == no:
        return "verbatim" if _ws(copy.text) == _ws(origin.text) else "retargeted"
    a, b = shingles(nc), shingles(no)
    inter = len(a & b)
    if a and inter / len(a) >= 0.9 and len(a) < len(b):
        return "trimmed"
    if b and inter / len(b) >= 0.9 and len(a) > len(b):
        return "extended"
    return "rewritten"


def _tier(copy: Doc, origin: Doc) -> str:
    if copy.blob_sha == origin.blob_sha:
        return "exact"
    if normalize(copy.text, copy.repo) == normalize(origin.text, origin.repo):
        return "normalized"
    return "minhash"


def dedup(docs: list[Doc]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"clusters": [], "membership": [], "lineage": [], "mutations": []}
    by_kind: dict[str, list[Doc]] = {}
    for d in docs:
        by_kind.setdefault(d.kind, []).append(d)
    for kind, kdocs in sorted(by_kind.items()):
        kdocs = sorted(kdocs, key=lambda d: d.artifact_id)
        for idx in cluster_docs(kdocs):
            o = kdocs[min(idx, key=lambda i: (kdocs[i].created_at or "9999", kdocs[i].repo, kdocs[i].path))]
            cid = "c-" + unit_key(kind, min(kdocs[i].blob_sha for i in idx))[:16]
            repos = sorted({kdocs[i].repo for i in idx})
            out["clusters"].append({"cluster_id": cid, "kind": kind, "canonical_artifact": o.artifact_id,
                                    "size": len(idx)})
            out["lineage"].append({"cluster_id": cid, "origin_repo": o.repo, "origin_basis": "repo_created_at",
                                   "upstream_lib": next((r for r in repos if r in UPSTREAM_LIBS), None)})
            for i in sorted(idx, key=lambda i: kdocs[i].artifact_id):
                d = kdocs[i]
                is_origin = d.artifact_id == o.artifact_id
                out["membership"].append({"artifact_id": d.artifact_id, "cluster_id": cid,
                                          "tier": "origin" if is_origin else _tier(d, o),
                                          "family_key": d.family_key})
                if not is_origin:
                    out["mutations"].append({"artifact_id": d.artifact_id, "cluster_id": cid,
                                             "mutation_class": mutation_class(d, o)})
    return out


def run(ctx: Ctx, opts: Opts) -> RunStats:
    arts = ctx.tables.read("artifacts")
    created = {r["repo"]: r["created_at"] for r in ctx.tables.read("repos")}
    fp = unit_key(VERSION, THRESHOLD,
                  sorted((a["artifact_id"], a["blob_sha"], hashlib.sha256(a["parsed_json"].encode()).hexdigest())
                         for a in arts),
                  sorted((r, created.get(r)) for r in {a["repo"] for a in arts}))

    def work() -> dict[str, list[dict]]:
        docs = [Doc(a["artifact_id"], a["kind"], a["repo"], a["path"], a["blob_sha"], ctx.blobs.get(a["blob_sha"]),
                    created.get(a["repo"]), family_key(a["kind"], a["path"], json.loads(a["parsed_json"])))
                for a in arts]
        return dedup(docs)

    return run_whole(ctx, "s4", fp, work)
