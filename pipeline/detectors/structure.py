"""Structure detectors: subagents, CLAUDE.md memory, skills, commands (PRD §5)."""
from pipeline.detectors.base import Evidence, Harness, ev


def detect_structure(h: Harness) -> list[Evidence]:
    out = []
    for a in h.of("agent"):
        fm = a.parsed.get("frontmatter") or {}
        end = a.parsed.get("frontmatter_end_line") or 1
        if fm.get("tools"):
            out.append(ev("agent_restricted_tools", a, 1, end))
        model = fm.get("model")
        if isinstance(model, str) and model and model != "inherit":
            out.append(ev("agent_model_routing", a, 1, end))
    for a in h.of("claude_md"):
        imports = a.parsed.get("imports") or []
        if imports:
            out.append(ev("claude_md_imports", a, imports[0]["line"]))
        if a.parsed.get("nested"):
            out.append(ev("claude_md_nested", a, 1))
    for a in h.of("skill"):
        resources = a.parsed.get("resources") or []
        end = a.parsed.get("frontmatter_end_line") or 1
        if "scripts" in resources:
            out.append(ev("skill_scripts", a, 1, end))
        if "references" in resources:
            out.append(ev("skill_references", a, 1, end))
    for a in h.of("command"):
        if a.parsed.get("has_arguments"):
            out.append(ev("command_arguments", a, a.parsed.get("arguments_line")))
    return out
