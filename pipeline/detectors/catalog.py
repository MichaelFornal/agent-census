"""The seed technique catalog (PRD §5). Labels and definitions are page copy: no digits."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Technique:
    id: str
    label: str
    category: str
    definition: str


PRIVATE_CATEGORIES = frozenset({"permissions"})  # aggregate only, never a named call-out (PRD §3)

TECHNIQUES: dict[str, Technique] = {t.id: t for t in [
    Technique("hook_stop_gate", "Stop-hook verification gate", "hooks",
              "A Stop or SubagentStop hook runs tests, lint or a type check before Claude may finish."),
    Technique("hook_pretooluse_guard", "PreToolUse hook", "hooks",
              "A hook runs before a tool call, usually to block destructive commands or protect paths."),
    Technique("hook_posttooluse_formatter", "PostToolUse formatter", "hooks",
              "A hook formats files after Claude edits them."),
    Technique("hook_sessionstart_context", "SessionStart context injection", "hooks",
              "A hook adds context when a session starts."),
    Technique("hook_userpromptsubmit", "UserPromptSubmit augmentation", "hooks",
              "A hook runs on every prompt, usually to add context or check it."),
    Technique("hook_notification", "Notification hook", "hooks",
              "A hook runs when Claude sends a notification, usually to alert the user."),
    Technique("agent_restricted_tools", "Subagent with a restricted tool list", "orchestration",
              "A subagent declares which tools it may use."),
    Technique("agent_model_routing", "Per-agent model routing", "orchestration",
              "A subagent names the model it runs on."),
    Technique("claude_md_imports", "CLAUDE.md import chain", "memory",
              "CLAUDE.md pulls in other files with @path imports."),
    Technique("claude_md_nested", "Per-directory CLAUDE.md", "memory",
              "A CLAUDE.md file below the repo root scopes instructions to its directory."),
    Technique("skill_scripts", "Skill that wraps scripts", "skills",
              "A skill ships a scripts directory that Claude runs."),
    Technique("skill_references", "Progressive disclosure", "skills",
              "A skill keeps detail in a references directory that Claude reads only when needed."),
    Technique("command_arguments", "Slash command with arguments", "commands",
              "A slash command takes arguments through $ARGUMENTS."),
    Technique("permissions_deny", "Deny rules", "permissions",
              "Settings deny specific tools or commands outright."),
    Technique("permissions_bypass", "Bypass-permissions mode", "permissions",
              "Settings default to skipping permission prompts."),
    Technique("permissions_sandbox", "Sandbox enabled", "permissions",
              "Settings turn on the command sandbox."),
    Technique("mcp_composition", "MCP server composition", "integrations",
              "The repo configures more than one MCP server."),
    Technique("plugin_manifest", "Plugin manifest", "integrations",
              "The repo ships a Claude Code plugin manifest."),
]}
