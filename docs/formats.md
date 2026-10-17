# Formats

These formats belong to Rivet. Schema versions start at 1.

## Canonical JSON and digests

Canonical JSON is `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)`. Keys are sorted by code point. Non-ASCII characters, and path bytes that are not valid UTF-8 (decoded with `surrogateescape`), become `\uXXXX` escapes. Integers are written plainly, and absent fields are omitted rather than written as `null`, unless the structure below shows `null`. A digest is the SHA-256 of the canonical text encoded as ASCII, written as lowercase hex.

## Configuration (`.rivet/config.json`)

```json
{
  "schema_version": 1,
  "context_dir": ".rivet/context",
  "execution_profile": "restricted",
  "orchestration": {"max_calls": 0, "worker_models": []},
  "gated_effects": ["destructive_external", "external_compute", "merge", "pull_request", "push"]
}
```

The file is written in exactly that key order, with two-space indentation and a final newline.
- **Rejected:** unknown fields, booleans in place of integers, unsafe or escaping `context_dir` values, blank or duplicate model names (checked after trimming), and missing mandatory gates.
- **Normalized:** gates and models are sorted and deduplicated.

## Snapshot

```
{
  "format": 1,
  "writer": "<absolute real path of the checkout>",
  "head": "<commit sha>" | null,
  "entries": {
    "<path>": {
      "index": [["<mode>", "<object id>", <stage>], ...],
      "worktree": {"type": "missing"}
                | {"type": "file", "sha256": "<hex>", "mode": "<octal permission bits>"}
                | {"type": "symlink", "target_sha256": "<hex of the link target bytes>"}
                | {"type": "dir"}
    }
  },
  "preexisting": ["<paths dirty vs HEAD, staged, unmerged or untracked>"]
}
```

`entries` covers every index path and every untracked path that is not ignored. Submodules and nested repositories are recorded as `dir`. Their contents are never read, and a submodule's gitlink is part of the parent index. Anything under `.rivet/runtime` (and `.rivet/runtime.bak-*`) is excluded.

| Value | Definition |
| --- | --- |
| Snapshot digest | Canonical digest of the whole snapshot. It is stored with the baseline and checked on every read. |
| `repository_state_fingerprint` | `SHA256(ASCII(digest(B) + digest(C)))`, where B is the writer baseline and C is the current snapshot |
| Delta | Every path in the union of B and C whose `{"index", "worktree"}` value differs. A path missing from one side counts as `{"index": [], "worktree": {"type": "missing"}}` there. |

Evidence is bound to the pair (claim-time HEAD, `repository_state_fingerprint`).

## Proposal fingerprint

`concept-proposal` hashes this object canonically:

```
{"id", "objective", "risk", "max_attempts", "allowed_paths", "effects", "depends_on",
 "concept_ref", "concept_fingerprint", "required_evidence", "external_results",
 "baseline_fingerprint", "preexisting_paths"}
```

Lists are sorted and deduplicated. `external_results` are sorted by `name`. `baseline_fingerprint` is the controller snapshot's digest. Owner, TTL and approval reference are not bound.

## Export (JSON Lines)

```
{"record_type": "rivet-export", "schema_version": 1, "mode": "checkpoint" | "events", "exported_at": "<ISO-8601 UTC>"}
{"record_type": "row", "table": "<table>", "row": {<column>: <value>, ...}}
...
```

A checkpoint lists these tables in foreign-key order:
- `work_items`
- `dependencies`
- `baselines`
- `contracts`
- `leases`
- `budgets`
- `evidence`
- `approvals`
- `followups`
- `writer_worktrees`
- `events`

Rows appear in insertion order, so events keep their local sequence; the sequence number itself is not exported.

Import behaves as follows:
- It accepts only this header and `row` records with exactly the table's columns.
- It skips rows that are identical to existing ones and rejects any conflicting primary key.
- It runs in one transaction, so any error leaves the database unchanged.
- An `events` file can only add events.
- Imported paths and leases may refer to the exporting machine, so run `rivet doctor` after importing.

## Database

The database is SQLite in WAL mode, with a 10-second busy timeout and foreign keys on. Every check-and-write runs in a `BEGIN IMMEDIATE` transaction. `schema_meta.schema_version` is `1`, and any other value fails closed with a pointer to `doctor`. The tables are:
- `work_items`
- `dependencies`
- `baselines`
- `contracts`
- `leases`
- `budgets` (with the CHECK `0 <= used <= reserved <= call_limit`)
- `evidence`
- `approvals`
- `followups`
- `events` (with an `AUTOINCREMENT` sequence)
- `writer_worktrees`
