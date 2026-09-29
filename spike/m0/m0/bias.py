"""How far the size-window sample over-represents small files (a bias on the S4 ratios). Throwaway code.

Run (after m0.lattice --replay has written lattice_claude_md[_partial].json):
    uv run python -m m0.bias
"""
from m0.paths import data_path, read_jsonl, read_metrics, write_metrics

THRESHOLD = 1000


def sample_share(sizes: list[int], threshold: int) -> float:
    return sum(s <= threshold for s in sizes) / len(sizes)


def population_share(leaves: list, overflows: list, root_total: int, threshold: int) -> float:
    """Files in lattice windows lying wholly at or below threshold, over the seed's total."""
    n = sum(t for lo, hi, t in leaves if hi <= threshold) + sum(t for size, t in overflows if size <= threshold)
    return n / root_total


def main() -> None:
    from m0.lattice import SEEDS
    from m0.gh import SearchClient, github_token

    client = SearchClient(github_token(), data_path("search_cache.jsonl"))

    def cached(q: str) -> tuple[int, bool]:
        body = client.cache[f"{q}|1|1"]
        return body["total_count"], body["incomplete_results"]

    from m0.lattice import LatticeResult, walk
    res = LatticeResult(seed=SEEDS["claude_md"])
    try:
        walk(SEEDS["claude_md"], cached, res=res)
    except KeyError:
        pass  # partial walk: population share counts only windows already walked (a lower bound)
    sizes = [f["size"] for f in read_jsonl(data_path("files.jsonl")) if f["kind"] == "claude_md" and not f["missing"]]
    d = {"kind": "claude_md", "threshold_bytes": THRESHOLD,
         "sample_share": sample_share(sizes, THRESHOLD),
         "population_share_lower_bound": population_share(res.leaves, res.overflows, res.root_total, THRESHOLD)}
    write_metrics("sample_bias", d)
    print(d)


if __name__ == "__main__":
    main()
