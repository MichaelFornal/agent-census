"""Batched GraphQL harvest of the sample, sweeping batch size (PRD §4 S2). Throwaway code.

Run: uv run python -m m0.harvest
"""
import json
import sys
from statistics import median

import httpx

from m0.gh import GraphQLClient, github_token
from m0.paths import append_jsonl, blob_path, data_path, read_jsonl, write_metrics
from m0.redact import redact

REPO_FIELDS = ("nameWithOwner stargazerCount isFork isTemplate createdAt pushedAt "
               "primaryLanguage { name } licenseInfo { spdxId } defaultBranchRef { target { oid } }")
BLOB = "... on Blob { oid byteSize isBinary isTruncated text }"
TREE = "... on Tree { entries { path type object { ... on Tree { entries { path type } } } } }"
SWEEP = [10, 25, 50, 100]
SWEEP_BATCHES = 40
STEADY = 50
POINTS_PER_HOUR = 5000


def build_query(batch: list[tuple[str, list[str]]]) -> str:
    parts = []
    for i, (full, paths) in enumerate(batch):
        owner, name = full.split("/", 1)
        files = " ".join(f"f{j}: object(expression: {json.dumps('HEAD:' + p)}) {{ {BLOB} }}"
                         for j, p in enumerate(paths))
        parts.append(f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) "
                     f"{{ {REPO_FIELDS} tree: object(expression: \"HEAD:.claude\") {{ {TREE} }} {files} }}")
    return "query { rateLimit { cost remaining resetAt } " + " ".join(parts) + " }"


def _count_entries(tree: dict | None) -> int:
    if not tree:
        return 0
    return sum(1 + _count_entries(e.get("object")) for e in tree.get("entries") or [])


def parse_response(batch: list[tuple[str, list[str]]], body: dict):
    data = body.get("data") or {}
    errors = body.get("errors") or []
    repos: list[dict] = []
    files: list[dict] = []
    for i, (full, paths) in enumerate(batch):
        r = data.get(f"r{i}")
        if r is None:
            repos.append({"repo": full, "missing": True})
            files += [{"repo": full, "path": p, "missing": True} for p in paths]
            continue
        repos.append({
            "repo": full, "missing": False,
            "stars": r.get("stargazerCount"), "is_fork": r.get("isFork"), "is_template": r.get("isTemplate"),
            "created_at": r.get("createdAt"), "pushed_at": r.get("pushedAt"),
            "language": (r.get("primaryLanguage") or {}).get("name"),
            "license": (r.get("licenseInfo") or {}).get("spdxId"),
            "head_oid": ((r.get("defaultBranchRef") or {}).get("target") or {}).get("oid"),
            "claude_tree_entries": _count_entries(r.get("tree")),
        })
        for j, p in enumerate(paths):
            b = r.get(f"f{j}")
            if b is None:
                files.append({"repo": full, "path": p, "missing": True})
            else:
                files.append({"repo": full, "path": p, "missing": False, "oid": b["oid"],
                              "size": b["byteSize"], "binary": b["isBinary"],
                              "truncated": b["isTruncated"], "text": b.get("text")})
    cost = (data.get("rateLimit") or {}).get("cost")
    return repos, files, cost, errors


def is_retryable(status: int, body: dict) -> bool:
    if status in (0, 502, 503, 504):
        return True
    if body.get("data"):
        return False
    for e in body.get("errors") or []:
        msg = (e.get("message") or "").lower()
        if e.get("type") in ("RESOURCE_LIMITS_EXCEEDED", "TIMEOUT") or "timeout" in msg or "timed out" in msg:
            return True
    return False


def store_file(f: dict) -> dict:
    f = dict(f)
    text = f.pop("text", None)
    f["stored"] = False
    if text is not None and not f.get("binary"):
        clean, counts = redact(text)
        p = blob_path(f["oid"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(clean)
        f["stored"] = True
        f["redactions"] = counts
    return f


def summarize(batches: list[dict], files: list[dict], repos: list[dict]) -> dict:
    by_size: dict[int, list[dict]] = {}
    for b in batches:
        by_size.setdefault(b["batch_size"], []).append(b)
    rows = {}
    for size, bs in sorted(by_size.items()):
        ok = [b for b in bs if not b["retried"]]
        lat = sorted(b["latency_s"] for b in ok)
        costs = [b["cost"] for b in ok if b["cost"] is not None]
        med_lat = median(lat) if lat else None
        rows[str(size)] = {
            "batches": len(bs),
            "retry_rate": (len(bs) - len(ok)) / len(bs),
            "median_cost": median(costs) if costs else None,
            "median_latency_s": med_lat,
            "p90_latency_s": lat[int(0.9 * (len(lat) - 1))] if lat else None,
            "repos_per_hour_latency_bound": size * 3600 / med_lat if med_lat else None,
        }
    ok_all = [b for b in batches if not b["retried"]]
    fetched = sum(b["batch_size"] for b in ok_all)
    points = sum(b["cost"] or 0 for b in ok_all)
    ppr = points / fetched if fetched else None
    return {
        "by_batch_size": rows,
        "repos_fetched": fetched,
        "points_per_repo": ppr,
        "repos_per_hour_points_bound": POINTS_PER_HOUR / ppr if ppr else None,
        "files": len(files),
        "files_missing": sum(1 for f in files if f.get("missing")),
        "files_binary": sum(1 for f in files if f.get("binary")),
        "files_truncated": sum(1 for f in files if f.get("truncated")),
        "repos_missing": sum(1 for r in repos if r.get("missing")),
        "secrets_redacted": sum(sum((f.get("redactions") or {}).values()) for f in files),
    }


def main() -> None:
    sample = read_jsonl(data_path("sample.jsonl"))
    kinds = {(s["repo"], s["path"]): s["kind"] for s in sample}
    by_repo: dict[str, list[str]] = {}
    for s in sample:
        by_repo.setdefault(s["repo"], []).append(s["path"])
    files_p, repos_p, batches_p = data_path("files.jsonl"), data_path("repos.jsonl"), data_path("graphql_batches.jsonl")
    done = {r["repo"] for r in read_jsonl(repos_p)}
    todo = sorted(r for r in by_repo if r not in done)
    client = GraphQLClient(github_token())
    k = len(read_jsonl(batches_p))
    shrink_to: int | None = None
    i = 0
    while i < len(todo):
        size = shrink_to or (SWEEP[k % len(SWEEP)] if k < SWEEP_BATCHES else STEADY)
        chunk = todo[i:i + size]
        batch = [(r, by_repo[r]) for r in chunk]
        try:
            status, body, latency = client.post(build_query(batch))
        except httpx.TimeoutException:
            status, body, latency = 0, {"errors": [{"type": "TIMEOUT", "message": "client timeout"}]}, client.timeout
        if status == 401:
            raise SystemExit("GitHub token rejected (401)")
        retry = is_retryable(status, body) and len(chunk) > 1
        append_jsonl(batches_p, {
            "batch_size": len(chunk), "status": status, "latency_s": latency,
            "cost": ((body.get("data") or {}).get("rateLimit") or {}).get("cost"),
            "n_errors": len(body.get("errors") or []), "retried": retry,
        })
        k += 1
        if retry:
            shrink_to = max(1, len(chunk) // 2)
            continue
        shrink_to = None
        repos, files, _, _ = parse_response(batch, body)
        for f in files:
            f["kind"] = kinds[(f["repo"], f["path"])]
            append_jsonl(files_p, store_file(f))
        for r in repos:
            append_jsonl(repos_p, r)
        i += len(chunk)
        print(f"{i}/{len(todo)} repos, batch={len(chunk)} status={status} {latency:.1f}s",
              file=sys.stderr, flush=True)
    write_metrics("graphql", summarize(read_jsonl(batches_p), read_jsonl(files_p), read_jsonl(repos_p)))


if __name__ == "__main__":
    main()
