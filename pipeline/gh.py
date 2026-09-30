"""GitHub clients: paced, cached code search and GraphQL (PRD §4 S1, S2)."""
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from pipeline.jsonl import append_jsonl, read_jsonl

API = "https://api.github.com"
SECONDARY_FLOOR_S = 60.0


def github_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    return subprocess.run(["gh", "auth", "token"], check=True, capture_output=True, text=True).stdout.strip()


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@dataclass
class Pacer:
    """Spaces requests at base_interval * penalty. Penalty doubles on a rate limit and halves on success."""

    base_interval: float = 6.0  # 10 req/min
    max_penalty: float = 64.0
    penalty: float = 1.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            due = self._last + self.base_interval * self.penalty
            now = self.clock()
            if due > now:
                self.sleep(due - now)
        self._last = self.clock()

    def on_success(self) -> None:
        self.penalty = max(1.0, self.penalty / 2)

    def on_rate_limit(self, retry_after: float | None) -> None:
        """retry_after: seconds GitHub asked for; 0.0 = rate-limited with no hint; None = server error."""
        self.penalty = min(self.max_penalty, self.penalty * 2)
        floor = SECONDARY_FLOOR_S if retry_after == 0.0 else 0.0  # GitHub: wait >= 1 min when no hint
        self.sleep(max(retry_after or 0.0, floor, self.base_interval * self.penalty))
        self._last = None


def rate_limit_wait(resp: httpx.Response, now: float | None = None) -> float | None:
    """Seconds to wait if resp is a rate-limit response (0.0 when GitHub gives no hint), else None."""
    remaining = resp.headers.get("x-ratelimit-remaining")
    limited = resp.status_code == 429 or (
        resp.status_code == 403 and (remaining == "0" or "rate limit" in resp.text.lower())
    )
    if not limited:
        return None
    if "retry-after" in resp.headers:
        return float(resp.headers["retry-after"])
    if remaining == "0" and "x-ratelimit-reset" in resp.headers:
        return max(0.0, float(resp.headers["x-ratelimit-reset"]) - (time.time() if now is None else now))
    return 0.0


class SearchClient:
    """REST code search. Every response is cached in JSONL, so a killed run resumes from the cache."""

    def __init__(self, token: str, cache_path: Path, pacer: Pacer | None = None,
                 transport: httpx.BaseTransport | None = None, max_attempts: int = 10,
                 wall: Callable[[], float] = time.time) -> None:
        self.http = httpx.Client(base_url=API, headers=_headers(token), timeout=60.0, transport=transport)
        self.pacer = pacer or Pacer()
        self.wall = wall
        self.cache_path = cache_path
        self.max_attempts = max_attempts
        self.cache = {r["key"]: r["body"] for r in read_jsonl(cache_path)}
        self.stats = {"http_requests": 0, "cache_hits": 0, "rate_limited": 0, "server_errors": 0}

    def search(self, q: str, page: int = 1, per_page: int = 100) -> dict:
        key = f"{q}|{page}|{per_page}"
        if key in self.cache:
            self.stats["cache_hits"] += 1
            return self.cache[key]
        for _ in range(self.max_attempts):
            self.pacer.wait()
            try:
                resp = self.http.get("/search/code", params={"q": q, "page": page, "per_page": per_page})
            except httpx.TransportError:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
                continue
            self.stats["http_requests"] += 1
            wait = rate_limit_wait(resp)
            if wait is not None:
                self.stats["rate_limited"] += 1
                self.pacer.on_rate_limit(wait)
                continue
            if resp.status_code >= 500 or resp.status_code == 408:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
                continue
            resp.raise_for_status()
            raw = resp.json()
            body = {
                "total_count": raw["total_count"],
                "incomplete_results": raw["incomplete_results"],
                "items": [
                    {"repo": it["repository"]["full_name"], "fork": it["repository"]["fork"],
                     "path": it["path"], "sha": it["sha"]}
                    for it in raw["items"]
                ],
            }
            self.cache[key] = body
            append_jsonl(self.cache_path, {"key": key, "body": body})
            self.pacer.on_success()
            if resp.headers.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in resp.headers:
                # Window exhausted: wait for the reset instead of spending a request on a 403.
                self.pacer.sleep(max(0.0, float(resp.headers["x-ratelimit-reset"]) - self.wall()) + 1.0)
            return body
        raise RuntimeError(f"code search failed after {self.max_attempts} attempts: {key}")


class GraphQLClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None, timeout: float = 60.0,
                 pacer: Pacer | None = None, max_attempts: int = 6) -> None:
        self.http = httpx.Client(base_url=API, headers=_headers(token), timeout=timeout, transport=transport)
        self.timeout = timeout
        self.pacer = pacer or Pacer(base_interval=1.0)
        self.max_attempts = max_attempts
        self.stats = {"posts": 0, "rate_limited": 0, "server_errors": 0, "latency_s": 0.0}

    def post(self, query: str) -> tuple[int, dict, float]:
        """Returns (status, body, latency_s). httpx.TimeoutException propagates to the caller.

        A 5xx is returned (the caller shrinks the batch) after a backoff; it never counts as success.
        """
        for _ in range(self.max_attempts):
            self.pacer.wait()
            t0 = time.monotonic()
            resp = self.http.post("/graphql", json={"query": query})
            latency = time.monotonic() - t0
            self.stats["posts"] += 1
            self.stats["latency_s"] += latency
            wait = rate_limit_wait(resp)
            if wait is not None:
                self.stats["rate_limited"] += 1
                self.pacer.on_rate_limit(wait)
                continue
            if resp.status_code >= 500:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
            else:
                self.pacer.on_success()
            try:
                body = resp.json()
            except ValueError:
                body = {"errors": [{"type": "HTTP", "message": resp.text[:300]}]}
            return resp.status_code, body, latency
        raise RuntimeError(f"graphql rate-limited on all {self.max_attempts} attempts")
