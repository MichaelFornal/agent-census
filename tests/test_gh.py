from pathlib import Path

import httpx
import pytest

from pipeline.gh import GraphQLClient, Pacer, RestClient, SearchClient, api_base, rate_limit_wait
from pipeline.jsonl import read_jsonl


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
    assert body == {"total_count": 5, "incomplete_results": False, "items": []}
    assert client.stats == {"http_requests": 2, "cache_hits": 0, "rate_limited": 1, "server_errors": 0,
                            "incomplete_retries": 0}
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


def test_search_sleeps_until_reset_when_window_is_exhausted(tmp_path, clock):
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1000"}
    client = SearchClient("t", tmp_path / "c.jsonl", pacer=Pacer(clock=clock.now, sleep=clock.sleep),
                          transport=httpx.MockTransport(lambda req: httpx.Response(200, json=SEARCH_OK, headers=headers)),
                          wall=lambda: 970.0)
    client.search("q")
    assert clock.sleeps == [31.0]
    assert client.stats["rate_limited"] == 0


def test_search_retries_after_408_request_timeout(tmp_path, clock):
    responses = iter([httpx.Response(408), httpx.Response(200, json=SEARCH_OK)])
    client = SearchClient("t", tmp_path / "c.jsonl", pacer=Pacer(clock=clock.now, sleep=clock.sleep),
                          transport=httpx.MockTransport(lambda req: next(responses)))
    assert client.search("q")["total_count"] == 5
    assert client.stats["server_errors"] == 1


def test_hintless_secondary_limit_waits_at_least_60s(clock):
    p = Pacer(base_interval=6.0, clock=clock.now, sleep=clock.sleep)
    p.on_rate_limit(0.0)
    assert clock.sleeps == [60.0]


def test_graphql_5xx_backs_off_instead_of_decaying_penalty(clock):
    pacer = Pacer(base_interval=1.0, clock=clock.now, sleep=clock.sleep)
    pacer.penalty = 4.0
    client = GraphQLClient("t", pacer=pacer,
                           transport=httpx.MockTransport(lambda req: httpx.Response(502, text="Bad Gateway")))
    status, body, _ = client.post("query { viewer { login } }")
    assert status == 502
    assert body["errors"][0]["type"] == "HTTP"
    assert pacer.penalty == 8.0
    assert client.stats["server_errors"] == 1 and client.stats["posts"] == 1


def still():
    return Pacer(base_interval=0.0, sleep=lambda s: None)


def test_only_counts_are_cached(tmp_path):
    cache = tmp_path / "c.jsonl"
    client = SearchClient("t", cache, pacer=still(),
                          transport=httpx.MockTransport(lambda req: httpx.Response(200, json=SEARCH_OK)))
    assert client.search("q", per_page=1)["items"] == []
    assert client.search("q", page=1, per_page=100)["items"][0]["repo"] == "o/r"
    client.search("q", page=1, per_page=100)
    assert [r["key"] for r in read_jsonl(cache)] == ["q|1|1"]
    assert (client.stats["http_requests"], client.stats["cache_hits"]) == (3, 0)


def test_incomplete_results_are_retried_and_never_cached(tmp_path):
    partial = {**SEARCH_OK, "incomplete_results": True, "total_count": 0}
    bodies = iter([partial, SEARCH_OK])
    cache = tmp_path / "c.jsonl"
    client = SearchClient("t", cache, pacer=still(),
                          transport=httpx.MockTransport(lambda req: httpx.Response(200, json=next(bodies))))
    assert client.search("q", per_page=1)["total_count"] == 5
    assert client.stats["incomplete_retries"] == 1
    stuck = SearchClient("t", tmp_path / "d.jsonl", pacer=still(),
                         transport=httpx.MockTransport(lambda req: httpx.Response(200, json=partial)))
    with pytest.raises(RuntimeError, match="incomplete_results"):
        stuck.search("q", per_page=1)
    assert stuck.stats["http_requests"] == 4  # one request and three retries
    assert read_jsonl(tmp_path / "d.jsonl") == []


def test_graphql_rate_limited_200_waits_for_reset_then_retries(clock):
    responses = iter([
        httpx.Response(200, json={"errors": [{"type": "RATE_LIMITED"}]}, headers={"x-ratelimit-reset": "1000"}),
        httpx.Response(200, json={"data": {"ok": 1}})])
    client = GraphQLClient("t", pacer=Pacer(base_interval=1.0, clock=clock.now, sleep=clock.sleep),
                           transport=httpx.MockTransport(lambda req: next(responses)), wall=lambda: 940.0)
    status, body, _ = client.post("query { viewer { login } }")
    assert (status, body) == (200, {"data": {"ok": 1}})
    assert clock.sleeps == [61.0] and client.stats["rate_limited"] == 1


def test_graphql_transport_error_is_status_zero():
    def down(req):
        raise httpx.ConnectError("down", request=req)

    client = GraphQLClient("t", pacer=still(), transport=httpx.MockTransport(down))
    status, body, _ = client.post("query { viewer { login } }")
    assert status == 0 and body["errors"][0]["type"] == "TRANSPORT"
    assert client.stats["server_errors"] == 1


def test_graphql_health_probe():
    up = GraphQLClient("t", pacer=still(), transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"data": {"rateLimit": {"remaining": 4999}}})))
    down = GraphQLClient("t", pacer=still(), transport=httpx.MockTransport(
        lambda req: httpx.Response(502, text="Bad Gateway")))
    assert up.healthy() is True and down.healthy() is False


def test_rest_get_returns_status_and_body_and_retries_5xx():
    responses = iter([httpx.Response(502, text="Bad Gateway"), httpx.Response(200, json={"tree": []})])
    client = RestClient("t", pacer=still(), transport=httpx.MockTransport(lambda req: next(responses)))
    assert client.get("/repos/o/r/git/trees/abc", {"recursive": "1"}) == (200, {"tree": []})
    assert client.stats == {"requests": 2, "rate_limited": 0, "server_errors": 1}


def test_rest_get_returns_404_without_retrying():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(404, json={"message": "Not Found"})

    client = RestClient("t", pacer=still(), transport=httpx.MockTransport(handler))
    assert client.get("/repos/o/r/git/trees/abc") == (404, {"message": "Not Found"})
    assert len(calls) == 1


def test_rest_get_gives_up_on_a_persistent_5xx():
    client = RestClient("t", pacer=still(), transport=httpx.MockTransport(lambda req: httpx.Response(503, text="x")))
    status, _ = client.get("/repos/o/r/git/trees/abc")
    assert status == 503 and client.stats["requests"] == 4


def test_rest_get_waits_out_a_rate_limit(clock):
    responses = iter([httpx.Response(429, headers={"retry-after": "30"}), httpx.Response(200, json={"ok": True})])
    client = RestClient("t", pacer=Pacer(base_interval=1.0, clock=clock.now, sleep=clock.sleep),
                        transport=httpx.MockTransport(lambda req: next(responses)))
    assert client.get("/x") == (200, {"ok": True})
    assert clock.sleeps == [30.0] and client.stats["rate_limited"] == 1


def test_api_base_follows_github_api_url(monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "http://127.0.0.1:9/")
    assert api_base() == "http://127.0.0.1:9"
    assert str(RestClient("t").http.base_url).rstrip("/") == "http://127.0.0.1:9"


def test_census_pace_zero_disables_sleeping(monkeypatch, clock):
    monkeypatch.setenv("CENSUS_PACE", "0")
    p = Pacer(base_interval=6.0, clock=clock.now, sleep=clock.sleep)
    p.wait()
    p.wait()
    p.on_rate_limit(0.0)
    assert sum(clock.sleeps) == 0.0


def test_a_scaled_pace_is_announced_once_per_process(monkeypatch, capsys):
    from pipeline import gh

    monkeypatch.setattr(gh, "_pace_announced", False)
    monkeypatch.setenv("CENSUS_PACE", "0.25")
    SearchClient("t", Path("/nonexistent/c.jsonl"))
    GraphQLClient("t")
    RestClient("t")
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "CENSUS_PACE=0.25" in err[0]
    monkeypatch.setattr(gh, "_pace_announced", False)
    monkeypatch.setenv("CENSUS_PACE", "1")
    RestClient("t")
    assert capsys.readouterr().err == ""
