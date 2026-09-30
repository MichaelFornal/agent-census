"""claude -p as a structured-output function on the Max plan (PRD §4 S6).

Artifact text is untrusted, so every built-in tool is off (--tools "") and the call runs in an empty
temp dir that loads only project settings. --json-schema returns the answer in `structured_output`.
"""
import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass

LIMIT_RE = re.compile(r"usage limit|rate limit|quota|429|limit reached", re.I)
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
            "--model", model, "--system-prompt", system, "--json-schema", json.dumps(schema)]


def parse_cli_output(returncode: int, stdout: str, stderr: str, wall_s: float) -> CallResult:
    if returncode != 0:
        err = (stderr or stdout)[-500:].strip()
        if LIMIT_RE.search(err):
            raise LLMLimitReached(err)
        return CallResult(None, f"exit {returncode}: {err}", wall_s, None)
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as e:
        return CallResult(None, f"envelope: {e}", wall_s, None)
    cost = envelope.get("total_cost_usd")
    if envelope.get("is_error"):
        msg = str(envelope.get("result"))[:300]
        if LIMIT_RE.search(msg):
            raise LLMLimitReached(msg)
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
