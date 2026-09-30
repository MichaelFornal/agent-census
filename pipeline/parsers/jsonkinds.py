"""Parsers for JSON harness files: settings, .mcp.json, plugin and marketplace manifests (PRD §4 S3).

Env and server env blocks keep key names only; values are never parsed out.
Malformed input raises ParseError and nothing else.
"""
import json
from urllib.parse import urlsplit

from pipeline.parsers.errors import ParseError

LAUNCHERS = {"npx", "uvx", "bunx", "pnpx"}


def _load(text: str) -> dict:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise ParseError("json_invalid", {"bytes": len(text.encode())}) from None
    if not isinstance(obj, dict):
        raise ParseError("not_object", {"bytes": len(text.encode())})
    return obj


def line_of(text: str, needle: str) -> int | None:
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return None


def _strs(v: object) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _str(v: object) -> str | None:
    return v if isinstance(v, str) else None


def _host(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def parse_settings(text: str, path: str, siblings: list[str]) -> dict:
    obj = _load(text)
    perms = obj.get("permissions") if isinstance(obj.get("permissions"), dict) else {}
    hooks = []
    hook_obj = obj.get("hooks") if isinstance(obj.get("hooks"), dict) else {}
    for event, groups in hook_obj.items():
        for g in groups if isinstance(groups, list) else []:
            if not isinstance(g, dict):
                continue
            inner = g.get("hooks")
            for h in inner if isinstance(inner, list) else []:
                if not isinstance(h, dict):
                    continue
                cmd = _str(h.get("command"))
                line = line_of(text, json.dumps(cmd)[1:-1]) if cmd else None
                hooks.append({"event": event, "matcher": _str(g.get("matcher")), "type": _str(h.get("type")),
                              "command": cmd, "line": line or line_of(text, f'"{event}"')})
    sandbox = obj.get("sandbox")
    return {
        "keys": sorted(obj),
        "permissions": {"allow": _strs(perms.get("allow")), "deny": _strs(perms.get("deny")),
                        "ask": _strs(perms.get("ask")), "default_mode": _str(perms.get("defaultMode"))},
        "permissions_line": line_of(text, '"permissions"'),
        "hooks": hooks,
        "env_keys": sorted(obj["env"]) if isinstance(obj.get("env"), dict) else [],
        "model": _str(obj.get("model")),
        "sandbox": bool(sandbox.get("enabled", True)) if isinstance(sandbox, dict)
        else (bool(sandbox) if sandbox is not None else None),
        "lines": len(text.splitlines()),
    }


def parse_mcp(text: str, path: str, siblings: list[str]) -> dict:
    obj = _load(text)
    servers_obj = obj.get("mcpServers")
    if not isinstance(servers_obj, dict):
        raise ParseError("no_mcp_servers", {"servers": []})
    servers = []
    for name, s in servers_obj.items():
        if not isinstance(s, dict):
            continue
        cmd = _str(s.get("command"))
        exe = cmd.rsplit("/", 1)[-1] if cmd else None
        args = _strs(s.get("args"))
        url = _str(s.get("url"))
        servers.append({
            "name": name,
            "transport": _str(s.get("type")) or ("stdio" if cmd else ("http" if url else None)),
            "command": exe,
            "package": next((a for a in args if not a.startswith("-")), None) if exe in LAUNCHERS else None,
            "url_host": _host(url) if url else None,
            "env_keys": sorted(s["env"]) if isinstance(s.get("env"), dict) else [],
            "line": line_of(text, json.dumps(name)),
        })
    return {"servers": servers}


def parse_manifest(text: str, path: str, siblings: list[str]) -> dict:
    obj = _load(text)
    return {"name": _str(obj.get("name")), "version": _str(obj.get("version")),
            "description": _str(obj.get("description")), "keys": sorted(obj)}
