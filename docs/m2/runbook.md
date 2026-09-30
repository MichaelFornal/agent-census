# M2 runbook: the unattended discover and harvest

Edition `fall-2026`. All commands run from the checkout that launched the run, with `CENSUS_DATA` pointing at
the data directory (`/Users/michaelfornal/Documents/agent-census/data`). Paths below are relative to
`$CENSUS_DATA/work/fall-2026/`.

## Start

```bash
export CENSUS_DATA=/Users/michaelfornal/Documents/agent-census/data
uv run census supervise s1 --edition fall-2026 --detach
uv run census supervise s2 --edition fall-2026 --detach   # only once the blob store has room (see Disk)
```

`--detach` starts the supervisor in its own session, so it outlives the shell and the Claude session that
started it. It does not survive a reboot or a logout: run the same command again afterwards. It resumes from
the journal. Starting it twice is refused ("already supervised by pid N").

The supervisor keeps the Mac awake with `caffeinate` while it runs. A closed lid still sleeps the machine
unless it is on power with an external display.

## Watch

```bash
uv run census status --edition fall-2026
tail -f $CENSUS_DATA/work/fall-2026/logs/s1.log
```

`status` prints, per stage, journaled units, parts, rows, deferred units, the S1 families that are fully
walked, and the supervisor's state: total restarts, restarts in a row without progress, and the last exit
code. **Several restarts in a row without progress means the stage is stuck.** Read the end of
`logs/<stage>.log` for the reason. Look once a day.

S1 walks eight families in this order: `claude_md/nonfork`, `claude_md/fork`, `claude_dir/nonfork`,
`claude_dir/fork`, `mcp/nonfork`, `mcp/fork`, `plugin/nonfork`, `plugin/fork`. It is complete when the
supervisor logs `complete` and `status` shows `complete: True`.

## What the stop messages mean

| Message in the log | Meaning | What to do |
|---|---|---|
| `exit -9 ... restarting` | The stage was killed. | Nothing. It resumes. |
| `code search kept answering incomplete_results` | GitHub timed out its own search three times. Nothing was recorded. | Nothing. The supervisor retries with backoff. If it repeats for a day on the same query, GitHub cannot answer it; that query needs a narrower split. |
| `code search failed after 10 attempts` | Rate limits or server errors on every attempt. | Nothing. |
| `waiting for S1 to finish the claude_md families` | S2 started before S1 finished both CLAUDE.md families. | Nothing. S2 checks again, at most every 30 minutes. |
| `S1 is still discovering repos; rerun S2` | S2 harvested everything found so far. | Nothing. |
| `N units deferred after transient failures` | GitHub could not serve N repos this run. They are retried, at most once an hour each, five times. | Nothing. |
| `GitHub GraphQL is failing` / `every REST tree request in the batch failed` | An outage. Nothing was recorded. | Nothing. |
| `only X GiB free under ...` | The blob volume is nearly full. | Free space or move the blob store (see Disk). |
| `another process is running this stage` | A second process tried to run the stage. | Stop the extra one. One process per stage and edition. |
| `GitHub token rejected (401)` | The token expired. | `gh auth login`, then nothing: the supervisor retries. |

After five failed attempts a repo is recorded with `repos.error = 'unreachable:<why>'` (nothing harvested) or
`'partial:<why>'` (harvested without some files or blobs). These are counted in the coverage numbers.

## Stop and restart

```bash
kill "$(cat $CENSUS_DATA/work/fall-2026/logs/supervise-s1.pid)"   # stops the supervisor and its stage
```

The supervisor passes the signal to the stage and exits with 143. To start again, run the start command.
Nothing is lost by stopping at any point, with `kill` or `kill -9`.

If only the stage is killed, the supervisor restarts it. If only the supervisor is killed with `kill -9`, the
stage keeps running without a supervisor and without `caffeinate`; kill it too (`logs/s1.child.pid`), then
start again.

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

S1 needs well under 1 GiB. The blob store needs an estimated 35–80 GiB for the full harvest. S2 stops by
itself when less than 5 GiB is free on the blob volume, and only that volume is checked.

To put the blob store on another volume, set `CENSUS_BLOBS` in the environment of every `census` command:

```bash
rsync -a $CENSUS_DATA/blobs/ /Volumes/<volume>/census-blobs/     # the M1 and rehearsal blobs
export CENSUS_BLOBS=/Volumes/<volume>/census-blobs
uv run census supervise s2 --edition fall-2026 --detach
```

If the volume is not mounted, the guard measures the parent directory instead, so mount it first.

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
