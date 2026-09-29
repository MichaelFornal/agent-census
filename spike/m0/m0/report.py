"""Render data/m0/metrics/*.json as PRD §9.1. Throwaway code.

Run: uv run python -m m0.report > ../../data/m0/targets.md
"""
import datetime as dt
from typing import Any

from m0.lattice import SEEDS
from m0.paths import read_metrics

NOT_MEASURED = "_Not measured yet._"


def _n(x: Any, nd: int = 1) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, int) and not isinstance(x, bool):
        return f"{x:,}"
    return f"{x:,.{nd}f}"


def _ratio(c: int, n: int) -> str:
    return _n(c / n, 3) if n else "n/a"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return (["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
            + ["| " + " | ".join(r) + " |" for r in rows])


def _lattice_row(x: dict) -> list[str]:
    if x.get("partial"):
        pct = round(100 * x["covered"] / x["root_total"])
        return [f"`{x['seed']}` (partial: {pct}% of files walked)", _n(x["root_total"]),
                f"{_n(x['nodes'])} (projected {_n(x['projected_nodes'])})", _n(x["n_leaves"]),
                _n(x["n_overflows"]), _n(x["unreachable"]), f"projected {_n(x['projected_fetch_requests'])}",
                _n(x["projected_hours"])]
    return [f"`{x['seed']}`", _n(x["root_total"]), _n(x["nodes"]), _n(x["n_leaves"]), _n(x["n_overflows"]),
            _n(x["unreachable"]), _n(x["fetch_requests"]), _n(x["projected_hours"])]


def render(m: dict[str, Any], measured_on: str) -> str:
    out = [f"### 9.1 M0 measured targets (measured {measured_on})", "",
           "Produced by the throwaway `spike/m0` scripts. Raw metrics: `docs/m0/`.", ""]

    out += ["**S1: code-search lattice** (paced at 10 req/min)", ""]
    lat = m.get("lattice") or []
    if lat:
        out += _table(["Seed", "Root total", "Lattice requests", "Leaves", "Floor overflows", "Files unreachable",
                       "Full-fetch requests", "Hours at 10 req/min"],
                      [_lattice_row(x) for x in lat])
    else:
        out.append(NOT_MEASURED)
    if m.get("lattice_missing"):
        out += ["", "Not reached yet: " + ", ".join(f"`{q}`" for q in m["lattice_missing"]) + "."]
    rate = m.get("search_rate")
    if rate:
        out += ["", f"Measured effective rate under GitHub secondary limits: {_n(rate['req_per_min'])} "
                    f"successful requests/min (over {_n(rate['window_s'])} s); hours above assume 10/min."]
    out.append("")

    out += ["**S2: GraphQL harvest**", ""]
    g = m.get("graphql")
    if g:
        out += _table(["Batch size (repos)", "Batches", "Median cost (points)", "Median latency (s)",
                       "p90 latency (s)", "Retry rate", "Repos/hr (latency bound)"],
                      [[k, _n(v["batches"]), _n(v["median_cost"]), _n(v["median_latency_s"], 2),
                        _n(v["p90_latency_s"], 2), _n(v["retry_rate"], 2), _n(v["repos_per_hour_latency_bound"], 0)]
                       for k, v in sorted(g["by_batch_size"].items(), key=lambda kv: int(kv[0]))])
        out += ["", f"Points per repo: {_n(g['points_per_repo'], 3)}. Repos/hr under the hourly points budget: "
                    f"{_n(g['repos_per_hour_points_bound'], 0)}. Files fetched: {_n(g['files'])} (missing "
                    f"{_n(g['files_missing'])}, binary {_n(g['files_binary'])}, truncated "
                    f"{_n(g['files_truncated'])}). Secrets redacted: {_n(g['secrets_redacted'])}."]
    else:
        out.append(NOT_MEASURED)
    out.append("")

    out += ["**S4: distinct-cluster ratio** (distinct / files, 5k sample)", ""]
    d = m.get("dedup")
    if d:
        out += _table(["Kind", "Files", "Exact", "Normalized", "MinHash 0.7", "MinHash 0.8", "MinHash 0.9",
                       "Projected distinct at 0.8 (upper bound)"],
                      [[kind, _n(v["n"]), _ratio(v["exact"], v["n"]), _ratio(v["normalized"], v["n"]),
                        _ratio(v["minhash"]["0.7"], v["n"]), _ratio(v["minhash"]["0.8"], v["n"]),
                        _ratio(v["minhash"]["0.9"], v["n"]), _n(v["projected_distinct_0.8"])]
                       for kind, v in d["kinds"].items()])
        rare = ", ".join(f"n={k}: {_n(v, 3)}" for k, v in d["rarefaction"].items())
        out += ["", f"Ratio by sample size (MinHash 0.8, all kinds): {rare}. Projected distinct clusters, "
                    f"all kinds: {_n(d['projected_distinct_total_0.8'])}."]
    else:
        out.append(NOT_MEASURED)
    out.append("")

    out += ["**S6: `claude -p` tier-2 throughput**", ""]
    llm = m.get("llm")
    if llm and llm.get("groups"):
        out += _table(["Mode", "Model", "Batch", "Workers", "Calls", "Valid rate", "Artifacts/hr", "Stopped"],
                      [[x["mode"], x["model"], _n(x["batch_size"]), _n(x["workers"]), _n(x["calls"]),
                        _n(x["valid_rate"], 3), _n(x["artifacts_per_hr"], 0), x.get("stopped_reason") or ""]
                       for x in llm["groups"]])
    else:
        out.append(NOT_MEASURED)
    out.append("")

    out += ["**S7: embeddings**", ""]
    e = m.get("embed")
    if e and e.get("results"):
        out += _table(["Model", "Device", "Batch", "Text", "Texts/s"],
                      [[x["model"], x["device"], _n(x["batch_size"]), x["text"], _n(x["texts_per_s"], 0)]
                       for x in e["results"]])
    else:
        out.append(NOT_MEASURED)
    out.append("")

    best = max((x["artifacts_per_hr"] for x in (llm or {}).get("groups", [])
                if x["mode"] == "sustain" and x["artifacts_per_hr"]), default=None)
    if d and best:
        total = d["projected_distinct_total_0.8"]
        out += [f"**Derived.** One tier-2 pass over {_n(total)} projected clusters at {_n(best, 0)} "
                f"artifacts/hr: {_n(total / best, 0)} hours. Two passes: {_n(2 * total / best, 0)} hours.", ""]
    return "\n".join(out)


def load() -> dict[str, Any]:
    return {
        "lattice": [x for k in SEEDS
                    if (x := read_metrics(f"lattice_{k}") or read_metrics(f"lattice_{k}_partial"))],
        "lattice_missing": [q for k, q in SEEDS.items()
                            if not (read_metrics(f"lattice_{k}") or read_metrics(f"lattice_{k}_partial"))],
        "search_rate": read_metrics("search_rate"),
        "graphql": read_metrics("graphql"),
        "dedup": read_metrics("dedup"),
        "llm": read_metrics("llm"),
        "embed": read_metrics("embed"),
    }


if __name__ == "__main__":
    print(render(load(), dt.date.today().isoformat()))
