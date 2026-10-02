# M2 runbook: the unattended discover and harvest

Edition `fall-2026`. All commands run from the checkout that launched the run, with `CENSUS_DATA` pointing at
the data directory (`/Users/michaelfornal/Documents/agent-census/data`). Paths below are relative to
`$CENSUS_DATA/work/fall-2026/`.

## Start

```bash
export CENSUS_DATA=/Users/michaelfornal/Documents/agent-census/data
uv run census supervise s1 --edition fall-2026 --detach
uv run census supervise s2 --edition fall-2026 --detach   # only once there is room (see Disk)
```

`--detach` starts the supervisor in its own session, so it outlives the shell and the Claude session that
started it. It does not survive a reboot or a logout: run the same command again afterwards. It resumes from
the journal.

Starting it twice prints the same success line, but the second supervisor exits at once. Its refusal
("already supervised by pid N") is in `logs/supervise-<stage>.out`. Check `census status` or the pid file
`logs/supervise-<stage>.pid` before starting.

The supervisor runs `caffeinate -is` while it lives, if `caffeinate` exists. That holds off idle sleep, and
system sleep only on AC power. A closed lid still sleeps the machine unless it is on power with an external
display.

## Watch

```bash
uv run census status --edition fall-2026
tail -f $CENSUS_DATA/work/fall-2026/logs/s1.log
```

`status` prints, per stage, journaled units, parts, rows, deferred units, the S1 families that are fully
walked, and the supervisor's state: total restarts, restarts in a row without progress, and the last exit
code. Look once a day.

**Several restarts in a row without progress means the stage is stuck**, with two exceptions for S2 where
nothing is wrong: S2 is waiting for S1, or its deferred repos are waiting out the one-hour gap between
attempts. Read the last stop message in `logs/<stage>.log` to tell which.

S1 walks eight families in this order: `claude_md/nonfork`, `claude_md/fork`, `claude_dir/nonfork`,
`claude_dir/fork`, `mcp/nonfork`, `mcp/fork`, `plugin/nonfork`, `plugin/fork`. It is complete when the
supervisor logs `complete` and `status` shows `complete: True`.

## What the stop messages mean

A clean stop appears as `<stage>: ran ...; stopped: <message>` and exit 2. A crash appears as a traceback or
a single error line and exit 1. The supervisor treats both the same way: it waits and starts the stage
again. The wait is 60 seconds after a run that journaled something; otherwise it doubles (2, 4, 8, 16
minutes) up to 30 minutes.

| Message in the log | Meaning | What to do |
|---|---|---|
| `exit -9, progress; restarting in 60s` | The stage was killed. | Nothing. It resumes. |
| `code search kept answering incomplete_results` (traceback) | GitHub timed out its own search four times in a row (the answer and three retries). Nothing was recorded. | Nothing at first: the supervisor retries. S1 cannot pass that query until GitHub answers it completely, so if it repeats for a day, S1 is blocked there and the code needs a change (there is no skip). |
| `code search failed after 10 attempts` (traceback) | Rate limits or server errors on every attempt. | Nothing. |
| `waiting for S1 to finish the claude_md families` | S2 started before S1 finished both CLAUDE.md families. | Nothing. S2 checks again after 2, 4, 8 and 16 minutes, then every 30. |
| `S1 is still discovering repos; rerun S2` | S2 harvested everything found so far. | Nothing. |
| `N units deferred after transient failures` | GitHub could not serve N repos. Each is tried at most once an hour. | Nothing. After the fifth failed attempt a repo is recorded as `unreachable` or `partial`. |
| `GitHub GraphQL is failing` | An outage: the health probe failed too. Nothing was recorded. | Nothing. |
| `every REST tree request in the batch failed` | A REST outage (three or more tree calls in the batch, all failed). With fewer than three, the repos are deferred instead. | Nothing. |
| `only X GiB free under ...` | The blob volume is nearly full. | Free space or move the blob store (see Disk). |
| `another process is running this stage` | A second process tried to run the stage. | Stop the extra one. One process per stage and edition. |
| `GitHub token rejected (401)` (S2), or an `HTTPStatusError` 401 traceback (S1) | The token expired. | If `GITHUB_TOKEN` was set when the supervisor started, stop it and start it with the new token. Otherwise `gh auth login`; the next restart picks the new token up. |

A repo recorded as `repos.error = 'unreachable:<why>'` has nothing harvested and is not counted as a harness
(like a deleted repo, `not_found`). A repo recorded as `'partial:<why>'` was harvested without some files or
blobs and is counted. No fact query reports either count yet.

## Stop and restart

```bash
kill "$(cat $CENSUS_DATA/work/fall-2026/logs/supervise-s1.pid)"   # stops the supervisor and its stage
```

The supervisor passes the signal to the stage and exits with 143 (`status` then shows the stage's own exit
code, -15). To start again, run the start command. Nothing is lost by stopping at any point, with `kill` or
`kill -9`.

If only the stage is killed, the supervisor restarts it. If only the supervisor is killed with `kill -9`, the
stage keeps running without a supervisor and without `caffeinate`; kill it too (its pid is in
`logs/s1.child.pid`), then start again.

## Kill test on the full S2

M2's exit criterion asks for a `kill -9` in the middle of the full S2. Once S2 is harvesting:

```bash
kill -9 "$(cat $CENSUS_DATA/work/fall-2026/logs/s2.child.pid)"
# wait for "restarting" and the next "s2: N/M units" line in logs/s2.log, then:
uv run census audit s2 --edition fall-2026
```

The audit must report no missing parts, no row-count differences, no unit journaled twice and no duplicated
rows. While the stage is running it may report one set of parts with no journal line: that is the batch in
flight.

## Disk

- **S1** wrote 2,212 hits in a few hundred KB in the rehearsal; the full walk is a few million hits.
- **S2 tables** needed about 18 KB of snappy Parquet per repo in the rehearsal (`docs/m2/rehearsal.md`).
  Parts are now written with zstd, 43% smaller on `harness_files`: about 10 GiB for a million repos.
  **Nothing checks this volume.**
- **Blobs** live in one SQLite file, `blobs.sqlite`, in the blob root. Measured on 2026-10-02: 10–97 KB of
  compressed blob per repo (preview-oct at the low end, the all-family rehearsal at the high end), stored at
  1.2x that on disk. The earlier one-file-per-blob layout cost 2.1x in 4 KiB blocks. S2 stops by itself when
  less than 5 GiB is free on the blob volume.
- **A blob root still in the old layout** (`<root>/<oid[:2]>/<oid>.zst`) is refused until it is packed:
  `uv run census pack-blobs`. Packing keeps every blob's redaction version and counts, deletes files only
  after their rows commit, and finishes on a rerun if it is killed.

To put the blob store on another volume, set `CENSUS_BLOBS` in the environment of every `census` command:

```bash
rsync -a $CENSUS_DATA/blobs/ /Volumes/<volume>/census-blobs/     # with every supervisor stopped
export CENSUS_BLOBS=/Volumes/<volume>/census-blobs
uv run census supervise s2 --edition fall-2026 --detach
```

If the volume is not mounted, the guard measures the parent directory instead, so mount it first. To move
the tables as well, stop both supervisors, move the whole data directory, and start them with `CENSUS_DATA`
pointing at the new place.

## Things that are not obvious

- **Never set `CENSUS_PACE` for a live run.** It scales every wait and exists for the tests. The clients
  print a line when it is not 1.
- **One code-search budget per token.** Do not run another S1, or the M0 lattice, while S1 runs.
- **`m1-slice` was harvested by the M1 code.** S2's unit keys changed in M2. Re-harvesting `m1-slice` needs
  `census run s2 --reset` first, or every repo is harvested twice and `census freeze` refuses.
- **Redaction can be slow on one odd input.** Text made of thousands of key-like words in a single unbroken
  run (`_KEY_KEY_KEY...`) takes minutes per 200 KB. Nothing is lost; the log is silent meanwhile.
- **The `redactions` table holds counts as of harvest, one copy per batch that touched a blob.** Any number
  about redactions must come from the blob headers (`BlobStore.redaction_counts`), not from that table.
- **Moving the run to another checkout** (for example after merging to `main`): stop the supervisors, then
  start them from the new checkout with the same `CENSUS_DATA`. The journal carries everything.
