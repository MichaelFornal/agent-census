"""Parsers for markdown harness files and hook scripts (PRD §4 S3)."""
import json
import re

import yaml

from pipeline.parsers.errors import ParseError

FENCE = re.compile(r"^\s*(```+|~~~+)\s*([\w+-]*)")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
IMPORT = re.compile(r"(?:^|\s)@((?:~/|\./|\.\./)?[\w.-]+(?:/[\w.-]+)*)")
IMPORT_EXT = re.compile(r"\.(md|txt|json|ya?ml)$")
HOOK_LANGS = {"sh": "shell", "bash": "shell", "zsh": "shell", "py": "python", "js": "javascript",
              "mjs": "javascript", "cjs": "javascript", "ts": "typescript", "rb": "ruby"}


def scan_markdown(lines: list[str], offset: int = 0) -> dict:
    headings, blocks, imports = [], [], []
    fence: tuple[str, str | None, int] | None = None
    for i, line in enumerate(lines, start=offset + 1):
        m = FENCE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0][0] and len(m.group(1)) >= len(fence[0]) and not m.group(2):
                blocks.append({"lang": fence[1], "start_line": fence[2], "end_line": i})
                fence = None
            continue
        if m:
            fence = (m.group(1), m.group(2) or None, i)
            continue
        if h := HEADING.match(line):
            headings.append({"level": len(h.group(1)), "text": h.group(2), "line": i})
        for im in IMPORT.finditer(line):
            target = im.group(1).rstrip(".")
            if "/" in target or IMPORT_EXT.search(target):
                imports.append({"target": target, "line": i})
    if fence is not None:
        blocks.append({"lang": fence[1], "start_line": fence[2], "end_line": offset + len(lines)})
    return {"headings": headings, "code_blocks": blocks, "imports": imports}


def split_frontmatter(text: str) -> tuple[dict, int, str | None]:
    """(frontmatter, first body line (1-based), error_class)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, 1, None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            try:
                fm = yaml.safe_load("\n".join(lines[1:i]))
            except yaml.YAMLError:
                return {}, i + 2, "frontmatter_invalid"
            if fm is None:
                fm = {}
            if not isinstance(fm, dict):
                return {}, i + 2, "frontmatter_invalid"
            return json.loads(json.dumps(fm, default=str)), i + 2, None
    return {}, 1, "frontmatter_unclosed"


def _as_list(v: object) -> list[str]:
    if isinstance(v, str):
        return [t.strip() for t in v.split(",") if t.strip()]
    if isinstance(v, list):
        return [str(t) for t in v]
    return []


def _parse_doc(text: str) -> tuple[dict, str | None]:
    fm, body_start, err = split_frontmatter(text)
    lines = text.splitlines()
    d = scan_markdown(lines[body_start - 1:], offset=body_start - 1)
    arg_line = next((i for i, line in enumerate(lines, 1) if "$ARGUMENTS" in line), None)
    d.update(frontmatter=fm, frontmatter_end_line=body_start - 1, bytes=len(text.encode()), lines=len(lines),
             has_arguments=arg_line is not None, arguments_line=arg_line)
    return d, err


def _finish(d: dict, err: str | None) -> dict:
    if err:
        raise ParseError(err, d)
    return d


def parse_claude_md(text: str, path: str, siblings: list[str]) -> dict:
    lines = text.splitlines()
    d = scan_markdown(lines)
    d.update(bytes=len(text.encode()), lines=len(lines), nested=path != "CLAUDE.md")
    return d


def parse_skill(text: str, path: str, siblings: list[str]) -> dict:
    d, err = _parse_doc(text)
    skill_dir = path.rsplit("/", 1)[0] + "/"
    d["resources"] = sorted({s[len(skill_dir):].split("/")[0] for s in siblings
                             if s.startswith(skill_dir) and "/" in s[len(skill_dir):]})
    return _finish(d, err)


def parse_agent(text: str, path: str, siblings: list[str]) -> dict:
    d, err = _parse_doc(text)
    d["tools"] = _as_list(d["frontmatter"].get("tools"))
    model = d["frontmatter"].get("model")
    d["model"] = model if isinstance(model, str) else None
    return _finish(d, err)


def parse_command(text: str, path: str, siblings: list[str]) -> dict:
    d, err = _parse_doc(text)
    d["allowed_tools"] = _as_list(d["frontmatter"].get("allowed-tools"))
    return _finish(d, err)


def parse_hook_script(text: str, path: str, siblings: list[str]) -> dict:
    lines = text.splitlines()
    name = path.rsplit("/", 1)[-1]
    lang = HOOK_LANGS.get(name.rsplit(".", 1)[-1]) if "." in name else None
    if lang is None and lines and lines[0].startswith("#!"):
        lang = next((v for k, v in HOOK_LANGS.items() if k in lines[0]), None)
    return {"bytes": len(text.encode()), "lines": len(lines), "language": lang or "other"}
