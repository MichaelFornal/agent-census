"""Fail when .astro page copy contains a digit (PRD §7): every printed number comes from facts.json.

Frontmatter, <script>, <style>, comments, {expressions} and tags (with their attributes) are code, not
copy, and are blanked out while keeping line numbers.
"""
import re
import sys
from pathlib import Path

FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
BLOCKS = re.compile(r"<(script|style)\b.*?</\1\s*>", re.S | re.I)
COMMENT = re.compile(r"<!--.*?-->", re.S)
TAG = re.compile(r"<[^>]*>")


def _blank(m: re.Match[str]) -> str:
    return "\n" * m.group(0).count("\n")


def strip_expressions(s: str) -> str:
    out, depth = [], 0
    for ch in s:
        if ch == "{":
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
        elif depth == 0 or ch == "\n":
            out.append(ch)
    return "".join(out)


def copy_digits(source: str) -> list[tuple[int, str]]:
    s = FRONTMATTER.sub(_blank, source)
    s = BLOCKS.sub(_blank, s)
    s = COMMENT.sub(_blank, s)
    s = strip_expressions(s)
    s = TAG.sub(_blank, s)
    return [(i, line.strip()) for i, line in enumerate(s.splitlines(), 1) if re.search(r"\d", line)]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else "site/src")
    bad = [(p, i, line) for p in sorted(root.rglob("*.astro")) for i, line in copy_digits(p.read_text())]
    for p, i, line in bad:
        print(f"{p}:{i}: digit in page copy: {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
