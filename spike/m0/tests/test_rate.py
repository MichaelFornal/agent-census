from m0.rate import last_rate_limited, rate_between


def test_rate_between_snapshots():
    a = {"t": 1000.0, "cache_lines": 500, "rate_limited": 10}
    b = {"t": 2800.0, "cache_lines": 560, "rate_limited": 40}
    assert rate_between(a, b) == {"window_s": 1800.0, "successful_requests": 60, "req_per_min": 2.0,
                                  "rate_limited": 30}


def test_last_rate_limited_reads_latest_progress_line():
    log = ("  s: 25 nodes, {'http_requests': 26, 'cache_hits': 1, 'rate_limited': 2, 'server_errors': 0}\n"
           "  s: 50 nodes, {'http_requests': 53, 'cache_hits': 1, 'rate_limited': 4, 'server_errors': 0}\n")
    assert last_rate_limited(log) == 4
    assert last_rate_limited("") == 0
