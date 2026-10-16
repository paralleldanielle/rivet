---
name: rivet-work
description: "Run one bounded engineering work item with Rivet: start or resume it, implement directly or through one worker at a time, prove acceptance with measured evidence, and finish with scope checked. Use for an explicit implementation request or to resume Rivet work in this repository. Keywords: rivet start, implement concept, verify, finish, lease."
---

# Rivet work

Run exactly one bounded work item through the Rivet engine. The engine owns lifecycle, leases, scope, evidence and accounting; you own the judgment and the code.

**Engine.** Run `rivet <command>`. If `rivet` is not on PATH, run `python3 <plugin>/bin/rivet <command>`, where `<plugin>` is two directories above this skill's folder; resolve it to an absolute path once and reuse it. Every command prints one JSON object. `rivet <command> --help` lists flags.

## Rules that always apply
- **The latest human instruction outranks any Rivet item.** If the human redirects you mid-item, pause first (`rivet pause --id <id> --epoch <epoch> --expected-version <version> --reason "<why>"`), then serve the new request at the size it was asked. Answering a question or writing a draft can be the whole turn. Resume only when asked; if the lease expired, report that instead of reusing the epoch.
- **Use the engine for every state change.** Never edit `.rivet/runtime` or the database. Never invent an `epoch` or `version`: copy them from the latest engine output.
- **A non-zero exit is a stop.** stderr carries JSON `error` and `code`. Pause if you can and report. Never retry by weakening criteria, widening paths, or creating a duplicate item.
- **Measurements exit 0 and carry the verdict in JSON:** `verify` → `outcome`, `finish` → `completed`, `check-effect` → `authorized`, `check-scope` → `valid`.

## Start only on an explicit request
Start work only when the human asks for implementation. A draft concept, a discussion, or your own inference is not a request. Establish:
- the objective and the allowed path prefixes (literal prefixes such as `src/billing`, not globs)
- acceptance criteria, preferably runnable; each becomes a named criterion
- anticipated effects (for example `push`) and any external results (commits to be published in another repository)
- the concept file, when implementing an approved concept

Ask only for what is genuinely missing. An explicit request to implement an unchanged, already-bounded concept needs no extra review round. If the repository has no `.rivet/config.json`, use the rivet-config workflow first.

```
rivet start --objective "<text>" --allowed-paths <prefix>... --evidence <criterion>... \
  [--concept-ref .rivet/context/<file>.md] [--effects push] [--risk medium] \
  [--external-result '{"name":"lib","remote":"<url>","ref":"refs/heads/main"}']
```

Keep `id`, `epoch`, `version` and the `delegation` block from the output. If `start` stops part-way (for example `dependency_incomplete`), the item stays `ready`: report it and later resume with `rivet admit --id <id>`, never with a second item.

Use the long form only for dependencies or an open boundary decision: `propose` → `transition --to ready` → `admit` → `claim --owner <you>` → `transition --to running`. To frame a decision about wider bounds, preview with `rivet concept-proposal ...` (read-only) and add `--confirm <proposal_fingerprint>` only after the human agrees.

## Resume existing work
Run `rivet status`, then `rivet show --id <id>` (no epoch needed, no side effects). For ready or paused work with no live lease, `rivet claim --id <id> --owner <you>`. For expired or ownerless work, use the rivet-doctor workflow. The admission baseline survives a reclaim; rerun evidence if `base_sha` moved.

## Do the work
Work directly, or delegate when `delegation.available` is true. Before the first worker, read `references/coordinator.md` (the full protocol) and your host's entry in `references/hosts.md`. In short: `dispatch` immediately before each launch, one worker at a time, always `settle-dispatch`, and review every result yourself.

Stay inside the allowed paths. A brand-new file under the context directory is draft capture: use it only for genuine notes about future ideas, never to work around scope. Never edit the admitted concept; that is concept drift and blocks completion. Record out-of-scope discoveries with `rivet propose-followup --id <id> --epoch <epoch> --objective "<idea>"`.

To isolate the writer, run `rivet create-worktree --id <id> --epoch <epoch> --path <directory outside the repository>` before making any edit, then work in that checkout. Engine calls from either checkout reach the same state.

## Prove acceptance
- **Runnable:** `rivet verify --id <id> --epoch <epoch> --criterion <name> -- <program> <args...>`. No shell is used; pass `bash -c "..."` explicitly if you need one. Read `outcome`, `exit_code` and `output_tail`.
- **Reviews and artifacts the engine cannot run:** `rivet record-evidence --id <id> --epoch <epoch> --batch -` with a JSON array of `{"criterion", "kind", "outcome"}` objects (optionally `artifact`, `command`, `commit_sha`). These stay labeled `caller_reported`; prefer measured verification.
- **Declared external results:** `rivet verify-external --id <id> --epoch <epoch> --name <name> --commit-sha <full sha>`.

Evidence binds to the whole repository state: any later edit, staging, commit or new file makes it stale. Verify after your final change.

## Finish
`rivet finish --id <id> --epoch <epoch>`. If `completed` is false, handle each blocker:
- `evidence_missing`: verify again after your final change.
- `scope_violation`: revert the listed out-of-scope change, or stop and ask. Never widen the contract.
- `concept_drift`: restore the concept's admitted bytes, or stop: a changed concept needs a new approved item.
- `external_result_failed`: publish and verify again, or report.
- `outstanding_dispatch`: wait for the worker, then `settle-dispatch`.

Completion never commits, pushes or publishes.

## Gated effects
Before `push`, `pull_request`, `merge` or another gate listed by `rivet config`: get real approval from the human for this item and effect, run `rivet approve --id <id> --effect <effect> --approver "<who>"`, then `rivet check-effect --id <id> --effect <effect> --consume` immediately before acting. Act only if `authorized` is true. One approval covers one use.

## What is and is not enforced
The engine measures leases, versions, scope, evidence, external results and call counts. It records, but cannot prevent, edits outside scope, host actions without approval, the model that actually ran, or parallel workers; `rivet config` reports each guarantee's level. Only this Git root is measured. Rivet does not limit cost; spending limits belong to the host.

Keep plans and notes in your session, not in the repository.
