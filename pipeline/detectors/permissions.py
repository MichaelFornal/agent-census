"""Permission and integration detectors (PRD §5, Permissions and Other). Permission evidence is shown
only anonymized (PRIVATE_CATEGORIES)."""
from pipeline.detectors.base import Evidence, Harness, ev


def detect_permissions(h: Harness) -> list[Evidence]:
    out = []
    for a in h.of("settings", "settings_local"):
        p = a.parsed.get("permissions") or {}
        line = a.parsed.get("permissions_line")
        if p.get("deny"):
            out.append(ev("permissions_deny", a, line))
        if p.get("default_mode") == "bypassPermissions":
            out.append(ev("permissions_bypass", a, line))
        if a.parsed.get("sandbox"):
            out.append(ev("permissions_sandbox", a, 1))
    return out


def detect_integrations(h: Harness) -> list[Evidence]:
    out = []
    servers = [(a, s) for a in h.of("mcp") for s in a.parsed.get("servers") or []]
    if len(servers) >= 2:
        a, s = servers[0]
        out.append(ev("mcp_composition", a, s.get("line")))
    for a in h.of("plugin"):
        out.append(ev("plugin_manifest", a, 1))
    return out
