"""parse(kind, text, path, siblings) -> (parsed, error_class). Malformed files keep their partial parse."""
from pipeline.parsers import jsonkinds, markdown
from pipeline.parsers.errors import ParseError

PARSERS = {
    "claude_md": markdown.parse_claude_md, "skill": markdown.parse_skill, "agent": markdown.parse_agent,
    "command": markdown.parse_command, "hook": markdown.parse_hook_script,
    "settings": jsonkinds.parse_settings, "settings_local": jsonkinds.parse_settings,
    "mcp": jsonkinds.parse_mcp, "plugin": jsonkinds.parse_manifest, "marketplace": jsonkinds.parse_manifest,
}


def parse(kind: str, text: str, path: str, siblings: list[str]) -> tuple[dict, str | None]:
    if not text.strip():
        return {"bytes": len(text.encode())}, "empty"
    try:
        return PARSERS[kind](text, path, siblings), None
    except ParseError as e:
        return e.partial, e.error_class
