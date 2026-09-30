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
