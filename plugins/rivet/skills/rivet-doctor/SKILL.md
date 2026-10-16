---
name: rivet-doctor
description: "Diagnose Rivet state honestly and apply only the engine's suggested lease/lifecycle repair after the human confirms. Use when work looks stuck, a lease expired, a session ended mid-item, the engine reports a schema or configuration problem, or someone asks what Rivet actually enforces. Keywords: rivet doctor, repair, expired lease, stale work."
---

# Rivet doctor

Report the engine's diagnosis exactly as returned, and apply only the repair it suggests, after the human confirms.

**Engine.** Run `rivet <command>`. If `rivet` is not on PATH, run `python3 <plugin>/bin/rivet <command>`, where `<plugin>` is two directories above this skill's folder; resolve it to an absolute path once and reuse it. Every command prints one JSON object.

## Steps
1. Run `rivet doctor`.
2. Report `ok` exactly as returned, and each finding's `code`, `severity`, `work_id` and `detail`. Any `error` finding means not ok. Report the `enforcement` and `delegation` blocks as returned; do not restate or reinterpret them.
3. If `suggested_repair` is present, show its `command` and `note` verbatim and ask the human to confirm.
4. After confirmation, run that exact command and nothing else. Never construct or guess a token.
5. Run `rivet doctor` again and report the result.

## Specific findings
- `expired_lease`, `orphaned_item`: the repair moves `leased` back to `ready` and started work to `paused`. It keeps attempts, call counts, evidence, the contract and the code, and it does not grant a lease. Resume with `rivet claim` in the rivet-work workflow.
- `schema_mismatch`: the suggested command moves the runtime directory aside as a backup and reinitializes. Local leases, evidence and accounting are lost; configuration and concepts are kept. Run it only after explicit confirmation.
- `configuration_unusable`: run the finding's `fix` command through the rivet-config workflow.
- `scope_violation`, `concept_drift`, `stale_evidence`, `control_file_drift`, `outstanding_dispatch`: report them. The item's owner resolves them in the rivet-work workflow.
- `missing_worktree`, `writer_missing` (common after importing state from another machine): report them. Do not recreate or delete checkouts.

## Never
- Edit the database, the configuration or runtime files to make findings disappear.
- Delete worktrees, change code or concepts, consume approvals, rerun commands, or publish.
- Claim more than the engine enforces. Approvals, worker models, one-worker-at-a-time and cost limits are protocol-only. Only this Git root is measured, and spending limits belong to the host.
