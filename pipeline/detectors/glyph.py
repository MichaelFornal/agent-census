"""The fingerprint glyph vector per harness (PRD §3 surface 6). Spoke definitions are M1 rulings."""
import math

from pipeline.detectors.base import Harness

BYPASS_WEIGHT = 20


def _breadth_weight(rule: str) -> int:
    return 2 if "(" not in rule or "*" in rule else 1  # a bare tool name or a wildcard grants more


def glyph(h: Harness) -> dict:
    root = [a for a in h.of("claude_md") if a.path == "CLAUDE.md"]
    settings = h.of("settings", "settings_local")
    perms = [a.parsed.get("permissions") or {} for a in settings]
    allow = [r for p in perms for r in p.get("allow") or []]
    bypass = any(p.get("default_mode") == "bypassPermissions" for p in perms)
    return {
        "repo": h.repo,
        "claude_md_log_bytes": math.log10(1 + (root[0].parsed.get("bytes") or 0)) if root else 0.0,
        "n_skills": len(h.of("skill")),
        "n_agents": len(h.of("agent")),
        "n_commands": len(h.of("command")),
        "n_hooks": sum(len(a.parsed.get("hooks") or []) for a in settings),
        "permission_breadth": float(sum(_breadth_weight(r) for r in allow) + (BYPASS_WEIGHT if bypass else 0)),
        "n_mcp_servers": sum(len(a.parsed.get("servers") or []) for a in h.of("mcp")),
    }
