from m0.harvest import build_query, is_retryable, parse_response, store_file, summarize
from m0.paths import blob_path

GHP = "ghp_" + "A1b2C3d4E5" * 4


def test_build_query_aliases_and_escapes():
    q = build_query([("o/r", ["CLAUDE.md", 'docs/we"ird.md']), ("x/y-z", [".mcp.json"])])
    assert q.startswith("query { rateLimit { cost remaining resetAt }")
    assert 'r0: repository(owner: "o", name: "r")' in q
    assert 'f1: object(expression: "HEAD:docs/we\\"ird.md")' in q
    assert 'r1: repository(owner: "x", name: "y-z")' in q
    assert 'tree: object(expression: "HEAD:.claude")' in q


BODY = {
    "data": {
        "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": "2026-09-29T12:00:00Z"},
        "r0": {
            "nameWithOwner": "o/r", "stargazerCount": 3, "isFork": False, "isTemplate": False,
            "createdAt": "2025-01-01T00:00:00Z", "pushedAt": "2026-09-01T00:00:00Z",
            "primaryLanguage": {"name": "Python"}, "licenseInfo": None,
            "defaultBranchRef": {"target": {"oid": "deadbeef"}},
            "tree": {"entries": [
                {"path": ".claude/settings.json", "type": "blob", "object": {}},
                {"path": ".claude/skills", "type": "tree",
                 "object": {"entries": [{"path": ".claude/skills/a", "type": "tree"}]}},
            ]},
            "f0": {"oid": "b1", "byteSize": 5, "isBinary": False, "isTruncated": False, "text": "hello"},
            "f1": None,
        },
        "r1": None,
    },
    "errors": [{"type": "NOT_FOUND", "path": ["r1"], "message": "Could not resolve to a Repository"}],
}
BATCH = [("o/r", ["CLAUDE.md", "gone.md"]), ("x/y", [".mcp.json"])]


def test_parse_response_keeps_batch_when_one_repo_is_gone():
    repos, files, cost, errors = parse_response(BATCH, BODY)
    assert cost == 1
    assert len(errors) == 1
    assert repos[0]["head_oid"] == "deadbeef"
    assert repos[0]["claude_tree_entries"] == 3
    assert repos[0]["language"] == "Python"
    assert repos[0]["license"] is None
    assert repos[1] == {"repo": "x/y", "missing": True}
    assert files[0]["text"] == "hello"
    assert files[0]["missing"] is False
    assert files[1] == {"repo": "o/r", "path": "gone.md", "missing": True}
    assert files[2] == {"repo": "x/y", "path": ".mcp.json", "missing": True}


def test_is_retryable():
    assert is_retryable(502, {})
    assert is_retryable(0, {"errors": [{"type": "TIMEOUT", "message": "client timeout"}]})
    assert is_retryable(200, {"data": None, "errors": [{"type": "RESOURCE_LIMITS_EXCEEDED", "message": "x"}]})
    assert is_retryable(200, {"errors": [{"message": "Something went wrong: timed out"}]})
    assert not is_retryable(200, BODY)


def test_store_file_writes_redacted_text_only():
    f = {"repo": "o/r", "path": ".claude/settings.local.json", "missing": False, "oid": "abc123",
         "size": 80, "binary": False, "truncated": False, "text": f'{{"allow": ["Bash(export T={GHP})"]}}'}
    rec = store_file(f)
    assert "text" not in rec
    assert rec["stored"] is True
    assert rec["redactions"] == {"github_token": 1}
    stored = blob_path("abc123").read_text()
    assert GHP not in stored
    assert "[REDACTED:github_token]" in stored


def test_store_file_skips_binary_and_missing():
    assert store_file({"repo": "o/r", "path": "x", "missing": True})["stored"] is False
    rec = store_file({"repo": "o/r", "path": "img", "missing": False, "oid": "ff00", "binary": True, "text": None})
    assert rec["stored"] is False
    assert not blob_path("ff00").exists()


def test_summarize():
    batches = [
        {"batch_size": 10, "status": 200, "latency_s": 1.0, "cost": 1, "n_errors": 0, "retried": False},
        {"batch_size": 10, "status": 200, "latency_s": 3.0, "cost": 1, "n_errors": 0, "retried": False},
        {"batch_size": 100, "status": 0, "latency_s": 60.0, "cost": None, "n_errors": 1, "retried": True},
    ]
    files = [{"missing": False, "binary": False, "truncated": False, "redactions": {"bearer": 2}},
             {"missing": True}]
    repos = [{"missing": False}, {"missing": True}]
    s = summarize(batches, files, repos)
    assert s["by_batch_size"]["10"]["median_latency_s"] == 2.0
    assert s["by_batch_size"]["10"]["repos_per_hour_latency_bound"] == 18000.0
    assert s["by_batch_size"]["100"]["retry_rate"] == 1.0
    assert s["by_batch_size"]["100"]["median_cost"] is None
    assert s["repos_fetched"] == 20
    assert s["points_per_repo"] == 0.1
    assert s["repos_per_hour_points_bound"] == 50000.0
    assert s["files_missing"] == 1
    assert s["secrets_redacted"] == 2
