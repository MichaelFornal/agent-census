"""Effective code-search rate while a lattice runs: successful (cached) requests per minute. Throwaway code.

Run alongside a lattice: uv run python -m m0.rate --minutes 30 --log ../../data/m0/lattice_full.log
"""
import argparse
import re
import time
from pathlib import Path

from m0.paths import data_path, write_metrics


def last_rate_limited(log_text: str) -> int:
    hits = re.findall(r"'rate_limited': (\d+)", log_text)
    return int(hits[-1]) if hits else 0


def rate_between(a: dict, b: dict) -> dict:
    window = b["t"] - a["t"]
    ok = b["cache_lines"] - a["cache_lines"]
    return {"window_s": window, "successful_requests": ok, "req_per_min": ok / (window / 60),
            "rate_limited": b["rate_limited"] - a["rate_limited"]}


def snapshot(log: Path) -> dict:
    cache = data_path("search_cache.jsonl")
    return {"t": time.time(), "cache_lines": sum(1 for _ in cache.open()),
            "rate_limited": last_rate_limited(log.read_text())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--log", type=Path, required=True)
    args = ap.parse_args()
    a = snapshot(args.log)
    time.sleep(args.minutes * 60)
    d = rate_between(a, snapshot(args.log))
    write_metrics("search_rate", d)
    print(d)


if __name__ == "__main__":
    main()
