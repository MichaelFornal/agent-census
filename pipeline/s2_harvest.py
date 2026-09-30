"""S2 harvest (PRD §4 S2). Repo metadata and the top level of `.claude` come from batched GraphQL (25 repos,
halved on a whole-batch failure). A repo whose `.claude` has subdirectories gets one REST git-trees call,
which lists the whole tree at any depth. Blobs not yet stored are fetched by batched GraphQL and redacted
on write.

Nothing transient is recorded as final. A repo GitHub could not serve is deferred (runner.Partial) and tried
again on later runs. After runner.MAX_ATTEMPTS the final pass records what is reachable and why the rest is
not. When GitHub itself is failing (the health probe fails too), the stage stops instead.
"""
import json
from dataclasses import dataclass

from pipeline.context import Ctx, Opts
from pipeline.fixtures import fixture_files, fixture_repos, git_blob_sha
from pipeline.gh import GraphQLClient, RestClient, github_token
from pipeline.journal import unit_key
from pipeline.kinds import PARSED_KINDS, classify
from pipeline.runner import Partial, RunStats, StopStage, Unit, run_batched, run_whole
from pipeline.store import BlobStore

VERSION = 2  # 2: the .claude tree comes from REST git trees (was GraphQL nested to depth 4); transients are deferred
BATCH = 25
ROOT_FILES = ["CLAUDE.md", ".mcp.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"]
MAX_EXTRA = 20  # nested CLAUDE.md paths looked up in the meta query; the rest go in follow-up queries
EXTRA_BATCH = 60
MAX_FETCH_BYTES = 200_000
BLOB_BATCH = 40
BLOB_BATCH_BYTES = 400_000
REPO_FIELDS = ("nameWithOwner stargazerCount isFork isTemplate createdAt pushedAt "
               "primaryLanguage { name } licenseInfo { spdxId } defaultBranchRef { target { oid } }")
BLOB_META = "... on Blob { oid byteSize isBinary }"
CLAUDE_TOP = ('claude: object(expression: "HEAD:.claude") { ... on Tree { oid entries { name type oid '
              'object { ... on Blob { byteSize isBinary } } } } }')


@dataclass
class Found:
    row: dict  # the repos row
    files: list[dict]  # {path, oid, size, binary}
    tree_oid: str | None = None  # the .claude tree, when it has subdirectories still to list
    subdirs: int = 0


def _repo_args(repo: str) -> str:
    owner, name = repo.split("/", 1)
    return f"owner: {json.dumps(owner)}, name: {json.dumps(name)}"


def _path_fields(paths: list[str]) -> str:
    return " ".join(f"f{j}: object(expression: {json.dumps('HEAD:' + p)}) {{ {BLOB_META} }}"
                    for j, p in enumerate(paths))


def meta_query(batch: list[tuple[str, list[str]]]) -> str:
    parts = [f"r{i}: repository({_repo_args(repo)}) {{ {REPO_FIELDS} {CLAUDE_TOP} "
             f"{_path_fields(ROOT_FILES + extra[:MAX_EXTRA])} }}" for i, (repo, extra) in enumerate(batch)]
    return "query { rateLimit { cost remaining } " + " ".join(parts) + " }"


def _not_found(body: dict) -> set[tuple]:
    """GraphQL error paths of type NOT_FOUND, e.g. ("r3",) or ("r0", "b2")."""
    return {tuple(e["path"]) for e in body.get("errors") or [] if e.get("type") == "NOT_FOUND" and e.get("path")}


def _error_type(body: dict, alias: str) -> str:
    for e in body.get("errors") or []:
        if e.get("path") and e["path"][0] == alias:
            return e.get("type") or "unknown"
    return "unknown"


def _alias_failure(body: dict, alias: str) -> str | None:
    """A non-NOT_FOUND GraphQL error at or under this repo's alias, e.g. a nulled `claude`: the data is unreliable."""
    for e in body.get("errors") or []:
        if e.get("type") != "NOT_FOUND" and e.get("path") and e["path"][0] == alias:
            return f"graphql_error:{e.get('type') or 'unknown'}"
    return None


def _missing(repo: str, error: str) -> dict:
    return {"repo": repo, "missing": True, "error": error, "canary": False}


def parse_meta(batch: list[tuple[str, list[str]]], body: dict) -> tuple[dict[str, Found], dict[str, str]]:
    """(repos GitHub answered for, {repo: error} for repos it did not). Only NOT_FOUND is final."""
    data = body.get("data") or {}
    gone = _not_found(body)
    found: dict[str, Found] = {}
    failed: dict[str, str] = {}
    for i, (repo, extra) in enumerate(batch):
        r = data.get(f"r{i}")
        if r is None:
            if (f"r{i}",) in gone:
                found[repo] = Found(_missing(repo, "not_found"), [])
            else:
                failed[repo] = f"graphql_error:{_error_type(body, f'r{i}')}"
            continue
        if error := _alias_failure(body, f"r{i}"):
            failed[repo] = error
            continue
        claude = r.get("claude") or {}
        files, subdirs = [], 0
        for e in claude.get("entries") or []:
            if e["type"] == "blob":
                obj = e.get("object") or {}
                files.append({"path": f".claude/{e['name']}", "oid": e["oid"], "size": obj.get("byteSize"),
                              "binary": obj.get("isBinary")})
            elif e["type"] == "tree":
                subdirs += 1
        for j, p in enumerate(ROOT_FILES + extra[:MAX_EXTRA]):
            b = r.get(f"f{j}")
            if b:
                files.append({"path": p, "oid": b["oid"], "size": b["byteSize"], "binary": b["isBinary"]})
        row = {
            "repo": repo, "missing": False, "error": None, "canary": False,
            "stars": r.get("stargazerCount"), "is_fork": r.get("isFork"), "is_template": r.get("isTemplate"),
            "created_at": r.get("createdAt"), "pushed_at": r.get("pushedAt"),
            "language": (r.get("primaryLanguage") or {}).get("name"),
            "license": (r.get("licenseInfo") or {}).get("spdxId"),
            "head_oid": ((r.get("defaultBranchRef") or {}).get("target") or {}).get("oid"),
            "tree_truncated": 0,
        }
        found[repo] = Found(row, files, claude.get("oid") if subdirs else None, subdirs)
    return found, failed


def is_retryable(status: int, body: dict) -> bool:
    if status in (0, 502, 503, 504):
        return True
    # A whole-batch failure (GitHub's 10 s execution limit, resource limits) returns data null with errors.
    return not body.get("data") and bool(body.get("errors"))


def _post(client: GraphQLClient, query: str) -> tuple[int, dict]:
    status, body, _ = client.post(query)
    if status == 401:
        raise SystemExit("GitHub token rejected (401)")
    return status, body


def _stop_if_down(client: GraphQLClient, what: str, status: int) -> None:
    if not client.healthy():
        raise StopStage(f"GitHub GraphQL is failing ({what} query: status {status}); "
                        "stopped so nothing transient is recorded")


def _halves(items: list) -> tuple[list, list]:
    mid = len(items) // 2
    return items[:mid], items[mid:]


def fetch_meta(client: GraphQLClient, batch: list[tuple[str, list[str]]]) -> tuple[dict[str, Found], dict[str, str]]:
    status, body = _post(client, meta_query(batch))
    if is_retryable(status, body):
        _stop_if_down(client, "meta", status)
        if len(batch) > 1:
            a, b = (fetch_meta(client, half) for half in _halves(batch))
            return {**a[0], **b[0]}, {**a[1], **b[1]}
        return {}, {batch[0][0]: f"graphql_{status}"}
    return parse_meta(batch, body)


def _group(pairs: list[tuple[str, str]]) -> list[tuple[str, list[str]]]:
    by_repo: dict[str, list[str]] = {}
    for repo, item in pairs:
        by_repo.setdefault(repo, []).append(item)
    return list(by_repo.items())


def fetch_extra(client: GraphQLClient, wanted: list[tuple[str, str]]) -> tuple[list[dict], dict[str, str]]:
    """Blob metadata for (repo, path) pairs. Returns (file entries carrying `repo`, {repo: error})."""
    items = _group(wanted)
    query = "query { " + " ".join(f"r{i}: repository({_repo_args(repo)}) {{ {_path_fields(paths)} }}"
                                  for i, (repo, paths) in enumerate(items)) + " }"
    status, body = _post(client, query)
    if is_retryable(status, body):
        _stop_if_down(client, "paths", status)
        if len(wanted) > 1:
            a, b = (fetch_extra(client, half) for half in _halves(wanted))
            return a[0] + b[0], {**a[1], **b[1]}
        return [], {wanted[0][0]: f"graphql_{status}"}
    if status != 200:
        return [], {repo: f"graphql_{status}" for repo, _ in items}
    data = body.get("data") or {}
    out, errors = [], {}
    for i, (repo, paths) in enumerate(items):
        r = data.get(f"r{i}")
        if r is None:
            errors[repo] = f"graphql_error:{_error_type(body, f'r{i}')}"
            continue
        if error := _alias_failure(body, f"r{i}"):
            errors[repo] = error
            continue
        for j, p in enumerate(paths):
            b = r.get(f"f{j}")
            if b:
                out.append({"repo": repo, "path": p, "oid": b["oid"], "size": b["byteSize"], "binary": b["isBinary"]})
    return out, errors


def fetch_tree(rest: RestClient, repo: str, oid: str) -> tuple[list[dict], bool, str | None]:
    """Every blob under .claude, at any depth. Returns (files, truncated by GitHub, error)."""
    status, body = rest.get(f"/repos/{repo}/git/trees/{oid}", {"recursive": "1"})
    if status != 200 or not isinstance(body.get("tree"), list):
        return [], False, f"rest_tree_{status}"
    files = [{"path": f".claude/{e['path']}", "oid": e["sha"], "size": e.get("size")}
             for e in body["tree"] if e.get("type") == "blob"]
    return files, bool(body.get("truncated")), None


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


def fetch_blobs(client: GraphQLClient, blobs: list[dict], store: BlobStore) -> dict[str, str]:
    """Store each blob's redacted text. Returns {oid: "" when stored, else why not}."""
    items = _group([(b["repo"], b["oid"]) for b in blobs])
    status_code, body = _post(client, blob_query(items))
    if is_retryable(status_code, body):
        _stop_if_down(client, "blob", status_code)
        if len(blobs) > 1:
            a, b = (fetch_blobs(client, half, store) for half in _halves(blobs))
            return {**a, **b}
        return {blobs[0]["oid"]: "fetch_error"}
    data = body.get("data") or {}
    gone = _not_found(body)
    status: dict[str, str] = {}
    for i, (_, oids) in enumerate(items):
        r = data.get(f"r{i}") or {}
        for j, oid in enumerate(oids):
            b = r.get(f"b{j}")
            if b is None:
                is_gone = (f"r{i}",) in gone or (f"r{i}", f"b{j}") in gone
                status[oid] = "missing" if is_gone else "fetch_error"
            elif b["isBinary"]:
                status[oid] = "binary"
            elif b["isTruncated"] or b.get("text") is None:
                status[oid] = "truncated"
            else:
                store.put(oid, b["text"])
                status[oid] = ""
    return status


def _redaction_rows(ctx: Ctx, oids: list[str]) -> list[dict]:
    """Redaction rows from the counts stored with each blob, so a kill after a blob write loses nothing."""
    rows = []
    for oid in dict.fromkeys(oids):
        rows += [{"blob_sha": oid, "rule": k, "n": n} for k, n in ctx.blobs.redaction_counts(oid).items() if n > 0]
    return rows


def harvest_batch(ctx: Ctx, gql: GraphQLClient, rest: RestClient, units: list[Unit], final: bool = False) -> Partial:
    """final=True is the last attempt for these units: record what is reachable and why the rest is not."""
    payloads = [u.payload for u in units]
    found, failed = fetch_meta(gql, payloads)
    more = [(repo, p) for repo, extra in payloads if repo in found and not found[repo].row["missing"]
            for p in extra[MAX_EXTRA:]]
    for i in range(0, len(more), EXTRA_BATCH):
        entries, errors = fetch_extra(gql, more[i:i + EXTRA_BATCH])
        for e in entries:
            found[e.pop("repo")].files.append(e)
        failed.update(errors)
    tree_calls = tree_errors = 0
    for repo, f in found.items():
        if f.tree_oid is None or repo in failed:
            continue
        tree_calls += 1
        files, truncated, error = fetch_tree(rest, repo, f.tree_oid)
        if error:
            tree_errors += 1
            failed[repo] = error
            f.row["tree_truncated"] = f.subdirs  # the final pass keeps the top level and says what is missing
        else:
            f.files = [x for x in f.files if not x["path"].startswith(".claude/")] + files
            f.row["tree_truncated"] = int(truncated)
    if tree_calls >= 3 and tree_errors == tree_calls:
        raise StopStage("every REST tree request in the batch failed; stopped so nothing transient is recorded")

    rows, wanted, queued = [], [], set()
    for repo, f in found.items():
        if repo in failed and not final:
            continue
        seen: set[str] = set()
        for e in f.files:
            if e["path"] in seen:
                continue
            seen.add(e["path"])
            kind = classify(e["path"]) or "other"
            reason = None
            if kind not in PARSED_KINDS:
                reason = "not_fetched_kind"
            elif e.get("binary"):
                reason = "binary"
            elif (e["size"] or 0) > MAX_FETCH_BYTES:
                reason = "too_large"
            elif not ctx.blobs.has(e["oid"]) and e["oid"] not in queued:
                queued.add(e["oid"])
                wanted.append({"repo": repo, "oid": e["oid"], "size": e["size"] or 0})
            rows.append({"repo": repo, "path": e["path"], "kind": kind, "blob_sha": e["oid"], "size": e["size"],
                         "skip_reason": reason})
    status: dict[str, str] = {}
    for chunk in plan_blob_batches(wanted):
        status.update(fetch_blobs(gql, chunk, ctx.blobs))
    for row in rows:
        if row["skip_reason"] is None and not ctx.blobs.has(row["blob_sha"]):
            row["skip_reason"] = status.get(row["blob_sha"]) or "fetch_error"
            if row["skip_reason"] == "fetch_error":
                failed.setdefault(row["repo"], "blob_fetch_error")
        row["fetched"] = row["skip_reason"] is None

    repos = []
    for repo, _ in payloads:
        if repo in failed and not final:
            continue
        if repo not in found:
            repos.append(_missing(repo, f"unreachable:{failed[repo]}"))
            continue
        if repo in failed:
            found[repo].row["error"] = f"partial:{failed[repo]}"
        repos.append(found[repo].row)
    rows = [r for r in rows if final or r["repo"] not in failed]
    redactions = _redaction_rows(ctx, [r["blob_sha"] for r in rows if r["fetched"]])
    key_of = {u.payload[0]: u.key for u in units}
    deferred = {} if final else {key_of[repo]: error for repo, error in failed.items()}
    return Partial({"repos": repos, "harness_files": rows, "redactions": redactions}, deferred)


def _run_fixtures(ctx: Ctx) -> RunStats:
    repos = fixture_repos(ctx.fixtures)
    files = fixture_files(ctx.fixtures)
    fp = unit_key(VERSION, repos, [(f.repo, f.path, git_blob_sha(f.data)) for f in files])

    def work() -> dict[str, list[dict]]:
        rows = []
        for f in files:
            oid, kind = git_blob_sha(f.data), classify(f.path) or "other"
            fetch = kind in PARSED_KINDS
            if fetch and not ctx.blobs.has(oid):
                ctx.blobs.put(oid, f.data.decode())
            rows.append({"repo": f.repo, "path": f.path, "kind": kind, "blob_sha": oid, "size": len(f.data),
                         "fetched": fetch, "skip_reason": None if fetch else "not_fetched_kind"})
        redactions = _redaction_rows(ctx, [r["blob_sha"] for r in rows if r["fetched"]])
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
    gql, rest = GraphQLClient(github_token()), RestClient(github_token())
    stats = run_batched(ctx, "s2", units, lambda batch: harvest_batch(ctx, gql, rest, batch), BATCH, opts.limit,
                        give_up=lambda u, error: harvest_batch(ctx, gql, rest, [u], final=True).rows)
    print(f"s2 graphql: {gql.stats}; rest: {rest.stats}", flush=True)
    return stats
