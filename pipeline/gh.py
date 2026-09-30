"""GitHub clients: paced, cached code search and GraphQL (PRD §4 S1, S2)."""
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from pipeline.jsonl import append_jsonl, read_jsonl

INCOMPLETE_RETRIES = 3


def api_base() -> str:
    """GITHUB_API_URL points the clients at GitHub Enterprise, or at the fake server in the kill tests."""
    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def _pace() -> float:
    return float(os.environ.get("CENSUS_PACE", "1"))

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
    """Spaces requests at base_interval * penalty. Penalty doubles on a rate limit and halves on success.
    CENSUS_PACE scales every sleep; 0 disables pacing (tests against a fake server)."""

    base_interval: float = 6.0  # 10 req/min
    max_penalty: float = 64.0
    penalty: float = 1.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _last: float | None = None
    scale: float = field(default_factory=_pace)

    def wait(self) -> None:
        if self._last is not None:
            due = self._last + self.base_interval * self.penalty * self.scale
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
        self.sleep(max(retry_after or 0.0, floor, self.base_interval * self.penalty) * self.scale)
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


def _wait_for_window(pacer: Pacer, resp: httpx.Response, wall: Callable[[], float]) -> None:
    """Window exhausted: wait for the reset instead of spending a request on a 403."""
    if resp.headers.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in resp.headers:
        pacer.sleep((max(0.0, float(resp.headers["x-ratelimit-reset"]) - wall()) + 1.0) * pacer.scale)


class SearchClient:
    """REST code search. Lattice counts (per_page == 1) are cached in JSONL, so a killed walk replays
    without requests. Result pages are not cached: the runner commits them with their node, and caching
    every page would hold the whole result set in memory at full scale."""

    def __init__(self, token: str, cache_path: Path, pacer: Pacer | None = None,
                 transport: httpx.BaseTransport | None = None, max_attempts: int = 10,
                 wall: Callable[[], float] = time.time) -> None:
        self.http = httpx.Client(base_url=api_base(), headers=_headers(token), timeout=60.0, transport=transport)
        self.pacer = pacer or Pacer()
        self.wall = wall
        self.cache_path = cache_path
        self.max_attempts = max_attempts
        self.cache = {r["key"]: r["body"] for r in read_jsonl(cache_path)}
        self.stats = {"http_requests": 0, "cache_hits": 0, "rate_limited": 0, "server_errors": 0,
                      "incomplete_retries": 0}

    def search(self, q: str, page: int = 1, per_page: int = 100) -> dict:
        key = f"{q}|{page}|{per_page}"
        if key in self.cache:
            self.stats["cache_hits"] += 1
            return self.cache[key]
        incomplete = 0
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
            self.pacer.on_success()
            _wait_for_window(self.pacer, resp, self.wall)
            if raw["incomplete_results"] and incomplete < INCOMPLETE_RETRIES:
                incomplete += 1  # GitHub timed out its own search: the count or the page may be short
                self.stats["incomplete_retries"] += 1
                continue
            body = {
                "total_count": raw["total_count"],
                "incomplete_results": raw["incomplete_results"],
                "items": [] if per_page == 1 else [
                    {"repo": it["repository"]["full_name"], "fork": it["repository"]["fork"],
                     "path": it["path"], "sha": it["sha"]}
                    for it in raw["items"]
                ],
            }
            if per_page == 1 and not body["incomplete_results"]:
                self.cache[key] = body
                append_jsonl(self.cache_path, {"key": key, "body": body})
            return body
        raise RuntimeError(f"code search failed after {self.max_attempts} attempts: {key}")


class GraphQLClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None, timeout: float = 60.0,
                 pacer: Pacer | None = None, max_attempts: int = 6, wall: Callable[[], float] = time.time) -> None:
        self.http = httpx.Client(base_url=api_base(), headers=_headers(token), timeout=timeout, transport=transport)
        self.pacer = pacer or Pacer(base_interval=1.0)
        self.max_attempts = max_attempts
        self.wall = wall
        self.stats = {"posts": 0, "rate_limited": 0, "server_errors": 0, "latency_s": 0.0}

    def post(self, query: str) -> tuple[int, dict, float]:
        """Returns (status, body, latency_s). Status 0 is a transport error or a client timeout.

        A 5xx or a transport error is returned after a backoff (the caller shrinks the batch); it never
        counts as success. Rate limits are waited out here, including the primary limit, which GitHub
        reports as HTTP 200 with a RATE_LIMITED error.
        """
        for _ in range(self.max_attempts):
            self.pacer.wait()
            t0 = time.monotonic()
            try:
                resp = self.http.post("/graphql", json={"query": query})
            except httpx.TransportError as e:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
                return 0, {"errors": [{"type": "TRANSPORT", "message": str(e)[:300]}]}, time.monotonic() - t0
            latency = time.monotonic() - t0
            self.stats["posts"] += 1
            self.stats["latency_s"] += latency
            wait = rate_limit_wait(resp)
            if wait is not None:
                self.stats["rate_limited"] += 1
                self.pacer.on_rate_limit(wait)
                continue
            try:
                body = resp.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                body = {"errors": [{"type": "HTTP", "message": resp.text[:300]}]}
            if resp.status_code == 200 and any(e.get("type") == "RATE_LIMITED" for e in body.get("errors") or []):
                self.stats["rate_limited"] += 1
                reset = resp.headers.get("x-ratelimit-reset")
                self.pacer.on_rate_limit(max(0.0, float(reset) - self.wall()) + 1.0 if reset else 0.0)
                continue
            if resp.status_code >= 500:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
            else:
                self.pacer.on_success()
            return resp.status_code, body, latency
        raise RuntimeError(f"graphql rate-limited on all {self.max_attempts} attempts")

    def healthy(self) -> bool:
        """One cheap query. It tells a GitHub outage (stop the stage) from a repo GitHub cannot serve."""
        status, body, _ = self.post("query { rateLimit { remaining } }")
        return status == 200 and bool((body.get("data") or {}).get("rateLimit"))


class RestClient:
    """Paced REST GETs (git trees). 0.75 s apart keeps one caller under the 5,000 requests/hour core limit."""

    def __init__(self, token: str, transport: httpx.BaseTransport | None = None, pacer: Pacer | None = None,
                 max_attempts: int = 4, wall: Callable[[], float] = time.time) -> None:
        self.http = httpx.Client(base_url=api_base(), headers=_headers(token), timeout=60.0, transport=transport)
        self.pacer = pacer or Pacer(base_interval=0.75)
        self.max_attempts = max_attempts
        self.wall = wall
        self.stats = {"requests": 0, "rate_limited": 0, "server_errors": 0}

    def get(self, path: str, params: dict | None = None) -> tuple[int, dict]:
        """(status, body). Rate limits are waited out here. Status 0 or >= 500 means GitHub kept failing."""
        status, body = 0, {"message": "no attempt"}
        for _ in range(self.max_attempts):
            self.pacer.wait()
            try:
                resp = self.http.get(path, params=params)
            except httpx.TransportError as e:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
                status, body = 0, {"message": str(e)[:300]}
                continue
            self.stats["requests"] += 1
            wait = rate_limit_wait(resp)
            if wait is not None:
                self.stats["rate_limited"] += 1
                self.pacer.on_rate_limit(wait)
                status, body = 0, {"message": "rate limited"}
                continue
            try:
                parsed = resp.json()
            except ValueError:
                parsed = None
            status = resp.status_code
            body = parsed if isinstance(parsed, dict) else {"message": resp.text[:300]}
            if status >= 500:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
                continue
            self.pacer.on_success()
            _wait_for_window(self.pacer, resp, self.wall)
            return status, body
        return status, body
