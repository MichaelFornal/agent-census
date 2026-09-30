"""The typed tables every stage reads and writes (PRD §4)."""
import pyarrow as pa

S, I, F, B = pa.string(), pa.int64(), pa.float64(), pa.bool_()


def _s(**cols: pa.DataType) -> pa.Schema:
    return pa.schema(list(cols.items()))


SCHEMAS: dict[str, pa.Schema] = {
    "repo_hits": _s(repo=S, path=S, component=S, query_id=S, blob_sha=S, is_fork=B),
    "s1_overflows": _s(seed=S, query=S, total=I, reachable=I),
    "repos": _s(repo=S, missing=B, error=S, stars=I, is_fork=B, is_template=B, created_at=S, pushed_at=S,
                language=S, license=S, head_oid=S, tree_truncated=I, canary=B),
    "harness_files": _s(repo=S, path=S, kind=S, blob_sha=S, size=I, fetched=B, skip_reason=S),
    "redactions": _s(blob_sha=S, rule=S, n=I),
    "artifacts": _s(artifact_id=S, repo=S, kind=S, path=S, blob_sha=S, parsed_json=S, error_class=S),
    "clusters": _s(cluster_id=S, kind=S, canonical_artifact=S, size=I),
    "membership": _s(artifact_id=S, cluster_id=S, tier=S, family_key=S),
    "lineage": _s(cluster_id=S, origin_repo=S, origin_basis=S, upstream_lib=S),
    "mutations": _s(artifact_id=S, cluster_id=S, mutation_class=S),
    "features": _s(repo=S, technique_id=S, artifact_id=S, path=S, start_line=I, end_line=I),
    "glyphs": _s(repo=S, claude_md_log_bytes=F, n_skills=I, n_agents=I, n_commands=I, n_hooks=I,
                 permission_breadth=F, n_mcp_servers=I),
    "semantics": _s(cluster_id=S, pass_id=S, artifact_id=S, use_case=S, domain_guess=S, non_coding=B,
                    techniques_json=S, notable=S),
    "semantics_rejects": _s(cluster_id=S, pass_id=S, reason=S),
    "llm_calls": _s(call_id=S, pass_id=S, model=S, n_sent=I, n_ok=I, error=S, wall_s=F, cost_usd=F),
    "use_cases": _s(use_case_id=S, parent_id=S, level=I, label=S, non_coding=B, size=I),
    "uc_membership": _s(cluster_id=S, use_case_id=S),
    "technique_candidates": _s(candidate_id=S, label=S, size=I, nearest_technique=S, similarity=F,
                               is_candidate=B, examples_json=S),
    "uncharted": _s(cluster_id=S, use_case=S, nn_distance=F, rank=I),
}
