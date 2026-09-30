import pytest

from pipeline.kinds import artifact_id, classify


@pytest.mark.parametrize("path,kind", [
    ("CLAUDE.md", "claude_md"),
    ("packages/api/CLAUDE.md", "claude_md"),
    (".claude/CLAUDE.md", "claude_md"),
    (".claude/settings.json", "settings"),
    (".claude/settings.local.json", "settings_local"),
    (".claude/skills/x/SKILL.md", "skill"),
    (".claude/skills/x/scripts/a.py", "skill_file"),
    (".claude/agents/a.md", "agent"),
    (".claude/agents/notes.txt", "other_claude"),
    (".claude/commands/go.md", "command"),
    (".claude/hooks/guard.sh", "hook"),
    (".mcp.json", "mcp"),
    ("sub/.mcp.json", None),
    (".claude-plugin/plugin.json", "plugin"),
    (".claude-plugin/marketplace.json", "marketplace"),
    ("README.md", None),
])
def test_classify(path, kind):
    assert classify(path) == kind


def test_artifact_id_is_stable_and_distinct():
    assert artifact_id("o/r", "CLAUDE.md") == artifact_id("o/r", "CLAUDE.md")
    assert artifact_id("o/r", "CLAUDE.md") != artifact_id("o/s", "CLAUDE.md")
    assert artifact_id("o/r", "CLAUDE.md").startswith("a-")
