"""Gitleaks-style secret redaction, applied before any blob is written. Throwaway code.

Rules may keep surrounding context through named groups `pre` and `post`; only the rest is replaced.
"""
import re

# Order matters: specific token shapes run before the generic key=value rule,
# and anthropic_key runs before openai_key (both start with "sk-").
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("url_credentials", re.compile(r"(?P<pre>\b[a-z][a-z0-9+.-]*://[^\s:/@\"']+:)[^\s@\"'/]+(?P<post>@)", re.I)),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("stripe_key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("bearer", re.compile(r"(?i)(?P<pre>bearer\s+)[A-Za-z0-9._\-]{20,}")),
    ("cli_flag_secret", re.compile(
        r'(?i)(?P<pre>--(?:api-key|token|password|secret)(?:[= ]\s*"?|",\s*"))[^"\s,)]{6,}')),
    ("curl_user_password", re.compile(r'(?P<pre>\s-u\s+[^\s:"]+:)[^\s"@)]{4,}')),
    ("mysql_password", re.compile(r'(?P<pre>\bmysql\b[^"\n)]*?\s-p)[^\s"\')]{4,}')),
    ("assigned_secret", re.compile(
        r'(?i)(?P<pre>"?[A-Za-z0-9_-]*(?:API_KEY|APIKEY|API-KEY|SECRET|TOKEN|PASSWORD|PASSWD|_KEY)[A-Za-z0-9_-]*"?'
        r'\s*[:=]\s*"?)[^"\s,{}$\[]{8,}')),
]


def redact(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for name, rx in RULES:
        def sub(m: re.Match[str], name: str = name) -> str:
            counts[name] = counts.get(name, 0) + 1
            g = m.groupdict()
            return f"{g.get('pre') or ''}[REDACTED:{name}]{g.get('post') or ''}"
        text = rx.sub(sub, text)
    return text, counts
