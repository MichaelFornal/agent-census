import httpx

from m0.gh import Pacer, SearchClient, rate_limit_wait


def test_pacer_spaces_requests_at_base_interval(clock):
    p = Pacer(base_interval=6.0, clock=clock.now, sleep=clock.sleep)
    p.wait()
    p.wait()
    assert clock.sleeps == [6.0]


def test_rate_limit_doubles_penalty_and_success_decays_it(clock):
    p = Pacer(base_interval=6.0, clock=clock.now, sleep=clock.sleep)
    p.wait()
    p.on_rate_limit(None)
    assert p.penalty == 2.0
    assert clock.sleeps == [12.0]
    p.on_rate_limit(None)
    assert p.penalty == 4.0
    p.on_success()
    assert p.penalty == 2.0
    p.on_success()
    p.on_success()
    assert p.penalty == 1.0


def test_retry_after_longer_than_penalty_wins(clock):
    p = Pacer(base_interval=6.0, clock=clock.now, sleep=clock.sleep)
    p.on_rate_limit(90.0)
    assert clock.sleeps == [90.0]


def test_penalty_is_capped(clock):
    p = Pacer(base_interval=1.0, max_penalty=8.0, clock=clock.now, sleep=clock.sleep)
    for _ in range(10):
        p.on_rate_limit(None)
    assert p.penalty == 8.0


def test_rate_limit_wait_429_uses_retry_after():
    assert rate_limit_wait(httpx.Response(429, headers={"retry-after": "30"})) == 30.0


def test_rate_limit_wait_secondary_limit_403_without_hint():
    resp = httpx.Response(403, text="You have exceeded a secondary rate limit.")
    assert rate_limit_wait(resp) == 0.0


def test_rate_limit_wait_primary_limit_uses_reset_header():
    resp = httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1000"})
    assert rate_limit_wait(resp, now=940.0) == 60.0


def test_rate_limit_wait_ignores_other_statuses():
    assert rate_limit_wait(httpx.Response(200)) is None
    assert rate_limit_wait(httpx.Response(422, text="Validation Failed")) is None


SEARCH_OK = {
    "total_count": 5,
    "incomplete_results": False,
    "items": [
        {"name": "CLAUDE.md", "path": "CLAUDE.md", "sha": "abc",
         "repository": {"full_name": "o/r", "fork": False, "id": 1}}
    ],
}


def test_search_retries_after_429_then_caches(tmp_path, clock):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.params["q"])
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "30"})
        return httpx.Response(200, json=SEARCH_OK)

    cache = tmp_path / "cache.jsonl"
    client = SearchClient("t", cache, pacer=Pacer(clock=clock.now, sleep=clock.sleep),
                          transport=httpx.MockTransport(handler))
    body = client.search("filename:CLAUDE.md", per_page=1)
    assert body == {"total_count": 5, "incomplete_results": False,
                    "items": [{"repo": "o/r", "fork": False, "path": "CLAUDE.md", "sha": "abc"}]}
    assert client.stats == {"http_requests": 2, "cache_hits": 0, "rate_limited": 1, "server_errors": 0}
    assert clock.sleeps == [30.0]

    def boom(req: httpx.Request) -> httpx.Response:
        raise AssertionError("should have been a cache hit")

    again = SearchClient("t", cache, pacer=Pacer(clock=lambda: 0.0, sleep=lambda s: None),
                         transport=httpx.MockTransport(boom))
    assert again.search("filename:CLAUDE.md", per_page=1) == body
    assert again.stats["cache_hits"] == 1


def test_search_server_error_backs_off_then_succeeds(tmp_path, clock):
    responses = iter([httpx.Response(502), httpx.Response(200, json=SEARCH_OK)])
    client = SearchClient("t", tmp_path / "c.jsonl", pacer=Pacer(clock=clock.now, sleep=clock.sleep),
                          transport=httpx.MockTransport(lambda req: next(responses)))
    assert client.search("q")["total_count"] == 5
    assert client.stats["server_errors"] == 1
