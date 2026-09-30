"""claude -p as a structured-output function on the Max plan (PRD §4 S6).

Artifact text is untrusted, so every built-in tool is off (--tools ""), MCP servers and skills are off,
and the call runs in an empty temp dir that loads only project settings. --json-schema returns the
answer in `structured_output`.
"""
import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass

LIMIT_RE = re.compile(r"usage limit|rate limit|quota|\b429\b|limit reached", re.I)
CALL_TIMEOUT_S = 900


@dataclass
class CallResult:
    data: dict | None
    error: str | None
    wall_s: float
    cost_usd: float | None


class LLMLimitReached(Exception):
    """The plan refused for quota reasons: the stage stops instead of retrying."""


def claude_args(model: str, system: str, schema: dict) -> list[str]:
    return ["claude", "-p", "--output-format", "json", "--tools", "", "--setting-sources", "project",
            "--strict-mcp-config", "--disable-slash-commands",
            "--model", model, "--system-prompt", system, "--json-schema", json.dumps(schema)]


def parse_cli_output(returncode: int, stdout: str, stderr: str, wall_s: float) -> CallResult:
    """A plan limit is raised only on a real signal: api_error_status 429, or LIMIT_RE matching stderr
    or the envelope's error text. Raw stdout is never pattern-matched (its digits would hit 429)."""
    try:
        envelope = json.loads(stdout)
        parse_err = "envelope is not an object" if not isinstance(envelope, dict) else ""
    except json.JSONDecodeError as e:
        envelope, parse_err = None, str(e)
    if not isinstance(envelope, dict):
        if returncode != 0:
            err = (stderr or stdout)[-500:].strip()
            if LIMIT_RE.search(stderr or ""):
                raise LLMLimitReached(err)
            return CallResult(None, f"exit {returncode}: {err}", wall_s, None)
        return CallResult(None, f"envelope: {parse_err}", wall_s, None)
    cost = envelope.get("total_cost_usd")
    msg = str(envelope.get("result"))[:300] if envelope.get("is_error") else ""
    if envelope.get("api_error_status") == 429 or LIMIT_RE.search(stderr or "") or LIMIT_RE.search(msg):
        raise LLMLimitReached(msg or (stderr or "").strip()[-500:] or "api_error_status 429")
    if returncode != 0:
        return CallResult(None, f"exit {returncode}: {msg or (stderr or '').strip()[-300:]}", wall_s, cost)
    if envelope.get("is_error"):
        return CallResult(None, f"is_error: {msg}", wall_s, cost)
    data = envelope.get("structured_output")
    if data is None:
        try:
            data = json.loads(envelope.get("result") or "")
        except json.JSONDecodeError:
            return CallResult(None, "no structured_output", wall_s, cost)
    if not isinstance(data, dict):
        return CallResult(None, "structured_output is not an object", wall_s, cost)
    return CallResult(data, None, wall_s, cost)


class ClaudeCLI:
    def call(self, model: str, system: str, prompt: str, schema: dict) -> CallResult:
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory() as cwd:
            try:
                proc = subprocess.run(claude_args(model, system, schema), input=prompt, capture_output=True,
                                      text=True, timeout=CALL_TIMEOUT_S, cwd=cwd)
            except subprocess.TimeoutExpired:
                return CallResult(None, "timeout", time.monotonic() - t0, None)
        return parse_cli_output(proc.returncode, proc.stdout, proc.stderr, time.monotonic() - t0)
