# M2 live rehearsal (measured 2026-09-30)

Edition `m2-rehearsal`, live GitHub, one token, branch `worktree-m2` at commit `9bf1001`. Both stages ran
under `census supervise --detach` and were killed with `kill -9` on purpose. All times UTC.
The M0 lattice was stopped first (it shares the code-search budget); its cache is intact.

## S1 discover (slice mode, `--limit 400`)

- Wall: 11:00:07 → 11:25:49 (25.7 min), including one kill and the supervisor's 60 s wait.
- Killed with `kill -9` at 11:06:57, after the first journaled node. Supervisor log:
  `exit -9, progress; restarting in 60s`, then `complete`.
- Resume: the killed family was already at its target, so the next run fetched nothing for it and went on.
- Code search: 109 requests, 49 rate-limited (45%), 60 successful: **2.4 successful requests/min**
  (M1: 2.6; M0: 2.3). 0 server errors, 0 incomplete answers.
- 8 nodes, 2,212 hits, 0 overflows.
- `census audit s1`: nothing lost or duplicated.

## S2 harvest (`--limit 300`)

- Wall: 11:26:12 → 11:32:40 (6.5 min), including one kill and the supervisor's 60 s wait.
- Killed with `kill -9` at 11:27:28 after 3 journal lines (75 repos). The resumed run harvested 225 repos and
  skipped 75.
- `census audit s2`: nothing lost or duplicated. 300 repos rows, 300 distinct repos.

| Measure | M1 (GraphQL tree to depth 4) | M2 rehearsal (GraphQL top level + REST tree) |
|---|---|---|
| Repos per hour of wall time | about 480 | **about 3,300** (300 repos in 5.4 min of stage time) |
| GraphQL posts that returned 5xx | 25% | **0 of 114** (resumed run) |
| Repos recorded missing | 2.6%, all transient timeouts | **0 of 300** |
| Repos with part of `.claude` not listed | 15.6% | **0 of 300** |
| Deferred units | not applicable | 0 |
| Requests per repo (resumed run) | 0.85 posts | 0.51 GraphQL posts + 0.49 REST requests |

GraphQL latency was 0.89 s per post. The harvest rate is bound by the clients' own pacing (1.0 s between
GraphQL posts, 0.75 s between REST requests), not by GitHub: at 0.5 requests per repo on each API, the
5,000-per-hour primary limits allow about 10,000 repos/hr. The pacing was left as it is. Lowering it is a
one-line change per client and should be measured on a longer run before the full S2.

## What the full listing changes

The REST tree lists everything under `.claude`, where M1 stopped at depth 4.

- `harness_files`: 99,028 rows for 300 repos. Mean 349 rows per repo with files, median 4, 90th percentile
  422, 99th percentile 6,722. M1 had 48.7 rows per repo.
- By kind: skill_file 53,356; other_claude 38,202; skill 3,133; command 1,717; agent 1,456; hook 654;
  claude_md 269; settings 86; mcp 66; marketplace 48; settings_local 26; plugin 11.
- 19,372 of the other_claude rows are under `.claude/worktrees/` (whole checkouts committed by mistake), and
  10,693 under `.claude/references/`. Three repos hold 47,350 of the 99,028 rows.
- Only parsed kinds are fetched: 7,454 files fetched, 6,415 distinct blobs, 12 skipped as too large.
- Redactions: 462 rows.

## Projections for the full run

- **S1:** about 55,000 search requests. At 2.4–2.6 successful requests/min that is 15–16 days.
- **S2:** at 3,300 repos/hr, a million repos is 12.6 days; S2 can start once S1 has finished both CLAUDE.md
  families (about four days in) and then runs alongside S1.
- **Disk, tables:** the rehearsal's tables take 5.4 MB for 300 repos, 18 KB per repo, almost all of it
  `harness_files`. A million repos is about 18 GiB of Parquet, in addition to the blobs.
- **Disk, blobs:** unchanged estimate, 35–80 GiB. The data volume had 10 GiB free at the end of the
  rehearsal.

## Open questions this raises

- Whether to record files under `.claude/worktrees/` at all. They are not harness files, and they are a
  fifth of all rows in this sample. Dropping them is a content decision, so it was not made here.
- Whether to lower the client pacing before the full S2.
