import json
import re
from collections import namedtuple

import httpx
import pytest

from pipeline import s2_harvest as s2
from pipeline.context import Opts
from pipeline.gh import GraphQLClient, Pacer, RestClient
from pipeline.runner import MAX_ATTEMPTS, StopStage, Unit, write_state

ALIAS = re.compile(r'repository\(owner: "([^"]+)", name: "([^"]+)"\)')
EXPR = re.compile(r'(f\d+): object\(expression: "HEAD:([^"]+)"\)')
OID = re.compile(r'(b\d+): object\(oid: "([0-9a-f]{40})"\)')
SECRET = "Zq8Xv2Lm9Pw4Rt7Ky3Nb"
DEEP = ".claude/skills/x/scripts/lib/util.py"


def pacer():
    return Pacer(base_interval=0.0, sleep=lambda s: None)


def blob(name, oid, size=10):
    return {"name": name, "type": "blob", "oid": oid, "object": {"byteSize": size, "isBinary": False}}


def subdir(name):
    return {"name": name, "type": "tree", "oid": "e" * 40, "object": {}}


def node(entries=None):
    return {"nameWithOwner": "x", "stargazerCount": 3, "isFork": False, "isTemplate": False,
            "createdAt": "2025-01-01T00:00:00Z", "pushedAt": "2026-01-01T00:00:00Z",
            "primaryLanguage": {"name": "Python"}, "licenseInfo": None,
            "defaultBranchRef": {"target": {"oid": "f" * 40}},
            "claude": {"oid": "d" * 40, "entries": entries} if entries is not None else None}


def unit(repo, extra=()):
    return Unit(f"k:{repo}", (repo, list(extra)))


class Hub:
    """A scripted GitHub. repos: repo -> meta node (None = deleted). paths: (repo, path) -> blob oid for a
    HEAD:<path> lookup. texts: blob oid -> text, or a dict returned as the blob, or None for a null blob.
    trees: repo -> [(path under .claude, oid, size)] for the REST call; a repo not listed answers 502."""

    def __init__(self, repos, paths=None, texts=None, trees=None):
        self.repos, self.paths, self.texts, self.trees = repos, paths or {}, texts or {}, trees or {}
        self.healthy = True
        self.fail_meta: dict[str, int] = {}  # repo -> HTTP status for any meta query that names it
        self.fail_blobs: set[str] = set()  # a blob query naming one of these oids answers 502
        self.not_found_blobs: set[str] = set()
        self.truncated: set[str] = set()  # repos whose REST tree comes back truncated
        self.path_queries: list[str] = []
        self.blob_queries: list[str] = []
        self.tree_calls: list[str] = []
        self.meta_sizes: list[int] = []

    def gql(self, req):
        q = json.loads(req.content)["query"]
        names = [f"{o}/{n}" for o, n in ALIAS.findall(q)]
        if not names:  # the health probe
            return httpx.Response(200 if self.healthy else 502, json={"data": {"rateLimit": {"remaining": 1}}})
        if not self.healthy:
            return httpx.Response(502, text="Bad Gateway")
        is_meta = "HEAD:.claude" in q
        if is_meta:
            self.meta_sizes.append(len(names))
            bad = [self.fail_meta[n] for n in names if n in self.fail_meta]
            if bad:
                return httpx.Response(bad[0], text="Bad Gateway")
        elif OID.search(q):
            self.blob_queries.append(q)
            if self.fail_blobs & {o for _, o in OID.findall(q)}:
                return httpx.Response(502, text="Bad Gateway")
        else:
            self.path_queries.append(q)
        data, errors = {}, []
        for i, (name, block) in enumerate(zip(names, q.split("repository(")[1:])):
            if is_meta and self.repos[name] is None:
                data[f"r{i}"] = None
                errors.append({"type": "NOT_FOUND", "path": [f"r{i}"]})
                continue
            r = dict(self.repos[name]) if is_meta else {}
            for alias, path in EXPR.findall(block):
                oid = self.paths.get((name, path))
                r[alias] = {"oid": oid, "byteSize": 20, "isBinary": False} if oid else None
            for alias, oid in OID.findall(block):
                text = self.texts.get(oid)
                if isinstance(text, str):
                    r[alias] = {"oid": oid, "isBinary": False, "isTruncated": False, "text": text}
                else:
                    r[alias] = text
                    if oid in self.not_found_blobs:
                        errors.append({"type": "NOT_FOUND", "path": [f"r{i}", alias]})
            data[f"r{i}"] = r
        return httpx.Response(200, json={"data": data, **({"errors": errors} if errors else {})})

    def rest(self, req):
        repo = re.fullmatch(r"/repos/(.+)/git/trees/[0-9a-f]{40}", req.url.path)[1]
        self.tree_calls.append(repo)
        if repo not in self.trees:
            return httpx.Response(502, text="Bad Gateway")
        tree = [{"path": p, "type": "blob", "sha": oid, "size": size} for p, oid, size in self.trees[repo]]
        tree.append({"path": "skills", "type": "tree", "sha": "e" * 40})
        return httpx.Response(200, json={"sha": "d" * 40, "tree": tree, "truncated": repo in self.truncated})

    def clients(self):
        return (GraphQLClient("t", transport=httpx.MockTransport(self.gql), pacer=pacer()),
                RestClient("t", transport=httpx.MockTransport(self.rest), pacer=pacer()))


def harvest(ctx, hub, units, final=False):
    return s2.harvest_batch(ctx, *hub.clients(), units, final=final)


def tree_hub():
    skill = "b" * 40
    return Hub({"o/a": node([blob("settings.json", "a" * 40), subdir("skills")])},
               texts={"a" * 40: "{}\n", skill: "---\nname: x\n---\nbody\n"},
               trees={"o/a": [("settings.json", "a" * 40, 3), ("skills/x/SKILL.md", skill, 20),
                              ("skills/x/scripts/lib/util.py", "c" * 40, 5)]})


def test_meta_query_lists_only_the_top_of_dot_claude_and_caps_nested_paths():
    extra = [f"pkg{i}/CLAUDE.md" for i in range(s2.MAX_EXTRA + 5)]
    q = s2.meta_query([("o/r", extra)])
    assert q.count("... on Tree") == 1
    assert '"HEAD:pkg0/CLAUDE.md"' in q and '"HEAD:.mcp.json"' in q
    assert f'"HEAD:pkg{s2.MAX_EXTRA}/CLAUDE.md"' not in q


def test_deleted_repo_is_final_and_the_rest_of_the_batch_is_kept(ctx):
    hub = Hub({"gone/repo": None, "o/r": node([blob("settings.json", "a" * 40)])},
              paths={("o/r", "CLAUDE.md"): "b" * 40}, texts={"a" * 40: "{}\n", "b" * 40: "# hi\n"})
    out = harvest(ctx, hub, [unit("gone/repo"), unit("o/r")])
    assert out.deferred == {}
    assert out.rows["repos"][0] == {"repo": "gone/repo", "missing": True, "error": "not_found", "canary": False}
    assert out.rows["repos"][1]["head_oid"] == "f" * 40 and out.rows["repos"][1]["tree_truncated"] == 0
    assert sorted(f["path"] for f in out.rows["harness_files"]) == [".claude/settings.json", "CLAUDE.md"]
    assert hub.tree_calls == []  # no subdirectories, so no REST call


def test_null_repo_without_not_found_is_transient():
    body = {"data": {"r0": None, "r1": None},
            "errors": [{"type": "NOT_FOUND", "path": ["r0"]}, {"type": "RESOURCE_LIMITS_EXCEEDED", "path": ["r1"]}]}
    found, failed = s2.parse_meta([("a/gone", []), ("b/slow", [])], body)
    assert found["a/gone"].row["error"] == "not_found"
    assert failed == {"b/slow": "graphql_error:RESOURCE_LIMITS_EXCEEDED"}
    _, failed = s2.parse_meta([("c/x", [])], {"data": {"r0": None}})
    assert failed == {"c/x": "graphql_error:unknown"}


def test_whole_batch_failure_halves_until_it_fits(ctx):
    hub = Hub({"o/a": node(), "o/b": node()})
    real, sizes = hub.gql, []

    def flaky(req):
        q = json.loads(req.content)["query"]
        n = len(ALIAS.findall(q))
        if "HEAD:.claude" in q:
            sizes.append(n)
            if n > 1:
                return httpx.Response(502, text="Bad Gateway")
        return real(req)

    hub.gql = flaky
    out = harvest(ctx, hub, [unit("o/a"), unit("o/b")])
    assert sizes == [2, 1, 1] and out.deferred == {}
    assert [r["repo"] for r in out.rows["repos"]] == ["o/a", "o/b"]


def test_client_timeout_halves_the_batch(ctx):
    hub = Hub({"o/a": node(), "o/b": node()})
    real = hub.gql

    def slow(req):
        q = json.loads(req.content)["query"]
        if "HEAD:.claude" in q and len(ALIAS.findall(q)) > 1:
            raise httpx.ReadTimeout("slow", request=req)
        return real(req)

    hub.gql = slow
    out = harvest(ctx, hub, [unit("o/a"), unit("o/b")])
    assert [r["repo"] for r in out.rows["repos"]] == ["o/a", "o/b"] and out.deferred == {}


def test_repo_github_cannot_serve_is_deferred_not_recorded_missing(ctx):
    hub = Hub({"o/a": node(), "o/b": node()})
    hub.fail_meta["o/a"] = 502
    out = harvest(ctx, hub, [unit("o/a"), unit("o/b")])
    assert out.deferred == {"k:o/a": "graphql_502"}
    assert [r["repo"] for r in out.rows["repos"]] == ["o/b"]


def test_outage_stops_the_stage_instead_of_deferring(ctx):
    hub = Hub({"o/a": node()})
    hub.healthy = False
    with pytest.raises(StopStage, match="GitHub GraphQL is failing"):
        harvest(ctx, hub, [unit("o/a")])


def test_final_pass_records_an_unreachable_repo(ctx):
    hub = Hub({"o/a": node()})
    hub.fail_meta["o/a"] = 504
    out = harvest(ctx, hub, [unit("o/a")], final=True)
    assert out.deferred == {}
    assert out.rows["repos"] == [{"repo": "o/a", "missing": True, "error": "unreachable:graphql_504",
                                  "canary": False}]


def test_rest_lists_the_whole_tree_at_any_depth(ctx):
    hub = tree_hub()
    out = harvest(ctx, hub, [unit("o/a")])
    files = {f["path"]: f for f in out.rows["harness_files"]}
    assert set(files) == {".claude/settings.json", ".claude/skills/x/SKILL.md", DEEP}
    assert (files[DEEP]["kind"], files[DEEP]["skip_reason"]) == ("skill_file", "not_fetched_kind")
    assert files[".claude/skills/x/SKILL.md"]["fetched"]
    assert out.rows["repos"][0]["tree_truncated"] == 0 and hub.tree_calls == ["o/a"]


def test_truncated_rest_tree_is_recorded(ctx):
    hub = tree_hub()
    hub.truncated.add("o/a")
    assert harvest(ctx, hub, [unit("o/a")]).rows["repos"][0]["tree_truncated"] == 1


def test_rest_tree_failure_defers_then_the_final_pass_keeps_the_top_level(ctx):
    hub = tree_hub()
    hub.trees = {}
    out = harvest(ctx, hub, [unit("o/a")])
    assert out.deferred == {"k:o/a": "rest_tree_502"} and out.rows["repos"] == []
    last = harvest(ctx, hub, [unit("o/a")], final=True)
    repo = last.rows["repos"][0]
    assert (repo["missing"], repo["error"], repo["tree_truncated"]) == (False, "partial:rest_tree_502", 1)
    assert [f["path"] for f in last.rows["harness_files"]] == [".claude/settings.json"]


def test_every_tree_call_failing_stops_the_stage(ctx):
    hub = Hub({f"o/r{i}": node([subdir("skills")]) for i in range(3)})
    with pytest.raises(StopStage, match="REST tree"):
        harvest(ctx, hub, [unit(f"o/r{i}") for i in range(3)])


def test_nested_paths_beyond_the_cap_go_in_follow_up_queries(ctx):
    extra = [f"pkg{i:03d}/CLAUDE.md" for i in range(s2.MAX_EXTRA + s2.EXTRA_BATCH + 1)]
    hub = Hub({"o/mono": node()}, paths={("o/mono", p): f"{i:040x}" for i, p in enumerate(extra)},
              texts={f"{i:040x}": f"# pkg {i}\n" for i in range(len(extra))})
    out = harvest(ctx, hub, [unit("o/mono", extra)])
    assert len(hub.path_queries) == 2
    assert sorted(f["path"] for f in out.rows["harness_files"]) == extra
    assert all(f["fetched"] for f in out.rows["harness_files"])


def test_plan_blob_batches_respects_count_and_bytes():
    wanted = [{"repo": "o/r", "oid": f"{i:040x}", "size": 150_000} for i in range(5)]
    assert [len(b) for b in s2.plan_blob_batches(wanted)] == [2, 2, 1]


def test_each_new_blob_is_fetched_once_and_stored_redacted(ctx):
    shared, stored, secret_oid = "a" * 40, "b" * 40, "c" * 40
    ctx.blobs.put(stored, "already here\n")
    hub = Hub({"o/a": node([blob("settings.json", shared), blob("settings.local.json", secret_oid)]),
               "o/b": node([blob("settings.json", shared)])},
              paths={("o/b", "CLAUDE.md"): stored},
              texts={shared: '{"model": "sonnet"}\n', secret_oid: '{"env": {"CLOUD_API_KEY": "%s"}}\n' % SECRET})
    out = harvest(ctx, hub, [unit("o/a"), unit("o/b")])
    assert sorted(o for _, o in OID.findall("".join(hub.blob_queries))) == sorted([shared, secret_oid])
    assert len(out.rows["harness_files"]) == 4 and all(f["fetched"] for f in out.rows["harness_files"])
    assert SECRET not in ctx.blobs.get(secret_oid)
    assert out.rows["redactions"] == [{"blob_sha": secret_oid, "rule": "assigned_secret", "n": 1}]


def test_oversized_and_non_harness_files_are_recorded_not_fetched(ctx):
    hub = Hub({"o/a": node([blob("notes.txt", "d" * 40), blob("settings.json", "e" * 40, size=300_000)])})
    out = harvest(ctx, hub, [unit("o/a")])
    reasons = {f["path"]: f["skip_reason"] for f in out.rows["harness_files"]}
    assert reasons == {".claude/notes.txt": "not_fetched_kind", ".claude/settings.json": "too_large"}
    assert not any(f["fetched"] for f in out.rows["harness_files"]) and hub.blob_queries == []


def test_final_blob_statuses_reach_skip_reason(ctx):
    bin_, trunc, gone = "1" * 40, "2" * 40, "3" * 40
    hub = Hub({"o/a": node([blob("settings.json", bin_), blob("settings.local.json", trunc)])},
              paths={("o/a", "CLAUDE.md"): gone},
              texts={bin_: {"oid": bin_, "isBinary": True, "isTruncated": False, "text": None},
                     trunc: {"oid": trunc, "isBinary": False, "isTruncated": True, "text": "x"}, gone: None})
    hub.not_found_blobs.add(gone)
    out = harvest(ctx, hub, [unit("o/a")])
    reasons = {f["path"]: f["skip_reason"] for f in out.rows["harness_files"]}
    assert reasons == {".claude/settings.json": "binary", ".claude/settings.local.json": "truncated",
                       "CLAUDE.md": "missing"}
    assert out.deferred == {} and not any(f["fetched"] for f in out.rows["harness_files"])


def test_transient_blob_failure_defers_the_repo_and_the_final_pass_records_it(ctx):
    lost = "4" * 40
    hub = Hub({"o/a": node([blob("settings.json", lost)]), "o/b": node([blob("settings.json", "a" * 40)])},
              texts={"a" * 40: "{}\n", lost: None})
    out = harvest(ctx, hub, [unit("o/a"), unit("o/b")])
    assert out.deferred == {"k:o/a": "blob_fetch_error"}
    assert [r["repo"] for r in out.rows["repos"]] == ["o/b"]
    assert {f["repo"] for f in out.rows["harness_files"]} == {"o/b"}
    last = harvest(ctx, hub, [unit("o/a")], final=True)
    assert (last.rows["repos"][0]["missing"], last.rows["repos"][0]["error"]) == (False, "partial:blob_fetch_error")
    assert [(f["skip_reason"], f["fetched"]) for f in last.rows["harness_files"]] == [("fetch_error", False)]


def test_blob_query_failure_halves_and_still_stores_both(ctx):
    a, b = "a" * 40, "b" * 40
    hub = Hub({}, texts={a: "one\n", b: "two\n"})
    real, seen = hub.gql, []

    def flaky(req):
        oids = OID.findall(json.loads(req.content)["query"])
        if oids:
            seen.append(len(oids))
        if len(oids) > 1:
            return httpx.Response(502, text="Bad Gateway")
        return real(req)

    hub.gql = flaky
    blobs = [{"repo": "o/r", "oid": a, "size": 4}, {"repo": "o/r", "oid": b, "size": 4}]
    status = s2.fetch_blobs(hub.clients()[0], blobs, ctx.blobs)
    assert seen == [2, 1, 1] and status == {a: "", b: ""}
    assert ctx.blobs.get(a) == "one\n" and ctx.blobs.get(b) == "two\n"


def test_redaction_counts_survive_a_kill_after_blob_write(ctx):
    oid = "c" * 40
    ctx.blobs.put(oid, '{"env": {"CLOUD_API_KEY": "%s"}}\n' % SECRET)  # killed before the commit
    hub = Hub({"o/a": node([blob("settings.json", oid)])})
    out = harvest(ctx, hub, [unit("o/a")])
    assert hub.blob_queries == []
    assert out.rows["redactions"] == [{"blob_sha": oid, "rule": "assigned_secret", "n": 1}]


def test_fixture_mode_stores_redacted_blobs(fctx):
    s2.run(fctx, Opts())
    assert len(fctx.tables.read("repos")) == 8
    files = fctx.tables.read("harness_files")
    assert all(fctx.blobs.has(f["blob_sha"]) for f in files if f["fetched"])
    assert not any(SECRET in fctx.blobs.get(f["blob_sha"]) for f in files if f["fetched"])
    assert s2.run(fctx, Opts()).units_skipped == 1


def seed_hits(ctx, hits):
    ctx.tables.write_part("repo_hits", "p-seed", [
        {"repo": r, "path": p, "component": "claude_md", "query_id": "q", "blob_sha": "s", "is_fork": False}
        for r, p in hits])
    write_state(ctx, "s1", {"families_done": ["claude_md/nonfork", "claude_md/fork"], "complete": True})


def wire(monkeypatch, hub):
    gql, rest = hub.clients()
    monkeypatch.setattr(s2, "GraphQLClient", lambda token: gql)
    monkeypatch.setattr(s2, "RestClient", lambda token: rest)
    monkeypatch.setattr(s2, "github_token", lambda: "t")


def test_run_defers_then_harvests_on_a_later_run(ctx, monkeypatch):
    hub = Hub({"o/a": node(), "o/b": node()},
              paths={("o/a", "CLAUDE.md"): "a" * 40, ("o/b", "CLAUDE.md"): "b" * 40,
                     ("o/b", "docs/CLAUDE.md"): "c" * 40},
              texts={"a" * 40: "# a\n", "b" * 40: "# b\n", "c" * 40: "# c\n"})
    hub.fail_meta["o/a"] = 502
    seed_hits(ctx, [("o/a", "CLAUDE.md"), ("o/b", "CLAUDE.md"), ("o/b", "docs/CLAUDE.md")])
    wire(monkeypatch, hub)
    stats = s2.run(ctx, Opts())
    assert (stats.units_run, stats.units_deferred) == (1, 1) and "deferred" in stats.stopped
    assert [r["repo"] for r in ctx.tables.read("repos")] == ["o/b"]
    assert sorted(f["path"] for f in ctx.tables.read("harness_files")) == ["CLAUDE.md", "docs/CLAUDE.md"]
    hub.fail_meta.clear()
    stats = s2.run(ctx, Opts())
    assert (stats.units_run, stats.stopped) == (1, None)
    assert sorted(r["repo"] for r in ctx.tables.read("repos")) == ["o/a", "o/b"]
    assert len(ctx.tables.read("harness_files")) == 3


def test_run_gives_up_after_max_attempts_and_records_why(ctx, monkeypatch):
    hub = Hub({"o/a": node()})
    hub.fail_meta["o/a"] = 502
    seed_hits(ctx, [("o/a", "CLAUDE.md")])
    wire(monkeypatch, hub)
    for _ in range(MAX_ATTEMPTS - 1):
        assert s2.run(ctx, Opts()).units_deferred == 1
    stats = s2.run(ctx, Opts())
    assert (stats.units_gave_up, stats.stopped) == (1, None)
    row = ctx.tables.read("repos")[0]
    assert (row["repo"], row["missing"], row["error"]) == ("o/a", True, "unreachable:graphql_502")


def mono_hub():
    extra = [f"pkg{i:03d}/CLAUDE.md" for i in range(s2.MAX_EXTRA + 1)]
    return Hub({"o/mono": node()}), extra


def test_follow_up_query_with_a_non_200_status_defers_the_repo(ctx):
    hub, extra = mono_hub()
    real = hub.gql

    def forbidden(req):
        q = json.loads(req.content)["query"]
        if "HEAD:.claude" not in q and "HEAD:pkg" in q:
            return httpx.Response(403, json={"message": "Forbidden"})
        return real(req)

    hub.gql = forbidden
    out = harvest(ctx, hub, [unit("o/mono", extra)])
    assert out.deferred == {"k:o/mono": "graphql_403"}
    assert out.rows["repos"] == [] and out.rows["harness_files"] == []


def test_follow_up_query_with_a_null_repo_defers_the_repo(ctx):
    hub, extra = mono_hub()
    real = hub.gql

    def nulled(req):
        q = json.loads(req.content)["query"]
        if "HEAD:.claude" not in q and "HEAD:pkg" in q:
            return httpx.Response(200, json={"data": {"r0": None},
                                             "errors": [{"type": "RESOURCE_LIMITS_EXCEEDED", "path": ["r0"]}]})
        return real(req)

    hub.gql = nulled
    out = harvest(ctx, hub, [unit("o/mono", extra)])
    assert out.deferred == {"k:o/mono": "graphql_error:RESOURCE_LIMITS_EXCEEDED"}
    assert out.rows["repos"] == [] and out.rows["harness_files"] == []


def test_error_under_a_present_repo_makes_it_failed_not_recorded():
    body = {"data": {"r0": node()},
            "errors": [{"type": "SERVICE_UNAVAILABLE", "path": ["r0", "claude"]}]}
    found, failed = s2.parse_meta([("o/a", [])], body)
    assert found == {} and failed == {"o/a": "graphql_error:SERVICE_UNAVAILABLE"}
    ok = {"data": {"r0": node()}, "errors": [{"type": "NOT_FOUND", "path": ["r0", "f0"]}]}
    assert "o/a" in s2.parse_meta([("o/a", [])], ok)[0]


def test_single_blob_502_defers_the_repo(ctx):
    oid = "5" * 40
    hub = Hub({"o/a": node([blob("settings.json", oid)])}, texts={oid: "{}\n"})
    hub.fail_blobs.add(oid)
    out = harvest(ctx, hub, [unit("o/a")])
    assert out.deferred == {"k:o/a": "blob_fetch_error"}
    assert out.rows["repos"] == [] and out.rows["harness_files"] == []


def two_repo_hub():
    return Hub({"o/a": node(), "o/b": node()},
               paths={("o/a", "CLAUDE.md"): "a" * 40, ("o/b", "CLAUDE.md"): "b" * 40},
               texts={"a" * 40: "# a\n", "b" * 40: "# b\n"})


def test_discovered_groups_nested_claude_md_paths_per_repo(ctx):
    ctx.tables.write_part("repo_hits", "p-seed", [
        {"repo": "o/a", "path": "CLAUDE.md", "component": "claude_md", "query_id": "q", "blob_sha": "s", "is_fork": False},
        {"repo": "o/a", "path": "pkg/b/CLAUDE.md", "component": "claude_md", "query_id": "q", "blob_sha": "s", "is_fork": False},
        {"repo": "o/a", "path": "pkg/a/CLAUDE.md", "component": "claude_md", "query_id": "q2", "blob_sha": "s", "is_fork": False},
        {"repo": "o/a", "path": "pkg/a/CLAUDE.md", "component": "claude_md", "query_id": "q3", "blob_sha": "s", "is_fork": False},
        {"repo": "o/a", "path": ".claude/CLAUDE.md", "component": "claude_md", "query_id": "q", "blob_sha": "s", "is_fork": False},
        {"repo": "o/b", "path": ".claude/skills/x/SKILL.md", "component": "skill", "query_id": "q", "blob_sha": "s", "is_fork": False},
    ])
    assert sorted(s2.discovered(ctx)) == [("o/a", ["pkg/a/CLAUDE.md", "pkg/b/CLAUDE.md"]), ("o/b", [])]


def test_full_mode_waits_for_the_claude_md_families(ctx, monkeypatch):
    hub = two_repo_hub()
    seed_hits(ctx, [("o/a", "CLAUDE.md"), ("o/b", "CLAUDE.md")])
    write_state(ctx, "s1", {"families_done": ["claude_md/nonfork"], "complete": False})
    wire(monkeypatch, hub)
    stats = s2.run(ctx, Opts())
    assert stats.stopped.startswith("waiting for S1") and hub.meta_sizes == []
    assert s2.run(ctx, Opts(limit=1)).units_run == 1  # slice mode is not gated


def test_full_mode_asks_for_a_rerun_while_s1_is_still_discovering(ctx, monkeypatch):
    hub = two_repo_hub()
    seed_hits(ctx, [("o/a", "CLAUDE.md"), ("o/b", "CLAUDE.md")])
    write_state(ctx, "s1", {"families_done": ["claude_md/nonfork", "claude_md/fork"], "complete": False})
    wire(monkeypatch, hub)
    stats = s2.run(ctx, Opts())
    assert stats.units_run == 2 and stats.stopped.startswith("S1 is still discovering")
    write_state(ctx, "s1", {"families_done": ["claude_md/nonfork", "claude_md/fork"], "complete": True})
    assert s2.run(ctx, Opts()).stopped is None


def test_low_disk_stops_the_stage_before_any_request(ctx, monkeypatch):
    hub = two_repo_hub()
    seed_hits(ctx, [("o/a", "CLAUDE.md")])
    wire(monkeypatch, hub)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(s2.shutil, "disk_usage", lambda p: usage(100 * 2**30, 99 * 2**30, 2**30))
    stats = s2.run(ctx, Opts())
    assert "GiB free" in stats.stopped and "CENSUS_BLOBS" in stats.stopped
    assert hub.meta_sizes == [] and ctx.journal("s2").entries() == []
    monkeypatch.setattr(s2.shutil, "disk_usage", lambda p: usage(100 * 2**30, 50 * 2**30, 50 * 2**30))
    assert s2.run(ctx, Opts()).units_run == 1


def test_census_blobs_moves_the_blob_store(tmp_path, monkeypatch):
    from pipeline.context import make_ctx

    monkeypatch.setenv("CENSUS_BLOBS", str(tmp_path / "elsewhere"))
    ctx = make_ctx("test")
    ctx.blobs.put("ab" * 20, "hello\n")
    assert (tmp_path / "elsewhere" / "ab" / ("ab" * 20 + ".zst")).exists()
