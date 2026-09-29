# Agent Census — Fall 2026 Edition

## 1. Purpose and decisions

**Primary purpose: a hiring showcase.** A hiring manager at a seed–Series B AI startup spends two
minutes on it and wants to talk to the builder. It should prove three things:
1. **Data engineering at scale.** Enumerate a rate-limited, capped source completely; dedupe
   hundreds of thousands of copies; run resumable multi-day pipelines.
2. **Product sense (FDE).** Turn a messy corpus into something a non-engineer can explore and
   learn from.
3. **Deep agent-tooling fluency.** Know the Claude Code harness (hooks, skills, subagents,
   permissions, MCP, plugins) well enough to catalog how the world uses it.

| Decision | Value |
|---|---|
| Subject | **Claude Code only.** The unit of analysis is a repo's **harness**: `CLAUDE.md` (all), `.claude/**` (settings, settings.local, hooks, skills, agents, commands), `.mcp.json`, `.claude-plugin/` |
| Centerpiece | An explorable atlas plus a findings report. It shows how people build their Claude setups differently, backs standout findings and outliers with visual proof, and catalogs techniques and skill-library use cases |
| Organizing axis | **By use case** (what Claude is being made to do), with techniques inside each one, and an **Uncharted** section for unique use cases that fit no category |
| Not in scope | Ranking or scoring "best" files. Paste-your-own-setup (not in v1). Cross-tool comparison. Live refresh |
| Cadence | **Dated editions** ("Fall 2026"), frozen and fully verified; the pipeline can be rerun for later editions |
| Acquisition | **Full enumeration** with two-tier analysis (deterministic over everything, LLM over every distinct artifact) |
| Hosting | Own subdomain `census.forn.al` (own Vercel project, JS allowed); forn.al links to it |
| Budget | $0 in cash. Claude Code Max plan (`claude -p`), free APIs, local models. No time cap: quality sets the scope |
| Validation | Fully machine-run (no human gold set), labelled as such. Michael does editorial review only (taxonomy labels, candidate techniques, the Uncharted list) |

## 2. Corpus facts (measured 2026-09-28, GitHub code search `total_count`, quantized to 1024)

| Component | Query | Files |
|---|---|---|
| CLAUDE.md | `filename:CLAUDE.md` | 790,528 |
| Skills | `path:.claude/skills filename:SKILL.md` | 433,152 |
| Subagents | `path:.claude/agents extension:md` | 238,080 |
| Commands | `path:.claude/commands extension:md` | 216,064 |
| Hooks files | `path:.claude/hooks` | 87,552 |
| settings.local.json | `path:.claude filename:settings.local.json` | 85,504 |
| settings.json | `path:.claude filename:settings.json` | 65,408 |
| .mcp.json | `filename:.mcp.json` | 64,896 |
| Plugin manifests | `path:.claude-plugin filename:plugin.json` | 25,408 |

These are seeds, not published facts. Every published number is re-derived by a named query (§7).
`total_count` is approximate: M0 found leaf counts over disjoint size windows summing to 5.8% more
than the root count, so no stage may treat it as exact.

Heavy vendoring is expected (433k SKILL.md files ≠ 433k skills), so dedup and lineage are central
to the analysis, not cleanup. M0 showed that the vendoring holds **by name, not by text**: about
97% of sampled files are textually distinct at MinHash 0.8, and 100 copies of one well-known skill
formed 74 text clusters. Lineage therefore needs a name/family key as well as text similarity (S4),
and tier-2 cost cannot count on text dedup shrinking the corpus.

## 3. Product: the site's surfaces

1. **Findings (landing page).** A headline strip (harnesses counted, distinct artifacts after
   dedup, copy rate), then 6–8 findings. Each finding has a chart and at least one **evidence
   card**: a real excerpt, the repo, and a permalink to the exact commit and line range. The
   finding slots are fixed in advance; which finding fills each slot is chosen after the data
   comes in:

   | Slot | Built from |
   |---|---|
   | Adoption & anatomy | S1/S2 counts, glyph distributions |
   | Most-used techniques | S5 + S6 prevalence |
   | Skill lineage (who copied whom, what they change) | S4 |
   | What runs without asking | settings allow/deny/ask, **aggregate only** |
   | Archetypes of setup | clusters of glyph vectors |
   | Uncharted highlights | S7 |
2. **Use-case atlas (front door 1).** A two-level emergent taxonomy: about 15–30 domains, each
   with subtypes. Non-coding use cases are tagged explicitly. Each use-case page shows prevalence,
   the techniques used for it, example harnesses (as glyphs plus evidence cards) and
   representative skills.
3. **Technique catalog (front door 2).** Each technique page has a definition, **how it was
   detected** (a deterministic detector or LLM-judged, stated openly), prevalence, variants, a
   "how it's wired" excerpt (e.g. the hook JSON plus the script it calls), the use cases that use
   it, and its measured validation score.
4. **Matrix (hero visual).** A use-case × technique heatmap; every cell links to its evidence.
5. **Uncharted.** One-off use cases. Each card shows what it does, why it is unique (nearest
   neighbor and distance), an excerpt and a link.
6. **How people build differently.** Every harness is a **fingerprint glyph**: a radial with fixed
   spoke order for CLAUDE.md size (log), the number of skills, agents, commands and hooks, how
   broad the permissions are, and the number of MCP servers. There are archetype small multiples
   and a zoomable 2-D map (UMAP of glyph vectors) of all harnesses.
7. **Skill libraries.** The most-vendored libraries, their family trees, and mutation analysis:
   "people copy X, usually delete Y, add Z".
8. **Methodology.** The pipeline, coverage limits, dedup rates, validation scores, the query
   behind every number, the edition hash, and the removal-request path.

**Attribution policy.**
- Evidence cards for techniques and use cases are named and linked (neutral showcase).
- Permissions and any risky or embarrassing content appear only in aggregate or anonymized, never
  as a named call-out.
- Secrets are redacted before storage.
- Every page links to a removal request (a GitHub issue template). A removed repo is excluded from
  the next build.
- Raw file contents are never redistributed in bulk. The public data release is derived features,
  cluster IDs and short excerpts.

## 4. Pipeline architecture

Nine stages, each its own module, each taking a typed table in and giving a typed table out. Every
stage is **idempotent and resumable**: work units are keyed by an input hash, and a per-stage
journal records completion. `kill -9` at any point, then rerun, loses nothing and duplicates
nothing.

| # | Stage | Output tables | Mechanics |
|---|---|---|---|
| S1 | discover | `repo_hits(repo, path, component, query_id)` | Code search with an **adaptive partition lattice**: run a query; if `total_count > 1000`, bisect the `size:` byte range; at the size floor, split by path depth or extension, and record whatever still exceeds the cap as unreachable (M0: 39,090 CLAUDE.md files sit in single byte sizes with more than 1,000 hits each, mostly template files). Seed families: `filename:CLAUDE.md`, `path:.claude`, `filename:.mcp.json`, `path:.claude-plugin`, each with `fork:true` (code search leaves forks out by default, and the with/without-forks counts need them). Pace at 10 req/min; on a 429, back off and **decay the penalty after success** (a penalty with no decay is permanent); on a hint-less secondary limit, wait at least 60 s; when `x-ratelimit-remaining` hits 0, sleep until the reset. Secondary limits cut the measured sustained rate to 2.3 req/min, so plan S1 wall time at that rate, not at 10. Search only finds repos; it does not collect every file |
| S2 | harvest | `repos(meta)`, `harness_files(repo, path, blob_sha, size)`, blob store | Batched GraphQL (start at 25 aliased repos per query: M0 saw no retries at 25, 43% at 50 and total failure at 100; shrink on timeout or resource-limit errors). Latency is the limit, not points (one point per batch at every size). Fetches: the `HEAD:.claude` tree (recursive via nested entries), root CLAUDE.md, nested CLAUDE.md paths from S1, `.mcp.json`, and repo metadata (stars, isFork, isTemplate, createdAt, pushedAt, primary language, license, HEAD OID for permalinks). Blob text is **redacted by the secret scan (gitleaks-style rules plus an entropy gate against over-redacting code) before it is written** to a content-addressed zstd store keyed by git blob SHA; a blob already in the store is not fetched again. About 6.4% of indexed repos are gone by harvest time; they are recorded as missing. Fallback: REST git trees plus `raw.githubusercontent.com` |
| S3 | parse | `artifacts(artifact_id, repo, kind, path, blob_sha, parsed_json)` | One parser per kind: CLAUDE.md (sections, `@imports`, command blocks, length); `settings*.json` (permissions allow/deny/ask, hooks by event, env keys, model, sandbox); SKILL.md, agent and command files (frontmatter plus body; skill `scripts/`/`references/` presence); `.mcp.json` servers; plugin manifests. Parsers read only redacted blobs (S2). Malformed files are recorded with an error class, never dropped silently |
| S4 | dedup & lineage | `clusters(cluster_id, kind, canonical_artifact, size)`, `membership`, `lineage(cluster_id, origin_repo, origin_commit, upstream_lib)`, `mutations(artifact_id, class)` | Tier 1: exact blob SHA. Tier 2: normalized hash (whitespace, case, repo/project names templated out). Tier 3: MinHash LSH near-duplicates (0.8 provisionally, from M0; the dedup audit, §6, sets the final threshold). A **family key** (frontmatter `name`, else the skill directory or file stem) groups skills, agents and commands whose text has drifted apart, since text similarity alone misses most edited copies. Origin = earliest commit in the cluster, or a known upstream library. Each copy's mutation class vs. origin: verbatim / trimmed / extended / re-targeted / rewritten. Forks and template repos are flagged; every count is available with and without them |
| S5 | tier-1 features | `features(repo, technique_id, evidence_ref)`, `glyphs(repo, vector)` | Deterministic detectors (§5) over the whole corpus, plus the glyph vector per harness |
| S6 | tier-2 extraction | `semantics(cluster_id, pass_id, json)` | `claude -p --output-format json --json-schema <schema> --tools ""` over **one representative per distinct cluster** (tools disabled because artifact text is untrusted; the schema flag removes the malformed-JSON errors M0 saw). Measured operating point: Sonnet, 20 artifacts per call, 3 parallel workers, about 5,000 valid artifacts/hr; artifacts are truncated to 6,000 characters (29% of M0 representatives were) and the truncation is recorded. Two independent passes plus adjudication (§6) |
| S7 | taxonomy | `use_cases(id, parent, label)`, `uc_membership`, `technique_candidates`, `uncharted` | A local embedding model over use-case statements and free-text technique descriptions, then UMAP and HDBSCAN into two levels; the LLM writes draft labels and Michael approves them. Uncharted candidates = HDBSCAN noise plus clusters of 3 or fewer members, ranked by nearest-neighbor distance, filtered by an LLM check ("genuinely unlike the rest, or just vague?"), then Michael's final pick |
| S8 | facts | `facts.json` | Every published number is a **named SQL query** in `facts/` run over the frozen edition. Each record: `{id, query_file, edition_hash, value, computed_at}` |
| S9 | site data | `site/src/data/*.json` | Per-page slices, evidence cards (excerpt ≤ 25 lines, permalink), glyph vectors, matrix cells, map coordinates |

**Storage.** One DuckDB catalog, Parquet per stage table, and the blob store, all outside git.
`census freeze` writes an edition manifest (hashes of every table plus the blob-store index) and
stamps `edition_hash`.

**Throughput.** M0 measured these instead of guessing them; the numbers are in §9.1 and later plans
use them: search requests the lattice needs per seed; GraphQL points and latency per batch;
distinct-cluster count (it sets the tier-2 cost); `claude -p` artifacts/hour sustainable on the Max
plan; embeddings/sec.

## 5. Analysis contracts

**Detector interface (tier 1).**
`detect(harness) -> list[Evidence(technique_id, artifact_id, locator, confidence=1.0)]`.
Pure functions with fixture tests.

**Seed technique catalog** (each gets a detector where it is visible in structure, otherwise it is
LLM-detected):
- *Hooks:* a Stop-hook verification gate (tests, lint or typecheck before finishing); PreToolUse
  guards (blocking destructive commands, protecting paths); PostToolUse formatters; SessionStart
  context injection; UserPromptSubmit augmentation; notification hooks.
- *Orchestration:* subagents with restricted tool lists; per-agent model routing; planner/executor
  split; parallel reviewer fan-out.
- *Memory and context:* `@import` chains; per-directory CLAUDE.md files; progress or handoff
  files; context-budget rules.
- *Skills:* a skill that wraps a CLI or script (`scripts/`); progressive disclosure
  (`references/`); templates; meta-skills (skills that write skills).
- *Instruction style:* rules that give a rationale; hard prohibitions; a commands table; a
  verification protocol; a persona or role.
- *Permissions:* allowlist breadth, deny rules, bypass modes, sandbox settings.
- *Other:* `$ARGUMENTS` command workflows; MCP server composition; plugins and marketplaces.

**Open technique discovery.** Tier 2 also returns free-text technique descriptions. They are
clustered in S7; any cluster that doesn't map to a catalog entry becomes a `technique_candidate`
for Michael to accept or reject. Accepted candidates join the catalog, with an LLM detector.

**Tier-2 extraction schema (per artifact):**
```json
{"use_case": "one sentence: what Claude is being made to do",
 "domain_guess": "free text",
 "non_coding": true,
 "techniques_described": [{"name": "...", "evidence_quote": "<=200 chars, verbatim"}],
 "notable": "why this is unusual, or null"}
```
`evidence_quote` must be a verbatim substring of the artifact, compared with whitespace collapsed
(M0: models flatten multi-line text, which is not fabrication). The runner checks this and rejects
the record if it fails (an anti-fabrication guard). Quotes spliced together from non-adjacent lines
still fail, and M0 showed those are real fabrications.

## 6. Validation (machine-run, published on the methodology page)

1. **Structural ground truth.** Where a technique is visible both in structure (tier 1) and in
   prose (tier 2), tier-1 is treated as truth and **tier-2 precision and recall are measured
   against it**.
2. **Two independent tier-2 passes.** Different prompts, a shuffled artifact order, and different
   models where the second model clears the validity bar. In M0, Haiku returned valid records for
   only 35–53% of artifacts and ran slower than Sonnet (it spends its output on thinking), so it
   doesn't clear that bar yet. That conclusion rests on only 9 calls, made without `--json-schema`
   or whitespace-normalized quote matching, so M1 measures Haiku again under those fixes. If Haiku
   still falls short, both passes run on Sonnet with different prompts and orders. Cohen's κ is
   published per field; disagreements are adjudicated by a third Opus pass.
3. **Planted canaries.** A few hundred synthetic harnesses with known techniques and use cases go
   in at S3 and pass through S4–S7, measuring end-to-end recall. They are tagged `canary=true` and
   excluded from every fact query (CI asserts this).
4. **LLM audit of detectors.** An LLM judge re-reads a random sample of hits and misses for each
   tier-1 detector to estimate precision and recall.
5. **Dedup audit.** An LLM judge rates sampled cluster pairs, oversampling pairs near the
   threshold. The false-merge and false-split rates set the MinHash threshold and are published.
6. **Publishing rule.** Any technique or use case whose measured agreement or precision is below
   **0.85** ships flagged "estimated ±" with its score, or doesn't ship.

The methodology page says plainly: "Validated by machine agreement and planted canaries, not by
human labels."

## 7. Numbers-provenance contract

- The site can only print numbers from `facts.json`. Hard-coded digits in page copy fail CI.
- `census facts --check` re-runs every query against the frozen edition and fails on any drift.
- Every number carries the edition date. Nothing is presented as live.

## 8. Stack, repo layout, operability

- **Pipeline:** Python 3.12 managed with `uv`; httpx (async); DuckDB and Parquet; `datasketch`
  (MinHash LSH); `sentence-transformers` with `BAAI/bge-small-en-v1.5` (M0: about 270 short texts/s, 4× nomic-embed,
  and MPS gives no gain over CPU for short texts; nomic's remote code also breaks on
  transformers 5); `umap-learn`; `hdbscan`; `claude -p` through a batching runner that
  respects plan limits and retries with decay.
- **CLI:** `census run <stage> [--edition fall-2026] [--limit N]`, `census status` (per-stage
  progress and journal), `census facts [--check]`, `census freeze`, `census canaries`.
- **Site:** Astro (static) with JS islands. Observable Plot for standard charts; D3 for glyphs,
  matrix and map; Pagefind for static search. Vercel project → `census.forn.al`.
- **Layout:**
  ```
  pipeline/  s1_discover.py … s9_site_data.py, parsers/, detectors/, llm/, store.py, journal.py
  facts/     one .sql per published number
  site/      Astro app
  tests/     fixtures/ (real-shaped harnesses), canaries/, test_* per parser/detector/stage
  docs/      PRD.md, methodology source
  data/      (gitignored) duckdb, parquet, blobs, editions/
  ```
- **CI (public GitHub Actions):** unit tests for every parser and detector on fixtures; schema
  checks on every stage's output; `census facts --check` on the frozen sample edition; an
  adversarial site pass that assumes every page is wrong (numbers vs facts, broken permalinks,
  un-redacted secret patterns, canary leakage); a link checker.
- **Anti-slop:** README and launch post hand-written by Michael; `Assisted-by:` commit trailer; no
  prose tells ("not just X but Y", emoji bullets, gratuitous bold, "pivotal", "testament"); no
  silent catches or TODO stubs in shipped code.

## 9. Milestones

| M | Milestone | Exit criterion |
|---|---|---|
| M0 | Measurement spike (the code is throwaway) | A targets table filled with measured values: lattice requests per seed, GraphQL points and latency per batch, distinct-cluster ratio on a 5k sample, `claude -p` artifacts/hr, embeddings/sec. Plans after M0 use these numbers |
| M1 | Vertical slice (~1,000 harnesses) | S1–S9 end to end; a crude site with one finding, one use-case page, one technique page; `facts --check` green; CI running |
| M2 | Full discover and harvest (unattended) | The whole repo universe harvested; resume verified with `kill -9` mid-S1 and mid-S2, with no loss or duplication |
| M3 | Parse, dedup, lineage and tier-1 on the full corpus | Dedup audit published; detectors pass the canaries; LLM detector audit done |
| M4 | Tier-2, taxonomy, validation | κ per field; taxonomy labels, candidate techniques and Uncharted approved by Michael |
| M5 | Full site | All 8 surfaces, glyphs, matrix, map; the adversarial pass is green |
| M6 | Freeze and launch | Findings chosen, `census freeze`, hand-written README and launch post, deployed to `census.forn.al`, linked from forn.al |

### 9.1 M0 measured targets (measured 2026-09-29)

Produced by the throwaway `spike/m0` scripts. Raw metrics: `docs/m0/`.

**S1: code-search lattice** (paced at 10 req/min)

| Seed | Root total | Lattice requests | Leaves | Floor overflows | Files unreachable | Full-fetch requests | Hours at 10 req/min | Hours at measured rate |
|---|---|---|---|---|---|---|---|---|
| `filename:CLAUDE.md` (partial: 24% of files walked) | 790,528 | 456 (projected 2,340) | 216 | 6 | 39,090 so far | projected 8,266 | 17.7 | 78.0 |
| `filename:.mcp.json` | 65,408 | 192 | 95 | 1 | 310 | 657 | 1.4 | 6.2 |

Not reached yet: `path:.claude`, `path:.claude-plugin`.

Measured effective rate under GitHub secondary limits: 2.3 successful requests/min (over 1,800 s); the last column rescales the hours to it.

**S2: GraphQL harvest**

| Batch size (repos) | Batches | Median cost (points) | Median latency (s) | p90 latency (s) | Retry rate | Repos/hr (latency bound) |
|---|---|---|---|---|---|---|
| 10 | 4 | 1.0 | 1.75 | 1.91 | 0.00 | 20,610 |
| 18 | 1 | 1 | 7.51 | 7.51 | 0.00 | 8,625 |
| 25 | 45 | 1 | 3.71 | 5.06 | 0.00 | 24,266 |
| 26 | 1 | 1 | 5.28 | 5.28 | 0.00 | 17,731 |
| 50 | 94 | 1.0 | 9.24 | 9.87 | 0.43 | 19,485 |
| 100 | 7 | n/a | n/a | n/a | 1.00 | n/a |

Points per repo: 0.027. Repos/hr under the hourly points budget: 186,143. Files fetched: 5,000 (missing 1, binary 0, truncated 0). Redaction rule matches (spike rules, over-redaction included): 765.

**S4: distinct-cluster ratio** (distinct / files, 5k sample)

| Kind | Files | Exact | Normalized | MinHash 0.7 | MinHash 0.8 | MinHash 0.9 | Projected distinct at 0.8 (upper bound) |
|---|---|---|---|---|---|---|---|
| agent | 593 | 0.998 | 0.987 | 0.966 | 0.968 | 0.968 | 230,452 |
| claude_md | 1,969 | 0.992 | 0.985 | 0.965 | 0.968 | 0.969 | 765,234 |
| command | 539 | 0.989 | 0.989 | 0.978 | 0.983 | 0.985 | 212,456 |
| hook | 218 | 0.986 | 0.986 | 0.977 | 0.977 | 0.977 | 85,544 |
| mcp | 162 | 0.901 | 0.877 | 0.864 | 0.864 | 0.870 | 56,083 |
| plugin | 63 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 25,408 |
| settings | 163 | 1.000 | 0.994 | 0.957 | 0.963 | 0.969 | 63,000 |
| settings_local | 213 | 0.958 | 0.944 | 0.944 | 0.944 | 0.944 | 80,687 |
| skill | 1,079 | 0.993 | 0.991 | 0.981 | 0.984 | 0.989 | 426,328 |

Ratio by sample size (MinHash 0.8, all kinds): n=1000: 0.989, n=2500: 0.976, n=4999: 0.969. Projected distinct clusters, all kinds: 1,945,192.

Size-window sampling over-represents small files: 48.4% of sampled `claude_md` files are at most 1,000 bytes vs at least 18.5% of the population, so these ratios lean toward distinct.

**S6: `claude -p` tier-2 throughput**

| Mode | Model | Batch | Workers | Calls | Valid rate | Artifacts/hr | Stopped |
|---|---|---|---|---|---|---|---|
| sweep | haiku | 5 | 1 | 3 | 0.533 | 184 |  |
| sweep | haiku | 10 | 1 | 3 | 0.367 | 239 |  |
| sweep | haiku | 20 | 1 | 3 | 0.350 | 273 |  |
| sweep | sonnet | 5 | 1 | 3 | 0.800 | 1,289 |  |
| sweep | sonnet | 10 | 1 | 3 | 0.667 | 1,071 |  |
| sweep | sonnet | 20 | 1 | 3 | 0.967 | 1,900 |  |
| sustain | sonnet | 20 | 1 | 47 | 0.932 | 1,723 | time |
| sustain | sonnet | 20 | 3 | 141 | 0.902 | 5,035 | time |

API-equivalent cost: $0.0080 per valid artifact. 28.7% of artifacts were truncated to 6,000 characters.

**S7: embeddings**

| Model | Device | Batch | Text | Texts/s |
|---|---|---|---|---|
| BAAI/bge-small-en-v1.5 | mps | 32 | short | 268 |
| BAAI/bge-small-en-v1.5 | mps | 128 | short | 255 |
| BAAI/bge-small-en-v1.5 | mps | 32 | long | 29 |
| BAAI/bge-small-en-v1.5 | mps | 128 | long | 23 |
| BAAI/bge-small-en-v1.5 | cpu | 32 | short | 268 |
| BAAI/bge-small-en-v1.5 | cpu | 128 | short | 240 |
| BAAI/bge-small-en-v1.5 | cpu | 32 | long | 19 |
| BAAI/bge-small-en-v1.5 | cpu | 128 | long | 16 |
| nomic-ai/nomic-embed-text-v1.5 | mps | 32 | short | 68 |
| nomic-ai/nomic-embed-text-v1.5 | mps | 128 | short | 62 |
| nomic-ai/nomic-embed-text-v1.5 | mps | 32 | long | 8 |
| nomic-ai/nomic-embed-text-v1.5 | mps | 128 | long | 7 |
| nomic-ai/nomic-embed-text-v1.5 | cpu | 32 | short | 63 |
| nomic-ai/nomic-embed-text-v1.5 | cpu | 128 | short | 59 |
| nomic-ai/nomic-embed-text-v1.5 | cpu | 32 | long | 7 |
| nomic-ai/nomic-embed-text-v1.5 | cpu | 128 | long | 5 |

**Derived.** Rough estimate: one tier-2 pass over 1,945,192 projected clusters at 5,035 artifacts/hr: 386 hours. Two passes: 773 hours. Biases run both ways: the cluster count is an upper bound and the sample leans toward small (more distinct) files, which push this up; artifacts were truncated to 6,000 characters and a 30-minute run cannot show whether weekly Max-plan limits bind, which push it down.

## 10. Risks and stated limits (these appear on the methodology page)

- **Coverage.** Code search indexes default branches of public repos only, and not all of them.
  This is a census of *indexed public default branches*. Files in single byte sizes with more than
  1,000 hits can't all be reached, and about 6.4% of indexed repos are gone by harvest time. Both
  are counted and published.
- **Rate limits and ToS.** One token, official APIs only, never the web UI or grep.app. GraphQL
  batch size adapts. Code search runs at the measured 2.3 req/min, so full S1 takes days (the
  CLAUDE.md seed alone projects to about 78 hours), which is why it must run unattended and resume.
- **Plan limits.** Tier-2 cost scales with the distinct-cluster count. M0's rough estimate is
  about 386 hours per pass over an upper bound of 1.95M clusters. Haiku is not a cheaper fallback
  at the quality measured so far (§6). If the Max plan's weekly limits bind, pass 1 runs on Sonnet
  everywhere and pass 2 only on a stratified subset plus the disagreements, with κ reported for
  that subset.
- **Privacy.** Redact before storage; permissions only in aggregate; removal path on every page;
  no bulk content release.
- **LLM labels are estimates.** Every semantic number carries its validation score.
- **Point in time.** Edition-dated; not live.
