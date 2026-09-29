"""Gitleaks-style secret redaction, applied before any blob is written. Throwaway code."""
import re

# Order matters: specific token shapes run before the generic key=value rule,
# and anthropic_key runs before openai_key (both start with "sk-").
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("bearer", re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{20,}")),
    ("assigned_secret", re.compile(
        r'(?i)("?[A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*"?\s*[:=]\s*"?)([^"\s,{}$\[]{8,})')),
]


def redact(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for name, rx in RULES:
        def sub(m: re.Match[str], name: str = name, rx: re.Pattern[str] = rx) -> str:
            counts[name] = counts.get(name, 0) + 1
            prefix = m.group(1) if rx.groups else ""
            return f"{prefix}[REDACTED:{name}]"
        text = rx.sub(sub, text)
    return text, counts
