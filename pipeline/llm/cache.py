"""LLM selection and a JSONL answer cache (so a killed label pass resumes without re-asking)."""
from pathlib import Path

from pipeline.journal import unit_key
from pipeline.jsonl import append_jsonl, read_jsonl
from pipeline.llm.client import CallResult, ClaudeCLI
from pipeline.llm.fake import FakeLLM


def make_llm(name: str):
    return {"claude": ClaudeCLI, "fake": FakeLLM}[name]()


class CachedLLM:
    def __init__(self, inner, path: Path) -> None:
        self.inner = inner
        self.path = path
        self.cache = {r["key"]: r["data"] for r in read_jsonl(path)}

    def call(self, model: str, system: str, prompt: str, schema: dict) -> CallResult:
        key = unit_key(model, system, prompt, schema)
        if key in self.cache:
            return CallResult(self.cache[key], None, 0.0, 0.0)
        res = self.inner.call(model, system, prompt, schema)
        if res.data is not None:
            self.cache[key] = res.data
            append_jsonl(self.path, {"key": key, "data": res.data})
        return res
