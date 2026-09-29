"""Embeddings/sec for S7 candidates on MPS and CPU (PRD §4, §8). Throwaway code.

Run: uv run python -m m0.embed
"""
import sys
import time

from m0.paths import blob_path, data_path, read_jsonl, write_metrics

MODELS = [("BAAI/bge-small-en-v1.5", ""), ("nomic-ai/nomic-embed-text-v1.5", "clustering: ")]
BATCH_SIZES = [32, 128]
N = 2000


def _cycle(xs: list[str]) -> list[str]:
    return (xs * (N // len(xs) + 1))[:N]


def texts() -> dict[str, list[str]]:
    heads = []
    for r in read_jsonl(data_path("representatives.jsonl")):
        p = blob_path(r["oid"])
        if p.exists() and p.read_text().strip():
            heads.append(p.read_text())
    short = [r["use_case"] for r in read_jsonl(data_path("llm_records.jsonl"))]
    if len(short) < 200:
        short = [h.strip().splitlines()[0][:200] for h in heads]
    return {"short": _cycle(short), "long": _cycle([h[:2000] for h in heads])}


def main() -> None:
    import torch
    from sentence_transformers import SentenceTransformer

    devices = ["mps", "cpu"] if torch.backends.mps.is_available() else ["cpu"]
    corpus = texts()
    results = []
    for name, prefix in MODELS:
        for device in devices:
            model = SentenceTransformer(name, device=device, trust_remote_code=True)
            model.max_seq_length = 512
            for kind, xs in corpus.items():
                xs = [prefix + x for x in xs]
                for bs in BATCH_SIZES:
                    model.encode(xs[:bs], batch_size=bs, show_progress_bar=False)  # warm-up
                    t0 = time.perf_counter()
                    model.encode(xs, batch_size=bs, show_progress_bar=False)
                    dt = time.perf_counter() - t0
                    row = {"model": name, "device": device, "batch_size": bs, "text": kind, "n": len(xs),
                           "texts_per_s": len(xs) / dt}
                    results.append(row)
                    print(row, file=sys.stderr, flush=True)
    write_metrics("embed", {"torch": torch.__version__, "results": results})


if __name__ == "__main__":
    main()
