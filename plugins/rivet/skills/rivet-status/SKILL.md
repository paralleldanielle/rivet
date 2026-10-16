---
name: rivet-status
description: "Read-only report of Rivet work in this repository: what exists, who holds it, what is blocked, stale or missing evidence, and how much delegation remains. Use for handoffs, a fresh session, or 'where are we?'. Not for changing state or reading progress from documents. Keywords: rivet status, handoff, resume, blockers."
---

# Rivet status

Report the actual state of Rivet work for a handoff, using only engine output and read-only Git. Change nothing.

**Engine.** Run `rivet <command>`. If `rivet` is not on PATH, run `python3 <plugin>/bin/rivet <command>`, where `<plugin>` is two directories above this skill's folder; resolve it to an absolute path once and reuse it. Every command prints one JSON object.

## Steps
1. `rivet status` (compact). If `initialized` is false, say so and stop.
2. `rivet show --id <id>` for each item that needs detail.
3. `rivet status --full` only when investigating something specific.
4. Read-only Git: the current branch, HEAD, and `git status --short`.

## Report
- The profile, the context directory, and whether delegation is available
- For each active item: state, owner, time left on the lease, base SHA, writer path, attempts used, and remaining worker calls
- Blockers by code: `dependency_incomplete`, `lease_expired`, `lease_missing`, `attempts_exhausted`, `evidence_missing`, `external_result_failed`, `outstanding_dispatch`
- Missing criteria, and the source (`engine_measured` or `caller_reported`) of satisfied ones. Evidence in these views is as recorded; it may be stale until verified again.
- Scope status (`clean`, `violated`, `unavailable`, `unknown`), violating paths, and concept drift
- Open follow-ups

External results are evidence about another repository, not scope enforcement there.

## Rules
- Never claim, renew, pause, repair or run verification to make the report look better. `show` and `status` do not renew leases.
- Do not infer progress from documents, notes or transcripts; only the engine's state counts.
- Send stale or ownerless work to the rivet-doctor workflow, and resumption to the rivet-work workflow.
