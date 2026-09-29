"""Gitleaks-style secret redaction, applied before any blob is written (PRD §4 S2).

Rules may keep surrounding context through named groups `pre` and `post`; only the rest is replaced.
The generic key=value rule has a gate: M0 found it over-redacting code such as `tokens = count(text)`.
"""
import math
import re
from collections import Counter
from collections.abc import Callable

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_.]*(\([^)]*\))?")
ENV_REF = re.compile(r"process\.env|os\.environ|getenv|ENV\[", re.I)


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(k / n * math.log2(k / n) for k in Counter(s).values())


def looks_secret(value: str) -> bool:
    """True for values shaped like credentials; False for code identifiers, calls and env lookups."""
    v = value.strip("\"'")
    if len(v) < 10 or ENV_REF.search(v) or IDENTIFIER.fullmatch(v):
        return False
    classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))
    return classes >= 2 and _entropy(v) >= 3.0


Gate = Callable[[str], bool] | None

# Order matters: specific token shapes run before the generic key=value rule,
# and anthropic_key runs before openai_key (both start with "sk-").
RULES: list[tuple[str, re.Pattern[str], Gate]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), None),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), None),
    ("url_credentials", re.compile(r"(?P<pre>\b[a-z][a-z0-9+.-]*://[^\s:/@\"']+:)[^\s@\"'/]+(?P<post>@)", re.I), None),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"), None),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"), None),
    ("stripe_key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"), None),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{30,}"), None),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), None),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), None),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), None),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), None),
    ("bearer", re.compile(r"(?i)(?P<pre>bearer\s+)[A-Za-z0-9._\-]{20,}"), None),
    ("cli_flag_secret", re.compile(
        r'(?i)(?P<pre>--(?:api-key|token|password|secret)(?:[= ]\s*"?|",\s*"))[^"\s,)]{6,}'), None),
    ("curl_user_password", re.compile(r'(?P<pre>\s-u\s+[^\s:"]+:)[^\s"@)]{4,}'), None),
    ("mysql_password", re.compile(r'(?P<pre>\bmysql\b[^"\n)]*?\s-p)[^\s"\')]{4,}'), None),
    ("assigned_secret", re.compile(
        r'(?i)(?P<pre>"?[A-Za-z0-9_-]*(?:API_KEY|APIKEY|API-KEY|SECRET|TOKEN|PASSWORD|PASSWD|_KEY)[A-Za-z0-9_-]*"?'
        r'\s*[:=]\s*"?)[^"\s,{}$\[]{8,}'), looks_secret),
]


def redact(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for name, rx, gate in RULES:
        def sub(m: re.Match[str], name: str = name, gate: Gate = gate) -> str:
            g = m.groupdict()
            pre, post = g.get("pre") or "", g.get("post") or ""
            whole = m.group(0)
            if gate is not None and not gate(whole[len(pre):len(whole) - len(post)]):
                return whole
            counts[name] = counts.get(name, 0) + 1
            return f"{pre}[REDACTED:{name}]{post}"
        text = rx.sub(sub, text)
    return text, counts
