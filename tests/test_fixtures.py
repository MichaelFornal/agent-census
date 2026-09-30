from helpers import FIXTURES

from pipeline.fixtures import fixture_files, fixture_repos, git_blob_sha, map_path


def test_map_path():
    assert map_path("dot.claude/skills/x/SKILL.md.fixture") == ".claude/skills/x/SKILL.md"
    assert map_path("dot.mcp.json.fixture") == ".mcp.json"
    assert map_path("dot.claude-plugin/plugin.json.fixture") == ".claude-plugin/plugin.json"
    assert map_path("packages/api/CLAUDE.md.fixture") == "packages/api/CLAUDE.md"


def test_git_blob_sha_matches_git():
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_fixture_set_shape():
    repos = fixture_repos(FIXTURES)
    assert len(repos) == 8
    assert [r["repo"] for r in repos if r["is_fork"]] == ["delta/skills-fork"]
    assert all(r["missing"] is False and r["canary"] is False for r in repos)
    files = fixture_files(FIXTURES)
    assert not any(f.path.endswith(".fixture") for f in files)
    skill = {f.repo: f.data for f in files if f.path == ".claude/skills/csv-cleaner/SKILL.md"}
    assert skill["beta/data-pipeline"] == skill["delta/skills-fork"]
