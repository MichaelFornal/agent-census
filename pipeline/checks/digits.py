"""Fail when .astro page copy contains a digit (PRD §7): every printed number comes from facts.json.

A small linear scanner (not regex stripping) flags a digit in:
  1. visible template text, including JSX nested inside {expressions};
  2. quoted attribute / prop values, except exempt names (href, class, data-*, ...);
  3. string literals in frontmatter, expressions and <script> (import specifiers and paths are exempt);
  4. any set:html attribute.
<style> blocks and <!-- --> comments are ignored. Numeric literals in code are never flagged.
"""
import bisect
import re
import sys
from pathlib import Path

EXEMPT_ATTRS = {"href", "src", "class", "id", "charset", "content", "name", "rel", "lang", "type", "style",
                "viewBox", "width", "height", "key"}
EXEMPT_ATTR_PREFIXES = ("data-", "is:", "client:", "set:")
EXEMPT_STRING_PREFIXES = ("/", "./", "../", "#", "http")
VOID = {"meta", "br", "img", "input", "hr", "link", "source", "area", "base", "col", "embed", "wbr", "track", "param"}
DIGIT = re.compile(r"\d")


class _Scanner:
    def __init__(self, source: str):
        self.s = source
        self.n = len(source)
        self.found: list[tuple[int, str]] = []
        self.starts = [0] + [m.end() for m in re.finditer("\n", source)]

    def line(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos)

    def add(self, pos: int, snippet: str) -> None:
        item = (self.line(pos), snippet)
        if item not in self.found:
            self.found.append(item)

    def run(self) -> list[tuple[int, str]]:
        s, i = self.s, 0
        m = re.match(r"---\n", s)
        if m:
            close = s.find("\n---\n", 3)
            if close != -1:
                full = self.n
                self.n = close + 1
                self.code(4)
                self.n = full
                i = close + 5
        self.markup(i, False)
        return sorted(self.found)

    # ---- code -----------------------------------------------------------------------------------
    def code(self, i: int) -> int:
        s = self.s
        depth = 0
        while i < self.n:
            c = s[i]
            if c in "'\"":
                i = self.string(i)
            elif c == "`":
                i = self.template(i + 1)
            elif s.startswith("//", i):
                j = s.find("\n", i)
                i = self.n if j < 0 or j >= self.n else j
            elif s.startswith("/*", i):
                j = s.find("*/", i + 2)
                i = self.n if j < 0 else j + 2
            elif c == "{":
                depth += 1
                i += 1
            elif c == "}":
                if depth == 0:
                    return i + 1
                depth -= 1
                i += 1
            elif c == "<" and self.jsx_start(i):
                i = self.markup(i, True)
            else:
                i += 1
        return i

    def jsx_start(self, i: int) -> bool:
        s = self.s
        if i + 1 >= self.n or not (s[i + 1].isalpha() or s[i + 1] == ">"):
            return False
        k = i - 1
        while k >= 0 and s[k].isspace():
            k -= 1
        if k < 0 or s[k] in "(,=?:&|{[>;":
            return True
        return s[max(0, k - 5):k + 1] == "return"

    def exempt_string(self, pos: int, text: str) -> bool:
        if text.startswith(EXEMPT_STRING_PREFIXES):
            return True
        ls = self.s.rfind("\n", 0, pos) + 1
        line = self.s[ls:pos].lstrip()
        return line.startswith("import") or (line.startswith("export") and " from" in line)

    def flag_text(self, pos: int, text: str) -> None:
        m = DIGIT.search(text)
        if m:
            self.add(pos + m.start(), text.strip())

    def string(self, i: int) -> int:
        s, q = self.s, self.s[i]
        j = i + 1
        while j < self.n and s[j] != q and s[j] != "\n":
            j += 2 if s[j] == "\\" else 1
        text = s[i + 1:j]
        if not self.exempt_string(i, text):
            self.flag_text(i + 1, text)
        return j + 1

    def template(self, i: int) -> int:
        s = self.s
        pieces: list[tuple[int, str]] = []
        seg = i
        while i < self.n:
            c = s[i]
            if c == "\\":
                i += 2
            elif c == "`":
                pieces.append((seg, s[seg:i]))
                i += 1
                break
            elif c == "$" and s.startswith("${", i):
                pieces.append((seg, s[seg:i]))
                i = self.code(i + 2)
                seg = i
            else:
                i += 1
        if pieces and not self.exempt_string(pieces[0][0], pieces[0][1]):
            for pos, text in pieces:
                self.flag_text(pos, text)
        return i

    # ---- markup ---------------------------------------------------------------------------------
    def markup(self, i: int, nested: bool) -> int:
        s = self.s
        depth = 0
        text = i
        while i < self.n:
            c = s[i]
            if s.startswith("<!--", i):
                self.text(text, i)
                j = s.find("-->", i + 4)
                i = self.n if j < 0 else j + 3
                text = i
            elif c == "<" and i + 1 < self.n and (s[i + 1].isalpha() or s[i + 1] in "/!>"):
                self.text(text, i)
                j, name, closing, selfclosing = self.tag(i)
                lname = name.lower()
                if lname in ("script", "style") and not closing and not selfclosing:
                    end = re.compile(rf"</{lname}\s*>", re.I).search(s, j)
                    stop = end.start() if end else self.n
                    if lname == "script":
                        full = self.n
                        self.n = stop
                        self.code(j)
                        self.n = full
                    j = end.end() if end else self.n
                    selfclosing = True
                i = text = j
                if nested:
                    if closing:
                        depth -= 1
                    elif not (selfclosing or lname in VOID or name.startswith("!")):
                        depth += 1
                    if depth <= 0:
                        return i
            elif c == "{":
                self.text(text, i)
                i = text = self.code(i + 1)
            else:
                i += 1
        self.text(text, i)
        return i

    def text(self, a: int, b: int) -> None:
        pos = a
        for line in self.s[a:b].split("\n"):
            self.flag_text(pos, line)
            pos += len(line) + 1

    def tag(self, i: int) -> tuple[int, str, bool, bool]:
        s = self.s
        j = i + 1
        if s[j] == "!":
            k = s.find(">", j)
            return (self.n if k < 0 else k + 1), "!", False, True
        closing = s[j] == "/"
        if closing:
            j += 1
        m = re.compile(r"[A-Za-z0-9:_.\-]*").match(s, j)
        name = m.group(0) if m else ""
        j = m.end() if m else j
        while j < self.n:
            while j < self.n and s[j].isspace():
                j += 1
            if j >= self.n:
                break
            if s[j] == ">":
                return j + 1, name, closing, False
            if s.startswith("/>", j):
                return j + 2, name, closing, True
            if s[j] == "{":
                j = self.code(j + 1)
                continue
            if s[j] == "/":
                j += 1
                continue
            k = j
            while k < self.n and not s[k].isspace() and s[k] not in "=>/":
                k += 1
            attr = s[j:k]
            if attr == "set:html":
                self.add(j, "set:html")
            j = k
            if j < self.n and s[j] == "=":
                j += 1
                if j < self.n and s[j] in "'\"":
                    q = s[j]
                    end = s.find(q, j + 1)
                    end = self.n if end < 0 else end
                    exempt = attr in EXEMPT_ATTRS or attr.startswith(EXEMPT_ATTR_PREFIXES)
                    if not exempt:
                        self.flag_text(j + 1, s[j + 1:end])
                    j = end + 1
                elif j < self.n and s[j] == "{":
                    j = self.code(j + 1)
                else:
                    while j < self.n and not s[j].isspace() and s[j] != ">":
                        j += 1
            elif k == j and not attr:
                j += 1
        return j, name, closing, False


def copy_digits(source: str) -> list[tuple[int, str]]:
    return _Scanner(source).run()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else "site/src")
    files = sorted(root.rglob("*.astro"))
    if not files:
        print(f"no .astro files found under {root}")
        return 1
    bad = [(p, i, line) for p in files for i, line in copy_digits(p.read_text())]
    for p, i, line in bad:
        print(f"{p}:{i}: digit in page copy: {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
