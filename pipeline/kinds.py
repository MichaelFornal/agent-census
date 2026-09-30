"""Which harness component a repo path is (PRD §1 subject: the Claude Code harness)."""
from pipeline.journal import unit_key

PARSED_KINDS = frozenset({"claude_md", "settings", "settings_local", "skill", "agent", "command", "hook",
                          "mcp", "plugin", "marketplace"})
ROOT_FILES = {".mcp.json": "mcp", ".claude-plugin/plugin.json": "plugin",
              ".claude-plugin/marketplace.json": "marketplace",
              ".claude/settings.json": "settings", ".claude/settings.local.json": "settings_local"}


def classify(path: str) -> str | None:
    if path.rsplit("/", 1)[-1] == "CLAUDE.md":
        return "claude_md"
    if path in ROOT_FILES:
        return ROOT_FILES[path]
    if path.startswith(".claude/skills/"):
        return "skill" if path.endswith("/SKILL.md") else "skill_file"
    if path.startswith(".claude/agents/") and path.endswith(".md"):
        return "agent"
    if path.startswith(".claude/commands/") and path.endswith(".md"):
        return "command"
    if path.startswith(".claude/hooks/"):
        return "hook"
    if path.startswith(".claude/"):
        return "other_claude"
    return None


def artifact_id(repo: str, path: str) -> str:
    return "a-" + unit_key("artifact", repo, path)[:16]
