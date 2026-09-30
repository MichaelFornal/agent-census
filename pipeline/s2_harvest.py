"""S2 harvest: batched GraphQL for repo metadata and the harness tree, then the blobs not yet stored
(PRD §4 S2). Batches start at 25 repos (PRD §9.1) and halve on a whole-batch failure.
"""
import json

import httpx

from pipeline.context import Ctx, Opts
from pipeline.fixtures import fixture_files, fixture_repos, git_blob_sha
from pipeline.gh import GraphQLClient, github_token
from pipeline.journal import unit_key
from pipeline.kinds import PARSED_KINDS, classify
from pipeline.runner import RunStats, Unit, run_batched, run_whole
from pipeline.store import BlobStore

VERSION = 1
BATCH = 25
TREE_DEPTH = 4  # entry levels under .claude: skills/<name>/scripts/<file>
ROOT_FILES = ["CLAUDE.md", ".mcp.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"]
MAX_FETCH_BYTES = 200_000
BLOB_BATCH = 40
BLOB_BATCH_BYTES = 400_000
REPO_FIELDS = ("nameWithOwner stargazerCount isFork isTemplate createdAt pushedAt "
               "primaryLanguage { name } licenseInfo { spdxId } defaultBranchRef { target { oid } }")
BLOB_META = "... on Blob { oid byteSize isBinary }"


def tree_fragment(depth: int) -> str:
    sub = f" ... on Tree {{ {tree_fragment(depth - 1)} }}" if depth > 1 else ""
    return f"entries {{ name type oid object {{ ... on Blob {{ byteSize }}{sub} }} }}"


def _repo_args(repo: str) -> str:
    owner, name = repo.split("/", 1)
    return f"owner: {json.dumps(owner)}, name: {json.dumps(name)}"


def meta_query(batch: list[tuple[str, list[str]]]) -> str:
    parts = []
    for i, (repo, extra) in enumerate(batch):
        files = " ".join(f"f{j}: object(expression: {json.dumps('HEAD:' + p)}) {{ {BLOB_META} }}"
                         for j, p in enumerate(ROOT_FILES + extra))
        parts.append(f"r{i}: repository({_repo_args(repo)}) {{ {REPO_FIELDS} "
                     f"claude: object(expression: \"HEAD:.claude\") {{ ... on Tree {{ {tree_fragment(TREE_DEPTH)} }} }} "
                     f"{files} }}")
    return "query { rateLimit { cost remaining } " + " ".join(parts) + " }"


def flatten(tree: dict | None, prefix: str) -> tuple[list[dict], int]:
    """Blob entries under prefix as {path, oid, size}, and the number of subtrees cut off by the depth limit."""
    files, cut = [], 0
    for e in (tree or {}).get("entries") or []:
        path = f"{prefix}/{e['name']}"
        obj = e.get("object") or {}
        if e["type"] == "blob":
            files.append({"path": path, "oid": e["oid"], "size": obj.get("byteSize")})
        elif e["type"] == "tree":
            if "entries" in obj:
                sub, c = flatten(obj, path)
                files += sub
                cut += c
            else:
                cut += 1
    return files, cut


def parse_meta(batch: list[tuple[str, list[str]]], body: dict) -> tuple[list[dict], list[dict]]:
    data = body.get("data") or {}
    repos, files = [], []
    for i, (repo, extra) in enumerate(batch):
        r = data.get(f"r{i}")
        if r is None:
            repos.append({"repo": repo, "missing": True, "error": "not_found", "canary": False})
            continue
        entries, cut = flatten(r.get("claude"), ".claude")
        for j, p in enumerate(ROOT_FILES + extra):
            b = r.get(f"f{j}")
            if b:
                entries.append({"path": p, "oid": b["oid"], "size": b["byteSize"], "binary": b["isBinary"]})
        seen: set[str] = set()
        for e in entries:
            if e["path"] not in seen:
                seen.add(e["path"])
                files.append({"repo": repo, **e})
        repos.append({
            "repo": repo, "missing": False, "error": None, "canary": False,
            "stars": r.get("stargazerCount"), "is_fork": r.get("isFork"), "is_template": r.get("isTemplate"),
            "created_at": r.get("createdAt"), "pushed_at": r.get("pushedAt"),
            "language": (r.get("primaryLanguage") or {}).get("name"),
            "license": (r.get("licenseInfo") or {}).get("spdxId"),
            "head_oid": ((r.get("defaultBranchRef") or {}).get("target") or {}).get("oid"),
            "tree_truncated": cut,
        })
    return repos, files


def is_retryable(status: int, body: dict) -> bool:
    if status in (0, 502, 503, 504):
        return True
    # A whole-batch failure (GitHub's 10 s execution limit, resource limits) returns data null with errors.
    return not body.get("data") and bool(body.get("errors"))


def _post(client: GraphQLClient, query: str) -> tuple[int, dict]:
    try:
        status, body, _ = client.post(query)
    except httpx.TimeoutException:
        return 0, {"errors": [{"type": "TIMEOUT", "message": "client timeout"}]}
    if status == 401:
        raise SystemExit("GitHub token rejected (401)")
    return status, body


def fetch_meta(client: GraphQLClient, batch: list[tuple[str, list[str]]]) -> tuple[list[dict], list[dict]]:
    status, body = _post(client, meta_query(batch))
    if is_retryable(status, body):
        if len(batch) > 1:
            mid = len(batch) // 2
            a, b = fetch_meta(client, batch[:mid]), fetch_meta(client, batch[mid:])
            return a[0] + b[0], a[1] + b[1]
        return [{"repo": batch[0][0], "missing": True, "error": f"graphql_{status}", "canary": False}], []
    return parse_meta(batch, body)


def blob_query(group: list[tuple[str, list[str]]]) -> str:
    parts = []
    for i, (repo, oids) in enumerate(group):
        blobs = " ".join(f"b{j}: object(oid: {json.dumps(o)}) {{ ... on Blob {{ oid isBinary isTruncated text }} }}"
                         for j, o in enumerate(oids))
        parts.append(f"r{i}: repository({_repo_args(repo)}) {{ {blobs} }}")
    return "query { " + " ".join(parts) + " }"


def plan_blob_batches(wanted: list[dict]) -> list[list[dict]]:
    batches, cur, size = [], [], 0
    for w in wanted:
        if cur and (len(cur) >= BLOB_BATCH or size + w["size"] > BLOB_BATCH_BYTES):
            batches.append(cur)
            cur, size = [], 0
        cur.append(w)
        size += w["size"]
    if cur:
        batches.append(cur)
    return batches


def fetch_blobs(client: GraphQLClient, blobs: list[dict], store: BlobStore) -> tuple[dict[str, str], list[dict]]:
    """Store each blob's redacted text. Returns ({oid: "" or skip reason}, redaction rows)."""
    group: dict[str, list[str]] = {}
    for b in blobs:
        group.setdefault(b["repo"], []).append(b["oid"])
    items = list(group.items())
    status_code, body = _post(client, blob_query(items))
    if is_retryable(status_code, body):
        if len(blobs) > 1:
            mid = len(blobs) // 2
            s1, r1 = fetch_blobs(client, blobs[:mid], store)
            s2_, r2 = fetch_blobs(client, blobs[mid:], store)
            return {**s1, **s2_}, r1 + r2
        return {blobs[0]["oid"]: "fetch_error"}, []
    data = body.get("data") or {}
    status: dict[str, str] = {}
    redactions: list[dict] = []
    for i, (_, oids) in enumerate(items):
        r = data.get(f"r{i}") or {}
        for j, oid in enumerate(oids):
            b = r.get(f"b{j}")
            if b is None:
                status[oid] = "missing"
            elif b["isBinary"]:
                status[oid] = "binary"
            elif b["isTruncated"] or b.get("text") is None:
                status[oid] = "truncated"
            else:
                redactions += [{"blob_sha": oid, "rule": k, "n": n} for k, n in store.put(oid, b["text"]).items()]
                status[oid] = ""
    return status, redactions


def harvest_batch(ctx: Ctx, client: GraphQLClient, payloads: list[tuple[str, list[str]]]) -> dict[str, list[dict]]:
    repos, files = fetch_meta(client, payloads)
    rows, wanted, queued = [], [], set()
    for f in files:
        kind = classify(f["path"]) or "other"
        reason = None
        if kind not in PARSED_KINDS:
            reason = "not_fetched_kind"
        elif f.get("binary"):
            reason = "binary"
        elif (f["size"] or 0) > MAX_FETCH_BYTES:
            reason = "too_large"
        elif not ctx.blobs.has(f["oid"]) and f["oid"] not in queued:
            queued.add(f["oid"])
            wanted.append({"repo": f["repo"], "oid": f["oid"], "size": f["size"] or 0})
        rows.append({"repo": f["repo"], "path": f["path"], "kind": kind, "blob_sha": f["oid"], "size": f["size"],
                     "skip_reason": reason})
    status: dict[str, str] = {}
    redactions: list[dict] = []
    for chunk in plan_blob_batches(wanted):
        s, r = fetch_blobs(client, chunk, ctx.blobs)
        status.update(s)
        redactions += r
    for row in rows:
        if row["skip_reason"] is None and status.get(row["blob_sha"]):
            row["skip_reason"] = status[row["blob_sha"]]
        row["fetched"] = row["skip_reason"] is None and ctx.blobs.has(row["blob_sha"])
    return {"repos": repos, "harness_files": rows, "redactions": redactions}


def _run_fixtures(ctx: Ctx) -> RunStats:
    repos = fixture_repos(ctx.fixtures)
    files = fixture_files(ctx.fixtures)
    fp = unit_key(VERSION, repos, [(f.repo, f.path, git_blob_sha(f.data)) for f in files])

    def work() -> dict[str, list[dict]]:
        rows, redactions = [], []
        for f in files:
            oid, kind = git_blob_sha(f.data), classify(f.path) or "other"
            fetch = kind in PARSED_KINDS
            if fetch and not ctx.blobs.has(oid):
                redactions += [{"blob_sha": oid, "rule": k, "n": n} for k, n in ctx.blobs.put(oid, f.data.decode()).items()]
            rows.append({"repo": f.repo, "path": f.path, "kind": kind, "blob_sha": oid, "size": len(f.data),
                         "fetched": fetch, "skip_reason": None if fetch else "not_fetched_kind"})
        return {"repos": repos, "harness_files": rows, "redactions": redactions}

    return run_whole(ctx, "s2", fp, work)


def run(ctx: Ctx, opts: Opts) -> RunStats:
    if ctx.fixtures:
        return _run_fixtures(ctx)
    nested: dict[str, set[str]] = {}
    for h in ctx.tables.read("repo_hits"):
        extra = nested.setdefault(h["repo"], set())
        if h["component"] == "claude_md" and h["path"] != "CLAUDE.md" and not h["path"].startswith(".claude/"):
            extra.add(h["path"])
    # Ordered by unit key (a hash), so `--limit N` harvests a stable pseudo-random N of the discovered repos.
    units = sorted((Unit(unit_key("s2", VERSION, repo), (repo, sorted(extra))) for repo, extra in nested.items()),
                   key=lambda u: u.key)
    client = GraphQLClient(github_token())
    stats = run_batched(ctx, "s2", units, lambda batch: harvest_batch(ctx, client, [u.payload for u in batch]),
                        BATCH, opts.limit)
    print(f"s2 graphql: {client.stats}", flush=True)
    return stats
