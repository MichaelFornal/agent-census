"""A deterministic stand-in for claude -p, for fixtures and CI. It answers the two schemas the
pipeline uses: extraction records and cluster labels."""
import re
from collections import Counter

from pipeline.llm.client import CallResult

USE_CASE_RE = re.compile(r"^Uses Claude to .+$", re.M)
ITEM_RE = re.compile(r'<artifact-([0-9a-f]+) id="([^"]+)">\n(.*?)\n</artifact-\1>', re.S)
STOP = {"claude", "uses", "about", "their", "which", "there"}


class FakeLLM:
    def call(self, model: str, system: str, prompt: str, schema: dict) -> CallResult:
        props = schema.get("properties", {})
        if "records" in props:
            records = []
            for _nonce, aid, text in ITEM_RE.findall(prompt):
                m = USE_CASE_RE.search(text)
                first = next((line.strip() for line in text.splitlines() if line.strip()), "")
                use_case = m.group(0) if m else first[:120]
                records.append({"id": aid, "use_case": use_case, "domain_guess": "fixture",
                                "non_coding": "novel" in use_case.lower(),
                                "techniques_described": [{"name": "stated purpose", "evidence_quote": use_case[:200]}]
                                if use_case else [],
                                "notable": None})
            return CallResult({"records": records}, None, 0.0, 0.0)
        if "label" in props:
            words = [w for w in re.findall(r"[a-z]+", prompt.lower()) if len(w) >= 5 and w not in STOP]
            top = Counter(words).most_common(1)
            return CallResult({"label": f"Fixture {top[0][0] if top else 'misc'}",
                               "non_coding": "novel" in prompt.lower()}, None, 0.0, 0.0)
        raise ValueError(f"FakeLLM: unknown schema {sorted(props)}")
