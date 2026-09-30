# M1 live slice run (measured 2026-09-30)

Edition `m1-slice`, frozen 2026-09-30, edition hash `a7300552835cbfd3b102f1a0dd65dab5336b48d779c5e6c3ebb7c4eca4e1f930`.
One GitHub token, the Claude Max plan, the M1 pipeline on branch `m1`. All times UTC.

## S1 discover

- Wall: 02:07:22 → 02:36:11 (28.8 min).
- 10 units (leaves), 0 floor overflows, 0 `incomplete_results`.
- Code search: 129 HTTP requests, 54 rate-limited, so 75 successful in 28.8 min: **2.6 successful requests/min** (M0: 2.3).
- 3,167 hits, **1,975 distinct repos**. By seed: claude_md 556, claude_dir 429, mcp 556, plugin 449.
- Pre-run probe of the fork qualifier (11 requests, 2 rate-limited):
  - `filename:.mcp.json size:100..120` returned 6,960. The same query with `fork:true` returned 976, and 100 of 100 items on the page were forks. `fork:only` gave the same result, and `fork:false` returned HTTP 422.
  - **`fork:true` returns forks only**, and a plain query returns non-forks only. S1 was changed to walk plain and `fork:only` families (commit 0cd37f3). The slice ran the non-fork families only.
  - `path:/` partitions a floor window: `filename:CLAUDE.md size:1000..1000` gave 83, and 56 with `path:/`.

## S2 harvest (with kill -9)

- First run 02:36:33, killed with `kill -9` at 02:41:11 after 3 journal lines (75 repos). Resumed 02:41:28 → 04:36:54 (115.4 min).
- Resume: ran 925, skipped 75 of 1,000 units. No duplicates: `repos` has 1,000 rows and 1,000 distinct repos; `harness_files` has 48,692 rows and 48,692 distinct (repo, path).
- GraphQL (resumed run): 790 posts, 2 rate-limited, **198 server errors (25% of posts)**, 2,992 s total request latency (3.8 s per post). Wall time was dominated by 5xx backoff and batch splits at batch size 25 with the depth-4 tree.
- Missing repos: **26 of 1,000 (2.6%)**. 24 were `graphql_502`, 1 `graphql_504` and 1 `graphql_200`. None were `not_found`; the repos had just been discovered. These are single-repo timeouts, not deleted repos.
- Tree truncation at TREE_DEPTH 4: 2,186 cut subtrees across 156 repos (15.6%).
- Harness files (rows / fetched):

  | Kind | Rows | Fetched |
  |---|---|---|
  | skill_file | 17,188 | 0 |
  | other_claude | 16,370 | 0 |
  | skill | 4,840 | 4,831 |
  | command | 3,624 | 3,620 |
  | agent | 2,926 | 2,923 |
  | hook | 2,065 | 2,014 |
  | claude_md | 773 | 769 |
  | settings | 249 | 249 |
  | marketplace | 243 | 243 |
  | mcp | 242 | 242 |
  | settings_local | 82 | 81 |
  | plugin | 70 | 70 |

  Skip reasons: not_fetched_kind 33,578; binary 49; too_large 23. Distinct fetched blobs: 12,456.
- Redactions, by distinct (blob, rule), as blobs / matches:

  | Rule | Blobs | Matches |
  |---|---|---|
  | assigned_secret | 355 | 669 |
  | curl_user_password | 100 | 217 |
  | url_credentials | 52 | 79 |
  | cli_flag_secret | 33 | 74 |
  | openai_key | 4 | 16 |
  | jwt | 6 | 16 |
  | mysql_password | 6 | 13 |
  | bearer | 9 | 11 |
  | aws_access_key | 3 | 8 |
  | anthropic_key | 1 | 5 |
  | github_token | 2 | 2 |
  | slack_token | 2 | 2 |
  | stripe_key | 1 | 1 |

## S3 parse, S4 dedup, S5 features

- Wall for all three: 04:37:59 → 04:49:27 (11.5 min).
- S3: 15,042 artifacts. Error classes: frontmatter_invalid 363, empty 33, no_mcp_servers 6, json_invalid 4.
- S4: 11,026 clusters from 15,042 artifacts, a **distinct ratio of 0.733** (M0's single-file sample gave about 0.97; whole-harness harvesting sees far more vendoring). By kind:

  | Kind | Distinct ratio |
  |---|---|
  | skill | 0.765 |
  | command | 0.581 |
  | agent | 0.798 |
  | hook | 0.691 |
  | claude_md | 0.900 |
  | settings | 0.859 |
  | marketplace | 0.934 |
  | mcp | 0.913 |
  | settings_local | 0.988 |
  | plugin | 1.000 |

  Skill family keys: 3,339 against 3,696 skill clusters. Mutations: verbatim 2,339, trimmed 717, rewritten 628, extended 330, retargeted 2. Upstream-library matches: 0.
- S5: 905 harnesses with artifacts, 6,391 features. Harnesses per technique:

  | Technique | Harnesses |
  |---|---|
  | mcp_composition | 173 |
  | agent_restricted_tools | 135 |
  | skill_scripts | 132 |
  | agent_model_routing | 127 |
  | skill_references | 122 |
  | command_arguments | 109 |
  | hook_pretooluse_guard | 105 |
  | hook_sessionstart_context | 97 |
  | claude_md_imports | 95 |
  | claude_md_nested | 92 |
  | plugin_manifest | 70 |
  | permissions_deny | 68 |
  | hook_userpromptsubmit | 54 |
  | hook_notification | 15 |
  | permissions_sandbox | 9 |
  | hook_stop_gate | 2 |
  | permissions_bypass | 2 |
  | hook_posttooluse_formatter | 2 |

  `hook_stop_gate` and `hook_posttooluse_formatter` are probably under-counted. Hooks that call a script, which then runs the tests or formatter, don't match the token rules. This is input for the M3 detector audit.

## S6 tier-2 extraction, pass a (with kill -9)

- Input: 8,823 text clusters (claude_md, skill, agent, command).
- First run 04:49:30, killed with `kill -9` at 04:51:52 after 2 journal lines (2 batches of 60 units). One in-flight `claude -p` child survived the kill and ran to completion; its result was discarded.
- Resume 1 ran 04:52 → about 06:52. It was stopped by the session's 2-hour background-task limit, not by the pipeline, at 8,640 of 8,823. Resume 2 finished the last 183 units, 06:53:16 → 06:56:01.
- No duplicates: 8,823 rows and 8,823 distinct (cluster_id, pass_id) across semantics and rejects.
- Sonnet, 20 artifacts per call, 3 workers:
  - 444 calls; 8,843 artifacts sent (including re-sends after splits); 8,624 valid.
  - **Valid rate 0.975** (M0: 0.90).
  - API-equivalent cost: $92.27.
  - Rejects: quote_not_verbatim 179, missing 20. 1 call error.
  - **About 4,070 valid artifacts per hour** of wall time, including the kill and resumes.
  - **No plan limit hit.**
- 1,390 of 8,624 use cases were marked non-coding.

## S7 taxonomy

- Wall: 06:56:28 → 07:00:44 (4.3 min, bge-small on CPU plus label calls).
- The taxonomy is **degenerate**:
  - Level 1 has 3 use cases, sized 8,208, 135 and 118. The largest is "Software Engineering Workflow Skills".
  - Level 2 has 4 use cases with 220 members.
  - `uc_membership` has 8,461 rows. There are 50 Uncharted rows and 2 technique clusters, neither of them a candidate.
- Throwaway probe on the same embeddings (UMAP 5-d):

  | min_cluster_size | Selection | Clusters | Largest | Noise |
  |---|---|---|---|---|
  | 86 | eom | 3 | 8,170 | 202 |
  | 86 | leaf | 12 | 447 | 5,537 |
  | 30 | eom | 7 | 8,179 | 49 |
  | 30 | leaf | 49 | 496 | 5,010 |
  | 15 | leaf | 107 | 227 | 4,647 |

  EOM always collapses to one domain. Leaf selection gives granular domains but leaves 55–64% of points as noise.
- Approved automatically (Michael's decision), limited to digit-free level-1 drafts, which left two:
  - `uc-e1a6fda600` "Software Engineering Workflow Skills"
  - `uc-4f734de77d` "Unity MCP CLI tool docs"

  `uc-e87b4b4352` "1C Enterprise and AL tooling skills" was excluded because its label contains a digit.
- Top 10 Uncharted, as rank / nearest-neighbour distance / use case:
  1. 0.349. The file contains only the Anthropic refusal-test magic string repeated, so it has no real instructions.
  2. 0.334. Documents that the workflow-migrate skill is never invoked directly and only houses migration scripts.
  3. 0.274. A pointer that includes the ergonomist agent definition from another directory.
  4. 0.270. Operate an environment-platform CLI to query and manage deployment environments.
  5. 0.258. Query and operate an entity platform (storages, entities, sync channels, schema changes).
  6. 0.258. Compose a minimal AI-DLC workflow by estimating an autonomy risk score.
  7. 0.248. Operate an alarm-rule CLI to list, create, update and delete alarm rules across regions.
  8. 0.246. Query service logs by keyword, log ID or error aggregation through an internal CLI.
  9. 0.236. Review and deploy configuration to pre-production environments through an internal CLI.
  10. 0.232. Manage custom dashboards through an internal CLI.
- Technique clusters, as size / label / nearest technique / similarity:
  - 33,211 / "Tool restriction" / skill_references / 0.783.
  - 966 / "Persona" / skill_references / 0.670.

  Neither is below the 0.6 candidate threshold.

## S8 facts and S9 site data

- Frozen at 07:03:06. `census facts`, then `census facts --check`, both passed. S9 wrote 21 pages of data.
- Facts:
  - harnesses_total 974
  - harnesses_without_forks 969 (5 templates or forks came back from non-fork search)
  - artifacts_total 15,042
  - distinct_artifacts 11,026
  - copy_rate 0.267
- Component share of harnesses:

  | Component | Share |
  |---|---|
  | claude_md | 0.707 |
  | skill | 0.311 |
  | settings | 0.256 |
  | marketplace | 0.249 |
  | mcp | 0.248 |
  | agent | 0.207 |
  | command | 0.178 |
  | hook | 0.128 |
  | settings_local | 0.083 |
  | plugin | 0.072 |
- Site: the digits check passed and `astro build` produced 21 pages (index, 18 techniques, 2 use cases).
- Checked by eye against facts.json:
  - The index shows 974, 11,026 and 26.7%.
  - The PreToolUse page shows 10.8% (105).
  - The deny-rules page shows only anonymized cards.
- Findings for M5:
  - The use-case pages are uninformative, because of the degenerate taxonomy.
  - Evidence excerpts render raw markdown.

## Inputs for M2

- **Code search:** 2.6 successful requests/min sustained over 29 minutes, with 42% of requests rate-limited. Every seed has to be walked twice, as plain and `fork:only` families.
- **GraphQL harvest:**
  - At batch size 25 with a depth-4 tree, 25% of requests returned 5xx. There were 3.8 s of request latency per post, but 115 minutes of wall time for 925 repos (**about 480 repos/hr**), dominated by backoff after server errors.
  - 15.6% of repos had subtrees cut at depth 4.
  - 2.6% of repos failed as single-repo timeouts.
- **Harness shape:** 15.5 parsed artifacts per harness (15,042 / 974). 12,456 distinct fetched blobs per 1,000 repos.
- **Dedup:** distinct ratio 0.733 on whole harnesses. By kind: skill 0.765, command 0.581, agent 0.798, hook 0.691, claude_md 0.900.
- **S6:** 4,070 valid artifacts/hr at 3 workers, valid rate 0.975, $0.0107 API-equivalent per valid artifact, and no Max-plan limit hit in about 2 hours of continuous calls.
- **S7:** HDBSCAN EOM collapses these use-case embeddings into one domain; see the probe table above.
