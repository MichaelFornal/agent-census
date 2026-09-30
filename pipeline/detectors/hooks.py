"""Hook detectors over settings.json and settings.local.json (PRD §5, Hooks)."""
import re
import shlex

from pipeline.detectors.base import Evidence, Harness, ev

DIRECT_VERIFY = {"pytest", "eslint", "ruff", "mypy", "tsc", "jest", "vitest", "pyright", "rspec", "phpunit",
                 "golangci-lint"}
RUNNERS = {"npm", "pnpm", "yarn", "bun", "npx", "bunx", "uv", "uvx", "poetry", "make", "just", "cargo", "go",
           "deno", "tox", "nox"}
VERIFY_TASKS = {"test", "tests", "lint", "typecheck", "type-check", "check", "vet", "clippy", "pytest", "mypy",
                "ruff", "eslint", "tsc", "verify", "ci"}
DIRECT_FORMAT = {"prettier", "black", "gofmt", "goimports", "rustfmt", "biome", "swiftformat", "clang-format"}
SPLIT_RE = re.compile(r"&&|\|\||;|\|")


def _segments(cmd: str) -> list[list[str]]:
    out = []
    for seg in SPLIT_RE.split(cmd):
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        while toks and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", toks[0]):
            toks = toks[1:]
        if toks:
            out.append(toks)
    return out


def is_verify(cmd: str) -> bool:
    for toks in _segments(cmd):
        first = toks[0].rsplit("/", 1)[-1]
        if first in DIRECT_VERIFY or (first in RUNNERS and VERIFY_TASKS & set(toks[1:3])):
            return True
    return False


def is_format(cmd: str) -> bool:
    for toks in _segments(cmd):
        first = toks[0].rsplit("/", 1)[-1]
        nxt = set(toks[1:3])
        if first in DIRECT_FORMAT:
            return True
        if first == "ruff" and "format" in nxt:
            return True
        if first == "eslint" and "--fix" in toks:
            return True
        if first in RUNNERS and (nxt & (DIRECT_FORMAT | {"format"}) or ("eslint" in nxt and "--fix" in toks)):
            return True
    return False


EVENT_TECHNIQUES = {"PreToolUse": "hook_pretooluse_guard", "SessionStart": "hook_sessionstart_context",
                    "UserPromptSubmit": "hook_userpromptsubmit", "Notification": "hook_notification"}


def detect_hooks(h: Harness) -> list[Evidence]:
    out = []
    for a in h.of("settings", "settings_local"):
        for hk in a.parsed.get("hooks") or []:
            cmd, line, event = hk.get("command") or "", hk.get("line"), hk.get("event")
            if event in ("Stop", "SubagentStop") and is_verify(cmd):
                out.append(ev("hook_stop_gate", a, line))
            if event == "PostToolUse" and is_format(cmd):
                out.append(ev("hook_posttooluse_formatter", a, line))
            if event in EVENT_TECHNIQUES:
                out.append(ev(EVENT_TECHNIQUES[event], a, line))
    return out
