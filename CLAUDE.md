# Agent Census

The spec is `docs/PRD.md`. Read it before designing or building anything; it records decisions
already made (Claude Code harnesses only, organized by use case, dated editions, full enumeration,
machine-run validation). Don't re-litigate them without asking.

- Build order is the milestone table in PRD §9. Start at **M0** (measurement spike, throwaway
  code). Later plans use M0's measured numbers, not guesses.
- Every number the site prints comes from `facts.json`, produced by a named query in `facts/`.
  Never hard-code a digit in page copy.
- Every stage is idempotent and resumable (PRD §4). Test resume by killing it, not by reasoning.
- The README and the launch post are written by Michael by hand. Never generate them.
- Commits carry an `Assisted-by: Claude` trailer.
- Privacy: redact secrets before anything is stored; permissions only in aggregate; no bulk
  redistribution of raw file contents.
