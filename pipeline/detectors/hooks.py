"""Hook detectors over settings.json and settings.local.json (PRD §5, Hooks)."""
import re

from pipeline.detectors.base import Evidence, Harness, ev

VERIFY_RE = re.compile(r"\b(pytest|tests?|lint|eslint|ruff|mypy|tsc|typecheck|type-check|clippy|jest|vitest"
                       r"|go vet|cargo check)\b", re.I)
FORMAT_RE = re.compile(r"\b(prettier|black|ruff format|gofmt|goimports|rustfmt|biome|swiftformat|clang-format)\b"
                       r"|\beslint\b.*--fix", re.I)
EVENT_TECHNIQUES = {"PreToolUse": "hook_pretooluse_guard", "SessionStart": "hook_sessionstart_context",
                    "UserPromptSubmit": "hook_userpromptsubmit", "Notification": "hook_notification"}


def detect_hooks(h: Harness) -> list[Evidence]:
    out = []
    for a in h.of("settings", "settings_local"):
        for hk in a.parsed.get("hooks") or []:
            cmd, line, event = hk.get("command") or "", hk.get("line"), hk.get("event")
            if event in ("Stop", "SubagentStop") and VERIFY_RE.search(cmd):
                out.append(ev("hook_stop_gate", a, line))
            if event == "PostToolUse" and FORMAT_RE.search(cmd):
                out.append(ev("hook_posttooluse_formatter", a, line))
            if event in EVENT_TECHNIQUES:
                out.append(ev(EVENT_TECHNIQUES[event], a, line))
    return out
