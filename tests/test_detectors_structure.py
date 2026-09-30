import math

import pytest
from helpers import FIXTURES, fixture_text, run_until

from pipeline import s5_features
from pipeline.context import Opts
from pipeline.detectors import detect
from pipeline.detectors.base import Artifact, Harness
from pipeline.detectors.glyph import glyph
from pipeline.fixtures import fixture_files
from pipeline.kinds import PARSED_KINDS, artifact_id, classify
from pipeline.parsers import parse
from pipeline.redact import redact


def harness(repo: str) -> Harness:
    files = [f for f in fixture_files(FIXTURES) if f.repo == repo]
    paths = [f.path for f in files]
    arts = []
    for f in files:
        kind = classify(f.path)
        if kind in PARSED_KINDS:
            parsed, err = parse(kind, redact(f.data.decode())[0], f.path, paths)
            arts.append(Artifact(artifact_id(repo, f.path), kind, f.path, parsed, err))
    return Harness(repo, tuple(sorted(arts, key=lambda a: a.path)))


@pytest.mark.parametrize("repo,expected", [
    ("acme/webapp", {"hook_stop_gate", "hook_posttooluse_formatter", "permissions_deny", "agent_restricted_tools",
                     "agent_model_routing", "claude_md_imports", "claude_md_nested", "command_arguments",
                     "mcp_composition"}),
    ("beta/data-pipeline", {"skill_scripts", "skill_references", "permissions_bypass"}),
    ("epsilon/infra", {"hook_pretooluse_guard", "hook_sessionstart_context", "permissions_sandbox"}),
    ("zeta/release-plugin", {"plugin_manifest", "command_arguments"}),
    ("theta/shop", {"claude_md_imports"}),
    ("gamma/novel", set()),
    ("delta/skills-fork", set()),
    ("eta/broken", set()),
])
def test_detect_on_fixture_harnesses(repo, expected):
    assert {e.technique_id for e in detect(harness(repo))} == expected


def test_evidence_locators():
    by_id = {e.technique_id: e for e in detect(harness("acme/webapp"))}
    assert (by_id["hook_stop_gate"].path, by_id["hook_stop_gate"].start_line) == (".claude/settings.json", 7)
    assert (by_id["claude_md_imports"].path, by_id["claude_md_imports"].start_line) == ("CLAUDE.md", 5)
    assert (by_id["agent_restricted_tools"].start_line, by_id["agent_restricted_tools"].end_line) == (1, 6)
    assert by_id["command_arguments"].start_line == 8


def test_detect_deduplicates_per_artifact():
    a = Artifact("s1", "settings", ".claude/settings.json",
                 {"hooks": [{"event": "PreToolUse", "command": "a", "line": 3},
                            {"event": "PreToolUse", "command": "b", "line": 4}]}, None)
    assert [e.start_line for e in detect(Harness("o/r", (a,)))] == [3]


def test_glyph_acme():
    g = glyph(harness("acme/webapp"))
    size = len(fixture_text("acme/webapp", "CLAUDE.md").encode())
    assert g == {"repo": "acme/webapp", "claude_md_log_bytes": math.log10(1 + size), "n_skills": 0, "n_agents": 1,
                 "n_commands": 1, "n_hooks": 2, "permission_breadth": 5.0, "n_mcp_servers": 2}


def test_glyph_bypass_and_empty_harness():
    assert glyph(harness("beta/data-pipeline"))["permission_breadth"] == 2 + 2 + 20
    assert glyph(Harness("o/r", ()))["claude_md_log_bytes"] == 0.0


def test_s5_on_fixtures(fctx):
    run_until(fctx, "s5")
    assert len(fctx.tables.read("features")) == 18
    assert len(fctx.tables.read("glyphs")) == 8
    assert s5_features.run(fctx, Opts()).units_run == 0
