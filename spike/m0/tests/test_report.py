from m0.report import NOT_MEASURED, render

M = {
    "lattice": [{"seed": "filename:CLAUDE.md", "root_total": 790528, "nodes": 1650, "n_leaves": 820,
                 "n_overflows": 2, "unreachable": 1400, "fetch_requests": 8100, "projected_hours": 16.3}],
    "graphql": {"by_batch_size": {"50": {"batches": 10, "median_cost": 1, "median_latency_s": 2.5,
                                          "p90_latency_s": 4.0, "retry_rate": 0.1,
                                          "repos_per_hour_latency_bound": 72000.0}},
                "points_per_repo": 0.02, "repos_per_hour_points_bound": 250000.0, "files": 5000,
                "files_missing": 40, "files_binary": 1, "files_truncated": 0, "secrets_redacted": 7},
    "dedup": {"kinds": {"skill": {"n": 1000, "exact": 400, "normalized": 350,
                                  "minhash": {"0.5": 200, "0.7": 250, "0.8": 300, "0.9": 320},
                                  "projected_distinct_0.8": 129946}},
              "rarefaction": {"1000": 0.5, "4960": 0.4}, "projected_distinct_total_0.8": 600000, "skipped": {}},
    "llm": {"groups": [{"mode": "sustain", "model": "haiku", "batch_size": 10, "workers": 3, "calls": 50,
                        "sent": 500, "ok": 480, "valid_rate": 0.96, "artifacts_per_hr": 1200.0, "errors": 1,
                        "wall_s": 1800.0, "stopped_reason": "time"}]},
    "embed": {"results": [{"model": "BAAI/bge-small-en-v1.5", "device": "mps", "batch_size": 128,
                           "text": "short", "n": 2000, "texts_per_s": 850.0}]},
}


def test_render_rows():
    out = render(M, "2026-10-01")
    assert out.startswith("### 9.1 M0 measured targets (measured 2026-10-01)")
    assert "| `filename:CLAUDE.md` | 790,528 | 1,650 | 820 | 2 | 1,400 | 8,100 | 16.3 |" in out
    assert "| 50 | 10 | 1 | 2.50 | 4.00 | 0.10 | 72,000 |" in out
    assert "| skill | 1,000 | 0.400 | 0.350 | 0.250 | 0.300 | 0.320 | 129,946 |" in out
    assert "| sustain | haiku | 10 | 3 | 50 | 0.960 | 1,200 | time |" in out
    assert "| BAAI/bge-small-en-v1.5 | mps | 128 | short | 850 |" in out
    assert "600,000 projected clusters at 1,200 artifacts/hr: 500 hours" in out


def test_render_marks_missing_sections():
    assert render({}, "2026-10-01").count(NOT_MEASURED) == 5


def test_render_partial_lattice_seed_and_search_rate():
    m = {"lattice": [{"seed": "filename:CLAUDE.md", "root_total": 790528, "nodes": 341, "n_leaves": 150,
                      "n_overflows": 6, "unreachable": 39090, "fetch_requests": 1200, "projected_hours": 13.4,
                      "partial": True, "covered": 147810, "projected_nodes": 1824,
                      "projected_fetch_requests": 6200}],
         "search_rate": {"req_per_min": 2.5, "window_s": 600}}
    out = render(m, "2026-10-01")
    assert ("| `filename:CLAUDE.md` (partial: 19% of files walked) | 790,528 | 341 (projected 1,824) | 150 | 6 "
            "| 39,090 | projected 6,200 | 13.4 |") in out
    assert "Measured effective rate under GitHub secondary limits: 2.5 successful requests/min" in out


def test_render_lists_seeds_not_reached():
    out = render({"lattice": M["lattice"], "lattice_missing": ["path:.claude", "path:.claude-plugin"]}, "d")
    assert "Not reached yet: `path:.claude`, `path:.claude-plugin`." in out
