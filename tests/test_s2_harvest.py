import json
import re

import httpx

from pipeline import s2_harvest as s2
from pipeline.context import Opts
from pipeline.gh import GraphQLClient, Pacer

OID = re.compile(r'object\(oid: "([0-9a-f]{40})"\)')


def client(handler):
    return GraphQLClient("t", transport=httpx.MockTransport(handler),
                         pacer=Pacer(base_interval=0.0, sleep=lambda s: None))


def blob_entry(name, oid, size=10):
    return {"name": name, "type": "blob", "oid": oid, "object": {"byteSize": size}}


def tree_entry(name, entries):
    return {"name": name, "type": "tree", "oid": "e" * 40, "object": {"entries": entries}}


def repo_node(entries=None, root_md=None):
    node = {"nameWithOwner": "x", "stargazerCount": 3, "isFork": False, "isTemplate": False,
            "createdAt": "2025-01-01T00:00:00Z", "pushedAt": "2026-01-01T00:00:00Z",
            "primaryLanguage": {"name": "Python"}, "licenseInfo": None,
            "defaultBranchRef": {"target": {"oid": "f" * 40}},
            "claude": {"entries": entries} if entries is not None else None}
    for j in range(len(s2.ROOT_FILES)):
        node[f"f{j}"] = None
    if root_md:
        node["f0"] = {"oid": root_md, "byteSize": 20, "isBinary": False}
    return node


def test_meta_query_nests_tree_to_depth():
    q = s2.meta_query([("o/r", ["docs/CLAUDE.md"])])
    assert q.count("... on Tree") == s2.TREE_DEPTH
    assert '"HEAD:docs/CLAUDE.md"' in q and '"HEAD:.mcp.json"' in q


def test_flatten_builds_full_paths_and_counts_cut_subtrees():
    tree = {"entries": [
        blob_entry("settings.json", "a" * 40),
        tree_entry("skills", [tree_entry("x", [blob_entry("SKILL.md", "b" * 40, size=99)])]),
        {"name": "deep", "type": "tree", "oid": "c" * 40, "object": {}},
    ]}
    files, cut = s2.flatten(tree, ".claude")
    assert files == [{"path": ".claude/settings.json", "oid": "a" * 40, "size": 10},
                     {"path": ".claude/skills/x/SKILL.md", "oid": "b" * 40, "size": 99}]
    assert cut == 1


def test_parse_meta_keeps_batch_when_one_repo_is_gone():
    body = {"data": {"r0": None, "r1": repo_node([blob_entry("settings.json", "a" * 40)], root_md="b" * 40)},
            "errors": [{"type": "NOT_FOUND"}]}
    repos, files = s2.parse_meta([("gone/repo", []), ("o/r", [])], body)
    assert repos[0] == {"repo": "gone/repo", "missing": True, "error": "not_found", "canary": False}
    assert repos[1]["head_oid"] == "f" * 40 and repos[1]["missing"] is False
    assert sorted(f["path"] for f in files) == [".claude/settings.json", "CLAUDE.md"]


def test_whole_batch_failure_shrinks_until_it_fits():
    seen = []

    def handler(req):
        n = json.loads(req.content)["query"].count("repository(")
        seen.append(n)
        if n > 1:
            return httpx.Response(502, text="Bad Gateway")
        return httpx.Response(200, json={"data": {"r0": repo_node()}})

    repos, _ = s2.fetch_meta(client(handler), [("o/a", []), ("o/b", [])])
    assert seen == [2, 1, 1]
    assert [r["repo"] for r in repos] == ["o/a", "o/b"]


def test_plan_blob_batches_respects_count_and_bytes():
    wanted = [{"repo": "o/r", "oid": f"{i:040x}", "size": 150_000} for i in range(5)]
    assert [len(b) for b in s2.plan_blob_batches(wanted)] == [2, 2, 1]


def test_harvest_batch_fetches_each_new_blob_once(ctx):
    shared, stored, secret_oid = "a" * 40, "b" * 40, "c" * 40
    ctx.blobs.put(stored, "already here\n")
    texts = {shared: '{"model": "sonnet"}\n', secret_oid: '{"env": {"CLOUD_API_KEY": "Zq8Xv2Lm9Pw4Rt7Ky3Nb"}}\n'}
    blob_queries = []

    def handler(req):
        q = json.loads(req.content)["query"]
        if "HEAD:.claude" in q:
            return httpx.Response(200, json={"data": {
                "r0": repo_node([blob_entry("settings.json", shared), blob_entry("settings.local.json", secret_oid)]),
                "r1": repo_node([blob_entry("settings.json", shared)], root_md=stored)}})
        blob_queries.append(q)
        data = {}
        for i, block in enumerate(q.split("repository(")[1:]):
            data[f"r{i}"] = {f"b{j}": {"oid": o, "isBinary": False, "isTruncated": False, "text": texts[o]}
                             for j, o in enumerate(OID.findall(block))}
        return httpx.Response(200, json={"data": data})

    out = s2.harvest_batch(ctx, client(handler), [("o/a", []), ("o/b", [])])
    assert sorted(OID.findall("".join(blob_queries))) == sorted([shared, secret_oid])
    assert len(out["harness_files"]) == 4 and all(f["fetched"] for f in out["harness_files"])
    assert "Zq8Xv2Lm9Pw4Rt7Ky3Nb" not in ctx.blobs.get(secret_oid)
    assert out["redactions"] == [{"blob_sha": secret_oid, "rule": "assigned_secret", "n": 1}]


def test_oversized_and_non_harness_files_are_recorded_not_fetched(ctx):
    def handler(req):
        return httpx.Response(200, json={"data": {"r0": repo_node([
            blob_entry("notes.txt", "d" * 40), blob_entry("settings.json", "e" * 40, size=300_000)])}})

    out = s2.harvest_batch(ctx, client(handler), [("o/a", [])])
    reasons = {f["path"]: f["skip_reason"] for f in out["harness_files"]}
    assert reasons == {".claude/notes.txt": "not_fetched_kind", ".claude/settings.json": "too_large"}
    assert not any(f["fetched"] for f in out["harness_files"])


def test_fixture_mode_stores_redacted_blobs(fctx):
    s2.run(fctx, Opts())
    assert len(fctx.tables.read("repos")) == 8
    files = fctx.tables.read("harness_files")
    assert all(fctx.blobs.has(f["blob_sha"]) for f in files if f["fetched"])
    assert not any("Zq8Xv2Lm9Pw4Rt7Ky3Nb" in fctx.blobs.get(f["blob_sha"]) for f in files if f["fetched"])
    assert s2.run(fctx, Opts()).units_skipped == 1
