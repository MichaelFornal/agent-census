import re

from pipeline.detectors.base import Artifact, Harness
from pipeline.detectors.catalog import TECHNIQUES
from pipeline.detectors.hooks import detect_hooks
from pipeline.detectors.permissions import detect_integrations, detect_permissions


def hook(event, command, line=5, matcher=None):
    return {"event": event, "matcher": matcher, "type": "command", "command": command, "line": line}


def settings(hooks=(), perms=None, sandbox=None, kind="settings"):
    return Artifact("s1", kind, ".claude/settings.json",
                    {"hooks": list(hooks), "permissions": perms or {}, "sandbox": sandbox, "permissions_line": 2}, None)


def ids(evidence):
    return sorted(e.technique_id for e in evidence)


def test_stop_gate_needs_a_verification_command():
    assert ids(detect_hooks(Harness("o/r", (settings([hook("Stop", "npm test --silent")]),)))) == ["hook_stop_gate"]
    assert ids(detect_hooks(Harness("o/r", (settings([hook("Stop", "echo done")]),)))) == []
    assert ids(detect_hooks(Harness("o/r", (settings([hook("SubagentStop", "uv run pytest -q")]),)))) == ["hook_stop_gate"]


def test_posttooluse_formatter():
    h = Harness("o/r", (settings([hook("PostToolUse", 'npx prettier --write "$F"'), hook("PostToolUse", "echo hi")]),))
    assert ids(detect_hooks(h)) == ["hook_posttooluse_formatter"]


def test_event_hooks_map_to_techniques():
    h = Harness("o/r", (settings([hook("PreToolUse", "guard.sh"), hook("SessionStart", "cat notes.md"),
                                  hook("UserPromptSubmit", "date"), hook("Notification", "say done")]),))
    assert ids(detect_hooks(h)) == ["hook_notification", "hook_pretooluse_guard", "hook_sessionstart_context",
                                    "hook_userpromptsubmit"]


def test_evidence_points_at_the_hook_line():
    [e] = detect_hooks(Harness("o/r", (settings([hook("Stop", "pytest", line=7)]),)))
    assert (e.artifact_id, e.path, e.start_line, e.end_line) == ("s1", ".claude/settings.json", 7, 7)


def test_permission_techniques():
    h = Harness("o/r", (settings(perms={"allow": ["Bash"], "deny": ["Read(.env)"], "default_mode": "bypassPermissions"},
                                 sandbox=True, kind="settings_local"),))
    assert ids(detect_permissions(h)) == ["permissions_bypass", "permissions_deny", "permissions_sandbox"]
    assert ids(detect_permissions(Harness("o/r", (settings(perms={"allow": ["Read"]}),)))) == []


def test_mcp_composition_needs_two_servers_and_plugin_manifest():
    one = Artifact("m1", "mcp", ".mcp.json", {"servers": [{"name": "a", "line": 2}]}, None)
    two = Artifact("m2", "mcp", ".mcp.json", {"servers": [{"name": "a", "line": 2}, {"name": "b", "line": 3}]}, None)
    plugin = Artifact("p1", "plugin", ".claude-plugin/plugin.json", {"name": "x"}, None)
    assert ids(detect_integrations(Harness("o/r", (one,)))) == []
    assert ids(detect_integrations(Harness("o/r", (two, plugin)))) == ["mcp_composition", "plugin_manifest"]


def test_detectors_survive_partial_parses():
    broken = Artifact("s1", "settings", ".claude/settings.json", {"bytes": 10}, "json_invalid")
    h = Harness("o/r", (broken,))
    assert detect_hooks(h) == [] and detect_permissions(h) == [] and detect_integrations(h) == []


def test_catalog_copy_has_no_digits_and_valid_categories():
    for t in TECHNIQUES.values():
        assert not re.search(r"\d", t.label + t.definition), t.id
        assert t.category in {"hooks", "orchestration", "memory", "skills", "commands", "permissions", "integrations"}


import pytest  # noqa: E402

from pipeline.detectors.base import Artifact as _A  # noqa: E402
from pipeline.detectors.hooks import is_format, is_verify  # noqa: E402
from pipeline.fixtures import fixture_files  # noqa: E402
from pipeline.kinds import classify  # noqa: E402
from pipeline.parsers import parse  # noqa: E402
from tests.helpers import FIXTURES  # noqa: E402


@pytest.mark.parametrize("cmd", ["npm test --silent", "uv run pytest -q", "make lint", "cargo clippy",
                                 "go vet ./...", "npx tsc --noEmit", "pnpm run typecheck",
                                 "./node_modules/.bin/eslint .", "CI=1 npm test", "cd app && npm test"])
def test_is_verify_true(cmd):
    assert is_verify(cmd)


@pytest.mark.parametrize("cmd", ["mkdir -p /var/test", "cd my-test-app", "echo $TEST", "/usr/bin/test -f x",
                                 "notify-send 'tests passed'", "cat lint-report.txt", "black-box.sh",
                                 "cat prettier.config.js", "echo 'no tests'", "rm test.log", "cat tests/x",
                                 "echo done > /tmp/tsc-out"])
def test_is_verify_false(cmd):
    assert not is_verify(cmd)


@pytest.mark.parametrize("cmd", ['npx prettier --write "$F"', "black .", "ruff format", "npx eslint --fix ."])
def test_is_format_true(cmd):
    assert is_format(cmd)


@pytest.mark.parametrize("cmd", ["echo black", "black-box.sh", "cat prettier.config.js", "echo ruff format",
                                 "eslint . && echo --fix"])
def test_is_format_false(cmd):
    assert not is_format(cmd)


MALFORMED = [("settings", '{"hooks": {"Stop": "x"}}'),
             ("settings", '{"hooks": {"Stop": [null, 1, {"hooks": "x"}]}}'),
             ("settings", '{"permissions": [1]}'),
             ("settings", '{"hooks": ["x"]}'),
             ("mcp", '{"mcpServers": {"a": 1, "b": {"command": ["x"]}}}')]


def _parser_inputs():
    for f in fixture_files(FIXTURES):
        kind = classify(f.path)
        if kind in ("settings", "settings_local", "mcp"):
            yield kind, f.data.decode(), f.path
    for kind, text in MALFORMED:
        yield kind, text, ".mcp.json" if kind == "mcp" else ".claude/settings.json"


def test_parser_output_satisfies_detector_shape_contract():
    seen = 0
    for kind, text, path in _parser_inputs():
        parsed, err = parse(kind, text, path, [])
        if err:  # partial parses carry no shape guarantee; detectors must still survive them
            pass
        elif kind == "mcp":
            assert isinstance(parsed["servers"], list) and all(isinstance(s, dict) for s in parsed["servers"])
        else:
            for hk in parsed["hooks"]:
                assert isinstance(hk, dict) and isinstance(hk["event"], str)
                assert hk["command"] is None or isinstance(hk["command"], str)
                assert hk["line"] is None or isinstance(hk["line"], int)
            p = parsed["permissions"]
            assert isinstance(p, dict)
            for k in ("allow", "deny", "ask"):
                assert isinstance(p[k], list) and all(isinstance(x, str) for x in p[k])
        h = Harness("o/r", (_A("x", kind, path, parsed, err),))
        detect_hooks(h), detect_permissions(h), detect_integrations(h)
        seen += 1
    assert seen > len(MALFORMED)
