# Portability and hardening choices

The first goal is behavioral parity with the original Rivet plugin, as described by the portable specification (`.rivet/context/rivet-portable-specification.md`). Where the original behaved a certain way **by accident**, this implementation fixes it. Where it behaved a certain way **on purpose**, this implementation keeps that behavior and labels it. This page records each of those decisions.

## Spec §20 hardening items

| ID | Status | Behavior here |
| --- | --- | --- |
| H1 | **Implemented** | Only `claim` enters `leased`, and only `complete`/`finish` enter `completed`. Moving to `ready`, `blocked` or `abandoned` drops the lease in the same transaction. `next_transitions` never lists `leased` or `completed`. |
| H2 | **Implemented** | Consuming an approval is one conditional update inside a write transaction, so it happens exactly once. |
| H3 | **Implemented** | The repair token is derived from the current findings. `--fix` re-reads every item under the repair transaction and skips anything renewed or reclaimed since the diagnosis. |
| H4 | **Implemented** | `context_dir`, concept references and `verify --cwd` are resolved through symlinks and must stay inside the repository (or the writer checkout). |
| H5 | Deferred | Non-concept work may still have empty paths (unrestricted scope) or no criteria (no evidence required). Every view labels this through `contract_mode`. Concept work always needs both. |
| H6 | Partial | The profile, call limit, model allow-list and gates are frozen at admission, and `dispatch`/`check-effect` use the frozen values. Control-file edits outside scope are violations, and `doctor` reports `control_file_drift`. Mutations are not refused just because the live config changed. |
| H7 | Deferred | Any valid pass satisfies an ordinary criterion; a later failure on the same state does not cancel it (tested by C58). `show`/`status` report stored validity, labeled `"basis": "recorded"`. `finish` and `complete` always re-evaluate freshness, and `doctor` reports `stale_evidence`. External criteria are the exception: there the latest measurement wins. |
| H8 | **Implemented** | Events export in their local sequence order, and the writer-worktree registry is exported. |
| H9 | **Implemented** | A reserved but unsettled worker call blocks completion (`outstanding_dispatch`). Settling a call after a crash stays an explicit `settle-dispatch`. |

## Other deliberate differences from the original

- **Machine-readable errors:** every error carries a stable `code`.
- **Fingerprint flags removed:** the compatibility flags `--input-fingerprint` and `--diff-fingerprint` are gone, because they could invalidate real evidence.
- **Export path:** `export` defaults to `.rivet/runtime/work.jsonl`, which Git ignores. The spec's suggested `.rivet/work.jsonl` would count as a change to active work. Relative `--in`, `--out` and `--path` values all resolve from the repository root.
- **Controller resolution:** engine calls from a linked writer worktree reach the main checkout's database instead of creating a new one.
- **Read-only commands:** `show`, `status`, `ready` and `doctor` never create runtime state.
- **Doctor commands:** suggested commands use the absolute launcher path plus `--repo`, so they run exactly as printed. A schema reset moves the runtime directory aside as a backup instead of deleting it.
- **Concept start:** `start --concept-ref ... --approval-ref` stores the approval reference. `concept-proposal --confirm` accepts `--owner` and `--ttl-seconds`.
- **`check-effect`:** it errors on unknown items, and never consumes an approval for an effect that is not gated.
- **TTLs:** negative TTLs are rejected. A TTL of 0 is accepted and produces an already-expired lease.
- **Scope of support:** Linux and macOS only. `legacy_head` measurement is not implemented, since there is no older state to import.

## Backlog

- H5: optionally reject empty contracts, or require an explicit `--unrestricted` flag.
- H6: refuse mutations when the effective configuration drifts, rather than only reporting it.
- H7: optionally let the latest result for a criterion win, and check freshness live in read-only views.
- Draft capture: optionally limit the exemption to Markdown files.
- Snapshot performance: cache file hashes by inode, size and mtime for very large repositories.
