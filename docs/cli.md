# CLI reference

`rivet [--repo PATH] COMMAND [options]`. Engine state belongs to the target repository. The engine resolves it through Git from the current directory, or from `--repo`. A call made from a subdirectory or from a linked writer worktree reaches the main checkout's state.

## Conventions

- **Success:** exactly one JSON object on stdout, exit 0.
- **Errors:** `{"error": "<reason>", "code": "<stable code>", ...}` on stderr, exit 1. Some errors carry extra fields, such as `changed_paths`, `missing`, `violations` or `work_ids`.
- **Usage errors:** unknown flags or commands print conventional usage text and exit 2.
- **Measurements succeed even when the verdict is negative.** Check the field, not the exit code:

| Command | Verdict field |
| --- | --- |
| `verify`, `verify-external` | `outcome` |
| `finish` | `completed` |
| `check-effect` | `authorized` |
| `check-scope` | `valid` |
| `doctor` | `ok` |

- Take `epoch` and `version` from engine output. Lifecycle mutations need `--expected-version`, and owned work needs the live `--epoch`.
- Any call that presents the live epoch renews the lease to at least now + TTL. `show`, `status`, `ready` and `doctor` never renew, and never create runtime state.
- List flags take several space-separated values after one flag (`--allowed-paths src tests`). `--external-result` and `--worker-model` repeat instead.

## Commands

| Command | Arguments | Result |
| --- | --- | --- |
| `init` | — | Default restricted config if missing, context dir, `.rivet/.gitignore` if missing, runtime DB; `warnings` |
| `config` | — | `config`, `enforcement`, workspace `delegation` |
| `config-init` | `--profile P [--context-dir D] [--max-calls N] [--worker-model M]... [--replace] [--dry-run]` | `action` (create/replace), `config`, `rendered`, `delegation`, `enforcement` |
| `concept-fingerprint` | `--concept-ref PATH` | `concept_ref`, `concept_fingerprint` (SHA-256 of exact bytes) |
| `concept-proposal` | `--concept-ref PATH --objective T --allowed-paths P... [proposal options] [--confirm FP] [--owner O] [--ttl-seconds N] [--approval-ref R]` | Preview: `proposal`, `proposal_fingerprint`. Confirmed: like `start` |
| `propose` | `--objective T [proposal options]` | `id`, `state: proposed` (refuses `--concept-ref`) |
| `ready` | — | Proposed or ready items whose dependencies are completed |
| `admit` | `--id ID [--evidence C...] [--approval-ref R] [--concept-fingerprint H] [--design-fingerprint H]` | Frozen contract, `contract_mode`, `baseline_fingerprint`, `preexisting_paths`, `budgets`, `delegation` |
| `start` | `--objective T [--concept-ref PATH] [proposal options] [--owner O] [--ttl-seconds N] [--approval-ref R]` | Running item: `id`, `epoch`, `version` (4), `base_sha`, `writer_path`, contract fields |
| `claim` | `--id ID --owner O [--worktree PATH] [--ttl-seconds N]` | `epoch`, `version`, `base_sha`, `writer_path`, `attempts` |
| `heartbeat` | `--id ID --epoch E [--ttl-seconds N]` | `renewed`, `expires_at` |
| `transition` | `--id ID --to S --expected-version V [--epoch E] [--reason T]` | `state`, `version`, `lease_released` |
| `pause` | `--id ID --epoch E --expected-version V --reason T` | `state: paused`, lease kept |
| `release` | `--id ID --epoch E --expected-version V [--reason T]` | `state: ready`, lease removed |
| `abandon` | `--id ID --expected-version V --reason T [--epoch E]` | `state: abandoned` |
| `dispatch` | `--id ID --epoch E --model M --adapter A` | `reserved`, `used`, `limit`, `remaining_calls` |
| `settle-dispatch` | `--id ID --epoch E --outcome completed\|failed` | `used`, `reserved`, `remaining_calls` |
| `verify` | `--id ID --epoch E --criterion C [--kind K] [--artifact R] [--timeout-seconds N] [--cwd DIR] -- PROGRAM ARGS...` | `outcome`, `exit_code`, `timed_out`, `duration_seconds`, `output_tail`, fingerprints, `invalidated` |
| `record-evidence` | `--id ID --epoch E` plus `--criterion C --kind K --outcome pass\|fail [--command T] [--artifact R] [--commit-sha S]`, or `--batch FILE\|-` | `evidence_ids`, `recorded`, `source: caller_reported`, `invalidated` |
| `verify-external` | `--id ID --epoch E --name N --commit-sha SHA` | `outcome`, `detail`, `commit_sha` |
| `check-scope` | `--id ID --epoch E` | `valid`, `violations`, `concept_drift`, `draft_paths`, `post_admission_paths`, both pre-existing lists |
| `complete` | `--id ID --epoch E --expected-version V` | Guarded completion, or an error naming the first failed guard |
| `finish` | `--id ID --epoch E` | `completed`, `blockers`, `evidence`, `scope` |
| `show` | `--id ID` | One item: contract, lease, budgets, delegation, recorded evidence, scope, blockers, `next_transitions`, follow-ups |
| `status` | `[--full]` | Compact: profile, delegation, counts, active items, blockers. `--full` adds raw records, which are diagnostic and not a stable interface |
| `doctor` | `[--fix --confirm TOKEN]` | `ok`, `findings`, `enforcement`, `delegation`, `suggested_repair` / `repaired` |
| `approve` | `--id ID --effect E --approver LABEL` | `approval_id` |
| `check-effect` | `--id ID --effect E [--consume]` | `gated`, `authorized`, `consumed`, `approval_id` |
| `propose-followup` | `--id ID --epoch E --objective T` | Quarantined follow-up in `proposed` state |
| `create-worktree` | `--id ID --epoch E --path DIR [--branch B]` | Registered writer `path`, `branch`, `base_sha` |
| `export` | `[--out PATH] [--since ISO]` | JSONL checkpoint (default `.rivet/runtime/work.jsonl`), or events only with `--since` |
| `import` | `--in PATH` | `imported`, `skipped` |
| `bootstrap` | see [hosts.md](hosts.md) | `actions`, `conflicts`, `next_steps` |

The proposal options are:
- `--id`
- `--risk low|medium|high|critical`
- `--allowed-paths`
- `--effects`
- `--evidence` (alias `--required-evidence`)
- `--external-result JSON` (repeatable)
- `--depends-on`
- `--max-attempts`

Relative `--out`, `--in` and `--path` values resolve from the repository root.

## Lifecycle

```
proposed -> ready -> leased -> running -> verification -> completed
```

Also available: `design_review` before admission; `paused`, `blocked` and `abandoned`; and `human_review`, which is a compatibility state entered only by explicit transition.

| Rule | Detail |
| --- | --- |
| Entering `leased` | Only `claim` can do this |
| Entering `completed` | Only `complete` and `finish` can do this |
| Moving to `ready`, `blocked` or `abandoned` | Drops the lease in the same transaction |
| Version numbers in a direct start | 1 at proposal, 2 at ready, 3 at claim, 4 at running |

## Codes

| Kind | Codes |
| --- | --- |
| Blockers (`show`, `status`, `finish`) | `dependency_incomplete`, `lease_expired`, `lease_missing`, `attempts_exhausted`, `evidence_missing`, `external_result_failed`, `scope_violation`, `concept_drift`, `outstanding_dispatch` |
| Doctor findings | `not_initialized`, `configuration_unusable`, `schema_mismatch`, `integrity_failed`, `runtime_not_ignored`, `expired_lease`, `orphaned_item`, `dependency_cycle`, `missing_worktree`, `writer_missing`, `scope_check_failed`, `scope_violation`, `concept_drift`, `stale_evidence`, `control_file_drift`, `outstanding_dispatch`, `enforcement_notice` |
| Common errors | `not_a_git_repo`, `not_initialized`, `not_found`, `duplicate_id`, `invalid_argument`, `invalid_path`, `invalid_state`, `invalid_transition`, `version_conflict`, `lease_missing`, `lease_held`, `lease_expired`, `epoch_mismatch`, `attempts_exhausted`, `not_admitted`, `already_admitted`, `criteria_mismatch`, `dependency_incomplete`, `worktree_changed`, `writer_mismatch`, `concept_invalid`, `concept_drift`, `concept_requires_start`, `bounds_required`, `confirmation_mismatch`, `scope_unavailable`, `scope_violation`, `evidence_missing`, `reserved_criterion`, `launch_failed`, `cwd_invalid`, `external_invalid`, `external_result_failed`, `delegation_unavailable`, `model_not_allowed`, `budget_exhausted`, `settlement_invalid`, `outstanding_dispatch`, `config_invalid`, `config_schema_unsupported`, `config_exists`, `config_locked`, `db_schema_unsupported`, `confirmation_required`, `repair_token_invalid`, `import_invalid`, `import_conflict`, `worktree_invalid`, `repo_writes_blocked`, `git_failed` |
