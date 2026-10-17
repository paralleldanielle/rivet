# Rivet Specification

**Status:** Normative.

**Purpose:** Define the observable behavior of Rivet, a host-portable system for
running bounded engineering work items with durable local state. Any
implementation that meets these requirements, on any agent host, is a Rivet
implementation.

This document describes observable behavior and design requirements. It is not
source code or a license grant.

## 1. Reading this specification

MUST, SHOULD, and MAY indicate requirements, recommendations, and optional
features, respectively.

The executable name `rivet`, the `.rivet` directory, command names, and JSON
field names below are the concrete interface. Behavior is specified, not binary
or storage layout: an implementation may store state however it likes, provided
the observable results match.

Schema versions for configuration, the runtime database, and exports start at
`1`.

## 2. Product definition

The system coordinates **one bounded engineering work item per invocation or
agent session**, with durable local handoff between sessions. It provides:

- An immutable admitted contract describing what may change and what proves success.
- Exclusive, expiring ownership of each work item.
- Versioned lifecycle transitions.
- Git-aware measurement of net changes since admission.
- Pinning of the human-owned concept used to authorize the work.
- A finite count of delegated worker invocations.
- Recorded verification evidence tied to a particular repository state.
- Explicit approval checks for consequential effects.
- Readable status, diagnostics, limited repair, and state handoff.

The database can contain multiple work items and dependencies. It is not an
autonomous scheduler, a distributed task service, or a persistent worker queue.
There is no automatic execution of ready items.

### 2.1 Separate sources of truth

| Layer | Owns | Does not own |
| --- | --- | --- |
| Human-owned Markdown | Objectives, design decisions, constraints, acceptance boundaries | Current execution progress or copied runtime records |
| Local state engine | Contracts, lifecycle, leases, attempts, call accounting, evidence references, approvals, events | Source contents, implementation plans, transcripts |
| Git, source, tests, external engineering systems | Actual implementation and results | Permission to expand an admitted contract |

Documents SHOULD be sufficient for a fresh agent session without replaying the
conversation. Execution progress MUST NOT be inferred from stale prose.

### 2.2 Non-goals

Do not require a central service, a particular model provider, an issue tracker,
a corporate taxonomy, a network identity system, a proprietary host extension,
or any organization-specific process.

The core system does not:

- Enforce credit, token, tool-call, output-volume, or elapsed-time budgets.
- Guarantee that arbitrary filesystem edits remain inside scope before they happen.
- Prevent arbitrary host commands, recursive agents, or external side effects.
- Lock an entire repository or coordinate writers in other repositories.
- Require a Git worktree, a commit per worker, a final commit, or a pull request.
- Judge whether a concept is good, a test is sufficient, or a request was actually approved.
- Maintain a persistent child-task plan or turn discoveries into executable work automatically.

## 3. Architecture and packaging

Implement four components:

| Component | Responsibility |
| --- | --- |
| Deterministic engine | Validate contracts and perform state, Git, evidence, and approval operations |
| Launcher | Locate and invoke the engine independently of the target repository |
| Five user workflows | Configuration, concept authoring, bounded work, status, diagnostics |
| Two agent roles | Lease-owning coordinator and non-delegating bounded worker |

A language supporting SQLite, JSON, subprocesses, cryptographic hashing, and
filesystem inspection is sufficient. Python 3.10+ with its standard library is
one suitable implementation, not a required source dependency.

The launcher MUST work when invoked by absolute path from a different Git
repository. Engine state belongs to the **target repository**, not the installed
plugin directory.

The five workflows and two agent roles MUST ship together. Any host-specific
manifest or frontmatter SHOULD contain only the fields required by that host,
generic discoverability metadata, and the project's own ownership and licensing
information. It SHOULD NOT contain organizational tags, account handles,
internal links, model lists, or integration settings.

### 3.1 Workspace layout

| Path | Purpose | Version-control policy |
| --- | --- | --- |
| `.rivet/config.json` | Workspace configuration | May be reviewed and committed |
| `.rivet/context/` | Default location for human-owned Markdown | May be reviewed and committed |
| `.rivet/.gitignore` | Excludes `runtime/` | May be committed |
| `.rivet/runtime/state.db` | Local SQLite state | Must be ignored |
| `.rivet/runtime/` | Database auxiliaries and optional derived registries | Must be ignored |
| `.rivet/runtime/work.jsonl` | Default explicit export location | Ignored with the rest of `runtime/` |

`context_dir` can replace the default Markdown directory. It MUST remain a
safe path relative to the Git root.

`context_dir` MUST be resolved through symbolic links and still lie inside the
repository.

Engine commands MUST resolve the controller repository through Git, including
when invoked from a subdirectory or from a linked writer worktree: such calls
reach the controller's database instead of creating a new one. Lifecycle calls
address the controller's database; verification commands execute in the
assigned writer checkout.

Runtime data MUST NOT be committed. An export written outside `runtime/` is not
ignored, so creating it can itself change scope and invalidate evidence.

## 4. Configuration contract

### 4.1 Shape and defaults

```json
{
  "schema_version": 1,
  "context_dir": ".rivet/context",
  "execution_profile": "restricted",
  "orchestration": {
    "max_calls": 0,
    "worker_models": []
  },
  "gated_effects": [
    "destructive_external",
    "external_compute",
    "merge",
    "pull_request",
    "push"
  ]
}
```

The implementation MUST independently version and validate its configuration.
Reject an unsupported schema instead of guessing a migration.

| Field | Validation and semantics |
| --- | --- |
| `schema_version` | Exactly the supported integer version |
| `context_dir` | Nonempty relative directory; not `.`; no absolute path or `..` component |
| `execution_profile` | Exactly `restricted` or `orchestrated` |
| `orchestration.max_calls` | Nonnegative integer; booleans are not integers |
| `orchestration.worker_models` | Array of unique, nonblank, whitespace-trimmed exact host model names |
| `gated_effects` | Array of nonempty effect identifiers containing every mandatory gate |

Reject unknown top-level fields and unknown orchestration fields. Missing
optional settings can be filled from defaults after schema validation.

`restricted` requires `max_calls = 0`. `orchestrated` requires an explicitly
selected positive maximum.

An empty model list means **any model offered by the host is permitted**.
A nonempty list is an exact allow-list, not a substring, alias, cost-tier, or
provider match. Normalize its ordering for deterministic output.

The five gates shown above are mandatory. Additional gates MAY be configured;
removing a mandatory gate MUST be rejected. Deduplicate and sort gate names.

### 4.2 Configuration operations

`config-init --dry-run` MUST validate and return the exact proposed configuration,
target path, action (`create` or `replace`), delegation readiness, and enforcement
report without writing files or creating runtime state.

Creating or replacing configuration is a human-confirmed workflow. The engine
does not itself prove that the confirmation occurred.

Existing configuration requires `--replace`. Replacement MUST be rejected if
any nonterminal item has an admitted contract, including ready, paused, or
blocked items. Unadmitted proposals alone do not lock configuration.

Write canonical, indented UTF-8 JSON with a final newline. A successful
configuration write creates the chosen context directory.

`init` MUST preserve existing configuration, create the restricted default if
missing, initialize runtime state, and create the runtime ignore rule
if absent. It MUST NOT overwrite an existing ignore file. Concept authoring can
create a context directory later if initialization did not create it.
If an existing ignore file leaves runtime state visible to Git, report the
required ignore rule rather than silently replacing the file.

`config` MUST load and report settings without creating work, renewing leases,
or changing configuration.

### 4.3 Delegation readiness

Return a `delegation` object from configuration, admission, item inspection,
status, and diagnostics:

```json
{
  "available": true,
  "max_calls": 4,
  "remaining_calls": 3,
  "worker_models": [],
  "reason": "An orchestrated item has calls remaining."
}
```

`available` MUST be a JSON boolean. Before admission, `remaining_calls` can be
omitted. For an admitted item it is `limit - reserved`, not `limit - used`.

Return false for the restricted profile or exhausted item budget.
This object reports engine eligibility, **not host capability discovery**:
the adapter must separately establish that its host can launch the requested
worker and model.

## 5. Logical persistent model

Equivalent table layouts are acceptable. The following information and
relationships MUST survive process termination.

### 5.1 Work item

| Field | Type and meaning |
| --- | --- |
| `id` | Unique opaque identifier, generated if not supplied |
| `objective` | Nonblank description of the bounded outcome |
| `state` | Lifecycle state |
| `risk` | `low`, `medium`, `high`, or `critical`; default `low` |
| `allowed_paths` | Repository-relative path prefixes |
| `required_effects` | Effects anticipated by the proposal; not permissions |
| `concept_ref` | Optional repository-relative concept file |
| `concept_fingerprint` | Optional SHA-256 hash of concept bytes |
| `proposed_evidence` | Frozen criterion names when a concept proposal was confirmed |
| `external_results` | Declared external result descriptors |
| `version` | Integer starting at 1; advanced by lifecycle mutations |
| `attempts` | Successful claim count, starting at 0 |
| `max_attempts` | Maximum successful claims; default 3 |
| timestamps | Creation and latest lifecycle update |

Dependencies are directed `(work_id, depends_on)` edges. Referenced items MUST
exist. Completion of every prerequisite is required for admission.

The implementation SHOULD validate a positive `max_attempts` on all proposal
paths. The concept-linked path MUST reject nonpositive values.

### 5.2 Admitted contract

There is at most one contract per item. It freezes:

- Objective, allowed paths, anticipated effects, and required evidence criteria.
- Optional approval reference and informational design fingerprint.
- Concept location and exact approved concept hash when applicable.
- External result declarations.
- Configured context directory as the draft-capture allowance.
- Fingerprints of configuration and the runtime ignore control file.
- Delegation limit and execution profile used at admission.
- Admission baseline reference, digest, and admission checkout identity.

An approval reference or design fingerprint is metadata, not an independently
validated authorization or a substitute for the concept hash.

There is no in-place scope amendment operation. Changed objective, acceptance,
or scope requires a new approved contract and normally a new work item.

### 5.3 Lease

One row per item at most:

`work_id`, `owner`, opaque `epoch`, claim-time `base_sha`, writer worktree path,
acquisition time, absolute expiry time, TTL seconds, and last heartbeat time.

The owner is a stable descriptive identity. The epoch is a fencing token unique
to that particular claim; knowing the owner name is not enough to mutate work.

### 5.4 Delegation accounting

One named accounting record, `delegated_calls`, per admitted item:

```text
0 <= used <= reserved <= limit
remaining_calls = limit - reserved
outstanding_calls = reserved - used
```

`reserved` counts dispatch slots permanently charged to the item.
`used` counts those slots subsequently settled. These counters MUST NOT
decrease, and an unsuccessful worker is not refunded.

Attempts are stored on the item, not in this accounting record.

### 5.5 Evidence

Each immutable evidence row contains:

| Group | Fields |
| --- | --- |
| Identity | Evidence ID, work ID, criterion, creation timestamp |
| Result | Kind, `pass` or `fail`, source, validity flag |
| Subject | Input fingerprint and repository-state fingerprint |
| Command measurement | Optional command description, exit code, elapsed seconds, output tail |
| References | Optional artifact reference and commit SHA |

The source MUST distinguish `engine_measured` from `caller_reported`.
An artifact path is a reference; the core engine does not certify its contents.

### 5.6 Other records

| Record | Required information |
| --- | --- |
| Approval | ID, work ID, exact effect, approver label, timestamp, consumed flag |
| Follow-up | ID, parent item, objective, `proposed` state, timestamp |
| Event | ID, optional work ID, event kind, structured detail, timestamp |
| Writer worktree | Work ID, absolute path, unique branch, base SHA, creation time |
| Schema metadata | Independently versioned database schema |

Baseline snapshots can live in immutable admission/worktree events or a dedicated
table. They MUST persist with state export and import.

Events MUST have a stable sequence independent of timestamp resolution and
random identifiers. Preserve that ordering across handoff.

### 5.7 Transaction and durability rules

Use SQLite foreign keys and database uniqueness/check constraints, not only
application checks. WAL mode and an approximately 10-second busy timeout are
suitable defaults.

Claims, transitions, reservations, settlements, and completion MUST serialize
their checks and writes in transactions. Concurrent claims of one item MUST
produce exactly one winner.

State change and its event SHOULD commit together. Failed mutations MUST NOT
partially update lifecycle or counters. Long-running Git, subprocess, and network
operations SHOULD occur outside write transactions, followed by lease, version,
and repository-state revalidation before settlement.

The database is local coordination, not a security boundary against a process
that can rewrite it.

## 6. Lifecycle

The normal route is:

```text
proposed -> ready -> leased -> running -> verification -> completed
```

There is no separate review state. A human review that must happen before
completion is an acceptance criterion satisfied by caller-reported evidence
(section 11.3); risk never adds a review step.

### 6.1 State graph

| From | Permitted destinations |
| --- | --- |
| `proposed` | `ready`, `abandoned` |
| `ready` | `leased`, `blocked`, `abandoned` |
| `leased` | `running`, `ready`, `paused`, `abandoned` |
| `running` | `verification`, `paused`, `blocked`, `abandoned` |
| `verification` | `running`, `paused`, `abandoned`; `completed` through completion |
| `paused` | `ready`, `leased`, `running`, `verification`, `abandoned` |
| `blocked` | `ready`, `abandoned` |
| `completed` | None |
| `abandoned` | None |

Graph eligibility is not sufficient authorization. Claim, lease, admission,
dependency, and completion preconditions still apply.

Two destinations are reachable only through specialized operations:

- Only `claim` enters `leased`.
- Only guarded completion (`complete` or `finish`) enters `completed`.

A generic `transition` to either MUST be rejected. A transition to `ready`,
`blocked`, or `abandoned` MUST remove any lease in the same transaction, so no
live lease is stranded on work that cannot be claimed.

`next_transitions` reports graph destinations reachable by generic transition,
excluding `leased` and `completed`. Callers MUST NOT mistake it for a guarantee
that every listed transition is currently executable.

### 6.2 Compare-and-set

Lifecycle mutation takes `expected_version`. Reject a stale version before
changing state. The update MUST condition on the same version and increment it
exactly once.

If a lease row exists, lifecycle mutation MUST authenticate its current live
epoch. Expired lease rows do not grant permission to change state without an
epoch.

Successful claim increments version and attempts. Admission alone, heartbeats,
evidence insertion, and budget accounting do not need to advance item version.

A typical direct start produces versions 1 at proposal, 2 at ready, 3 at claim,
and 4 at running.

### 6.3 Pause, release, abandonment

`pause` requires a live epoch, expected version, and reason. It changes state to
`paused` but **does not delete the lease**.

Consequently another session cannot immediately claim paused work while its old
lease is live. For an immediate handoff, pause and then release; otherwise wait
for expiry and use the documented recovery path.

`release` accepts leased or paused work, changes it to ready, and removes its
lease. Running work must be paused first. It does not reset the admission
baseline, attempts, evidence history, or call counters.

`abandon` requires a reason and expected version, plus the current live epoch
if a lease exists. It produces a terminal item and removes its lease, without
reverting code or deleting a worktree.

Agents SHOULD supply reasons for all transitions, even where the interface
allows an empty reason.

## 7. Concept authoring and admission

### 7.1 Concept workflow

A concept is a human-owned Markdown description of intended engineering
behavior, not a work queue. It SHOULD state:

- Problem, desired outcome, and relevant stable context.
- Decisions and their rationale.
- Boundaries and invariants.
- Interfaces and assumptions where they affect implementation.
- Observable acceptance and stable evidence pointers.
- Unresolved decisions that genuinely block implementation.

Do not require empty template sections, copied source inventories, a detailed
implementation plan, runtime values, or a transcript.

Create a draft in the configured context directory. Creating or fingerprinting
a draft MUST NOT propose, admit, claim, or start work. Draft status MUST NOT be
silently promoted to approval.

Changing approved design or acceptance boundaries requires explicit human
approval. An explicit implementation request can authorize starting unchanged,
already bounded concept work without a second review turn.

### 7.2 Concept path and hash

Concept references MUST be relative files inside the configured context
directory. Reject absolute paths, traversal, missing files, and paths that,
after resolving symbolic links, lie outside the repository or context directory.

Hash the file's exact bytes with SHA-256. Do not normalize Markdown, encoding,
whitespace, or line endings before hashing.

Return the normalized reference and hash from `concept-fingerprint` without
creating runtime state.

### 7.3 Preview and confirmation

`concept-proposal` without confirmation is read-only. It MUST validate and
return:

- Optional requested item ID, objective, risk, and maximum attempts.
- Sorted/deduplicated path prefixes, effects, and dependencies.
- Concept reference and byte hash.
- Unique ordinary acceptance criteria and normalized external declarations.
- Current worktree baseline digest and pre-existing paths.
- A proposal fingerprint binding all of these fields.

Concept work MUST declare explicit nonempty allowed paths and at least one
ordinary or external acceptance criterion.

Criterion names MUST be unique, nonblank strings. `external:` is reserved.
Use deterministic canonical serialization and SHA-256 for the proposal
fingerprint. An implementation MAY choose its own canonical representation but
MUST specify it and use it identically for preview and confirmation.

Confirmation repeats the same contract with `--confirm <fingerprint>`.
Recompute the proposal from current files and arguments. If any bound field,
concept bytes, or worktree state changed, reject confirmation and require a new
preview.

Initialize the workspace before previewing: creating configuration or an ignore
file afterward can legitimately change the baseline and invalidate confirmation.

Successful confirmation proposes, readies, admits, claims, and starts one item.
It does not grant push, merge, or any other effect approval.

### 7.4 Ordinary proposal and readiness

`propose` creates a non-concept item in `proposed` state. Concept-linked work
uses direct start or the preview/confirmation route instead.

The `ready` query lists proposed or ready items whose dependencies are all
completed. It does not change state or admit work.

Admission requires state `ready`, all dependencies completed, and no previous
contract. Admission:

1. Freezes the contract and required acceptance criteria.
2. Appends a criterion `external:<name>` for every declared external result.
3. Pins and rechecks the concept hash, if present.
4. Captures the complete admission baseline and its digest.
5. Records the pre-existing paths at admission.
6. Records control-file fingerprints and the draft allowance.
7. Allocates zero delegated calls in restricted mode, or the full configured
   `max_calls` in orchestrated mode.

There MUST NOT be a per-item flag that silently lowers the configured call
budget. The coordinator can simply choose to use fewer calls.

Confirmed concept criteria cannot be replaced at later admission. Repeated
admission MUST fail rather than updating the existing contract.

### 7.5 Empty scope and evidence

For non-concept work, allowed paths and required criteria may be empty. Empty
paths mean **unrestricted repository scope**, not no-write access. Empty
criteria mean there is no evidence requirement.

Every view of an admitted item MUST report this through `contract_mode`, so an
unrestricted or evidence-free contract is never mistaken for a bounded one.
Concept work always requires both (section 7.3). Normal agent workflows SHOULD
always bound paths and acceptance.

## 8. Ownership, leases, and attempts

`claim` MUST:

1. Require an admitted item in ready or paused state.
2. Reject an existing unexpired lease, even for the same owner label.
3. Reject when `attempts >= max_attempts`.
4. Use the writer path associated with the durable baseline.
5. On the first claim, verify that the worktree still exactly matches admission.
6. Create a new unpredictable epoch and expiry, record current HEAD as
   claim-time `base_sha`, increment attempts and version, and enter leased state.

These checks and updates MUST be atomic. Reclaiming work MUST NOT replace the
admission baseline with the current dirty worktree.

Claim cannot silently switch the writer path. Use the explicit worktree
operation for an eligible untouched item.

An active lease requires an exact epoch match and `expires_at > current_time`.
Expired epochs MUST NOT be revived by heartbeat, evidence recording, or any
other authenticated operation.

Every successful epoch-authenticated operation SHOULD extend expiry to at least
`now + ttl_seconds`. Read-only inspection does not renew it.

Default claim and heartbeat TTL is 3,600 seconds. A TTL of 0 is accepted and
produces an already-expired lease, which cannot then be renewed. Negative TTLs
MUST be rejected.

Heartbeat explicitly sets the renewed TTL. Long verification operations extend
the lease before execution to cover their configured timeout, then authenticate
again before writing results. If the epoch expires or changes during execution,
do not record an authorized success.

The engine guarantees one owner **per work item**, not one writer per checkout.
Two different items can otherwise interfere in the same checkout; isolation or
host policy must address that.

Expiry fences engine operations; it does not stop an editor, old worker, or
subprocess from writing files outside the engine. Lease expiry uses a trusted
local wall clock; command durations SHOULD use a monotonic clock.

## 9. Admission baseline, scope, and fingerprints

This is the most important part to implement accurately. Comparing only
`git diff HEAD`, or only filenames, is insufficient.

### 9.1 Snapshot contents

A snapshot MUST include:

| Element | Required observation |
| --- | --- |
| Writer identity | Resolved absolute checkout path |
| Commit | Current HEAD |
| Index | Every tracked path and all index-stage entries: mode, object ID, stage |
| Worktree | State of every index path and every nonignored untracked path |
| Pre-existing paths | Paths dirty relative to snapshot HEAD, staged paths, and untracked paths |

For worktree objects, distinguish:

- Missing path.
- Regular file: SHA-256 of bytes and permission bits.
- Symbolic link: hash of the link target bytes, not the target file contents.
- Directory, including a submodule directory: directory identity/type only.

Do not recursively interpret a submodule's files as part of the parent Git
root. Its staged gitlink is represented by the parent's index.

Exclude nontracked runtime state. Respect Git's ignored-untracked rules.
Tracked files still participate even if an ignore pattern would match them.
Ignored build outputs are not made visible merely by this system.

Use NUL-delimited Git output and preserve unusual path names. Hash file bytes
incrementally. Do not follow symlinks when fingerprinting file objects.
Reject unsupported objects, unreadable files, escaping parent symlinks, or
files that disappear in a way that makes the snapshot unmeasurable.

The snapshot MUST contain fingerprints, not copies of source contents.

### 9.2 Canonical hashing

One sufficient portable convention is sorted-key JSON without insignificant
whitespace, encoded as UTF-8, followed by SHA-256.

Define:

```text
B = immutable writer admission snapshot
C = freshly observed writer snapshot
H(S) = SHA256(canonical_UTF8_JSON(S))
repository_state_fingerprint = SHA256(ASCII(H(B) + H(C)))
```

An implementation MUST document its canonical form, including dictionary
ordering, path encoding, integer representation, and treatment of absent fields,
so that digests are reproducible.

Store and verify the baseline's own digest. A corrupt or missing required
baseline MUST NOT be reinterpreted as a clean checkout.

### 9.3 Delta calculation

For every path in the union of baseline and current path maps, compare its
entire index/worktree state. The post-admission delta is the set whose states
differ.

This catches:

- Staged changes even when worktree bytes stay the same.
- Unstaged changes, additions, deletions, and both sides of a rename.
- Untracked content changes, not merely new filenames.
- Permission and symlink-target changes.
- Changes committed after admission.

Unchanged pre-existing edits are visible but are not attributed to the item.
Changing them after admission counts against scope, including pre-existing
untracked notes.

This is **net-state** measurement. A temporary edit restored exactly to its
baseline cannot be discovered afterward from the net delta alone.

The admission commit MUST remain an ancestor of current HEAD. Reject unavailable
ancestry, history rewrites that violate this condition, or a different resolved
writer path instead of falling back to an incomplete diff.

### 9.4 Allowed paths

A prefix matches only the exact path or a descendant separated by `/`.
`src` matches `src` and `src/module.py`, not `src2/module.py`.
Trailing slashes do not change this rule. Paths are not glob patterns.

Every contract, concept or ordinary, MUST reject unsafe prefixes: absolute
paths, `.`, and any `..` component.

For bounded contracts, a delta path is a violation unless:

- It falls under an allowed prefix.
- It is the admitted concept and its bytes still match the pinned hash.
- It qualifies for new draft capture.
- It is an unchanged initialization control file whose admitted fingerprint is
  still identical.

Configuration and ignore-file edits are not generally exempt. The scope rule
detects them as violations when outside allowed paths.

Admission freezes the execution profile, call limit, model allow-list, and
gated effects. `dispatch` and `check-effect` MUST use the frozen values, not the
live configuration. Changing the configuration file does not refuse further
mutations; it is reported as drift (section 16.3). There is no tamper-proof lock
against direct edits.

### 9.5 Draft capture

A file qualifies only when it is under the frozen `context_dir`, was absent
from the admission baseline, and is not the admitted concept.

A file already present at admission does not become a new draft merely because
it was untracked. Editing it requires normal scope permission.

The exemption permits capturing a future idea without expanding the active
item. It does not approve that idea or schedule its implementation.

The exemption is path-based, not a Markdown parser: it recognizes any new path
under the context directory. Agent protocol MUST limit its use to genuine
human-context drafts.

### 9.6 Concept drift

Independently of allowed paths, rehash the admitted concept before completion.
Missing or changed bytes are a blocker even if the concept is inside an
otherwise allowed prefix. Never edit the concept to match an implementation
and then treat the admitted contract as unchanged.

### 9.7 Whole-worktree evidence

Scope is about the item's delta; evidence observes the **whole writer state**.
Thus changing an allowed file, staging a file, making a commit, or capturing a
scope-exempt draft can invalidate earlier evidence.

Do not bind tests merely to changed filenames or exclude pre-existing edits
from their observed subject.

## 10. Delegation protocol and accounting

Only a live lease-owning coordinator can reserve or settle a call.

`dispatch` requires:

- Orchestrated profile.
- Running lifecycle state.
- Live matching epoch.
- Nonblank declared model and host-adapter names.
- An allowed exact model name when an allow-list exists.
- At least one unreserved call.

Atomically increment `reserved`, append a dispatch event with model and adapter,
and return the remaining count. Only then may the adapter invoke one worker.
An engine rejection MUST prevent that invocation.

`settle-dispatch` requires a live epoch and an outstanding reserved call.
Increment `used` exactly once and record `completed` or `failed`.
Settlement cannot increase the reservation, refund it, or exceed it.

A crash after reservation can leave `reserved > used`. Treat the reservation
as charged; do not restore budget because a transcript or worker response is
missing. Any recovery settlement must be explicit.

The system MUST NOT claim that these counts measure model cost, actual host
invocations bypassing the protocol, or which model really executed.

### 10.1 Sequential workers

The coordinator MUST invoke at most one worker at a time, wait for its result,
settle the call, and evaluate the result before the next assignment.

Workers MUST NOT invoke workers or operate engine state. This is protocol-only
unless a host independently restricts those capabilities.

The accounting can represent several outstanding reservations; it does not
itself prove sequential execution. Do not advertise SQLite counters as a host
concurrency limit.

A reserved but unsettled call MUST block completion with `outstanding_dispatch`.
Reconciling a reservation after a crash is always an explicit `settle-dispatch`.

There is no persisted child-task queue, scheduler, recursion budget, or mandatory
commit per assignment. Direct foreground work is always a valid choice.

## 11. Evidence and verification

### 11.1 Freshness

Evidence is associated with:

```text
input_fingerprint = current lease's claim-time base_sha
diff_fingerprint = current repository_state_fingerprint
```

On new evidence insertion and before completion, invalidate stored valid
evidence whose subject fingerprints do not match the current pair.

The durable admission baseline is not reset on reclaim, but claim-time HEAD
can change. Old evidence may therefore need to be rerun after reclaim.

The evidence summary MUST return `required`, `satisfied`, `missing`, and source
per satisfied criterion. A criterion is satisfied by at least one valid passing
record of an acceptable source/kind.

For ordinary criteria, both measured and caller-reported passes can satisfy the
core contract. If both exist, report the source as `engine_measured`; a later
assertion must not relabel measured evidence as merely reported.

Ordinary evidence is existential: a new failure at the same fingerprints does
not supersede an earlier valid pass. External criteria differ: the latest
measurement wins (section 12.2).

### 11.2 Measured command verification

`verify --criterion <name> -- <argv...>` MUST:

1. Authenticate and extend the live lease for the command timeout.
2. Resolve the assigned writer checkout and optional command directory.
3. Execute the supplied argument vector without an implicit shell.
4. Observe exit status, duration, timeout, and output.
5. Authenticate again, calculate current fingerprints, invalidate stale
   evidence, and insert one measured result transactionally.

Defaults: `kind = command`, timeout 1,800 seconds, working directory is the
leased writer root. A `--cwd` MUST be resolved through symbolic links and stay
inside the writer checkout.

Exit code zero means `pass`; any nonzero exit or timeout means `fail`.
A timeout has `timed_out = true` and no normal exit code.
Failure to launch the executable is a command-contract error, not a fabricated
test result.

Record a display-safe command description, optional artifact pointer, exit code,
elapsed seconds, and a bounded output tail. The tail is the final 4,096 decoded
characters of stdout followed by stderr, decoding undecodable bytes with
replacement. This is not interleaved stream chronology.

The CLI operation succeeds when it successfully **records** a failed
verification. The caller must inspect `outcome`; process exit zero does not mean
the acceptance criterion passed.

The core binding is to state observed after command execution, allowing tools
that generate or modify files. It does not prove an immutable input throughout
the test. No filesystem sandbox or semantic test audit is implied.

An explicitly requested shell can be passed as an executable, but arbitrary
verification commands can have effects. All host-side effect policy still
applies; the engine does not reliably classify shell contents.

### 11.3 Caller-reported evidence

Use `record-evidence` for reviews or artifact assertions that the engine cannot
execute. Require criterion, kind, and `pass`/`fail`. Optional fields are command
description, artifact reference, and commit SHA.

Batch input is a nonempty JSON array read from a file or stdin:

```json
[
  {"criterion": "design-review", "kind": "review", "outcome": "pass"},
  {
    "criterion": "report",
    "kind": "artifact",
    "outcome": "pass",
    "artifact": "artifacts/report.html"
  }
]
```

Reject unknown fields, invalid outcomes, missing required strings, and attempts
to combine a batch with single-record criterion/kind/outcome flags. Validate
the entire batch and insert all rows in one transaction.

Caller-reported data cannot choose `engine_measured` as its source, and
cannot supply its own fingerprints: the engine binds every record to the
current repository state, exactly as for measured results.

### 11.4 Reserved external criteria

Ordinary `verify` and `record-evidence`, including batches, MUST reject
`external:<name>` criteria. Ordinary verification MUST also reject the
`external_result` kind.

Only the dedicated external verification path can create evidence satisfying
declared external outcomes. Imported or malformed rows MUST NOT satisfy those
criteria unless their source, kind, SHA, and freshness are appropriate.

## 12. External Git result verification

This is additional **result evidence**, not scope enforcement in another
repository.

### 12.1 Declaration

Declare results before admission, with one descriptor per unique result:

```json
{
  "name": "library",
  "remote": "https://code.example/library.git",
  "ref": "refs/heads/main",
  "gitlink": "vendor/library"
}
```

`gitlink` is optional. The future produced SHA is not part of the declaration.

Validate:

- Names match `[A-Za-z0-9][A-Za-z0-9_.-]*` and are unique.
- Only `name`, `remote`, `ref`, and optional `gitlink` fields are accepted.
- Ref is a full, Git-valid ref beginning with `refs/`.
- Gitlink is a safe repository-relative path.
- Remote is a URL or Git path, not an option or command.
- URLs contain no embedded passwords, query strings, fragments, or HTTP
  username/token credentials.
- If supported, SSH usernames are simple transport usernames, not credentials.
- Supported schemes are explicitly enumerated, such as HTTPS, HTTP, SSH, Git,
  and file; reject command-backed remote transports.

Relative local remotes resolve against the controller repository.
Sort descriptors by name for deterministic hashing and output.

### 12.2 Measurement

`verify-external` receives a declared name and a complete 40- or 64-hex-character
Git object ID, normalized to lowercase.

Use a temporary bare repository of the matching object format:

1. Disable interactive Git prompts and command-backed external transports.
2. Fetch only the declared ref without modifying any working checkout.
3. Verify that the supplied object exists and is a commit.
4. Verify that it is an ancestor of or equal to the fetched ref tip.
5. If a gitlink was declared, inspect the **parent repository index**.

A matching parent gitlink requires exactly a stage-zero entry with mode
`160000` and the declared produced SHA. Merely changing the submodule
checkout's HEAD is not enough.

A later descendant on the remote ref is acceptable. Requiring equality with
the current remote tip would incorrectly reject a valid published result.

Bound each Git/network subprocess, with 120 seconds as a suitable default.
Clean up the temporary repository. Report inaccessible remote refs, missing or
unreachable commits, mismatched gitlinks, and timeouts as failed measured
evidence with a reason.

A new external measurement supersedes the prior valid evidence for that
criterion. All declared external results are required.

### 12.3 Completion-time recheck

Completion MUST repeat live remote ancestry and optional gitlink checks for
current passing external evidence. If the remote was rewritten, disappeared,
or no longer contains the produced commit, invalidate the old pass, preserve a
measured failure reason, and block completion.

No automatic push occurs. Publishing still requires its own human/host effect
approval.

The checkout where the commit was produced is irrelevant: it can be a sibling
repository or submodule checkout. The engine does not inspect that external
diff, lease that checkout, validate its tests, or prevent external edits.

## 13. Effect approval

An effect approval matches **one work item and one exact effect identifier**.
It records an approver label and starts unconsumed.

`approve` records a human/host-mediated assertion. It does not authenticate the
person named by a command-line argument. The adapter MUST obtain real approval
before calling it.

`check-effect` returns:

```json
{"effect": "push", "gated": true, "authorized": false}
```

For a mandatory or configured gate, authorization requires a matching unused
approval. For a nongated effect, the result can be authorized without an
approval. A nonconsuming check is only a preview.

With `--consume`, use one matching approval and mark it consumed. Selection and
consumption MUST be one conditional update inside a write transaction, so a
single approval is consumed exactly once even under concurrent checks. Never
consume an approval for an effect that is not gated. Check immediately before
the action, not once for an entire session. A failed external action does not
implicitly restore the consumed approval.

`check-effect` on an unknown item is an error, not an unauthorized result.

The required-effects list in the contract is not permission to act, and
completion is not approval to publish.

This core approval is not bound to an exact command, target URL, commit, or
expiration time. More granular approval capabilities are optional extensions.
The default mechanism MUST be described as protocol-only unless the host
actually intercepts and mediates every relevant tool call.

Hooks or telemetry alone do not establish that mediation.

## 14. Optional writer worktree

`create-worktree` requires a live epoch and:

- No previously registered writer worktree for the item.
- A destination path that does not already exist.
- No post-admission changes in the current writer state.
- No concept drift.
- For concept work, the pinned concept's bytes must already exist identically
  in the admission commit.

Do not silently copy uncommitted intent or pre-existing user edits into the
new checkout.

Create a new branch at the lease base commit using Git worktree support.
Register its absolute path, branch, base, and work ID; associate it with the
lease. Capture a separate baseline for the isolated writer.

Preserve the admission context. Inspection MUST distinguish:

- `preexisting_paths`: the current writer baseline's prior edits.
- `original_preexisting_paths`: earlier edits in the controller checkout at
  admission.

After pause, release, and reclaim, continue in the same registered writer
checkout. Do not recapture its current state as a fresh clean baseline.

Git worktree creation and a SQLite transaction are not one atomic operation.
If creation succeeds but registration fails, report the exact surviving path and
branch rather than pretending nothing happened or deleting user-visible state
automatically.

Worktrees are optional and are never deleted by ordinary completion or doctor
repair.

## 15. Convenience start and completion

### 15.1 Direct start

`start` combines proposal, ready transition, admission, claim, and transition to
running. It introduces no separate lifecycle concept.

For concept work it MUST bind explicit paths, acceptance, concept bytes, and
the current baseline, using the same validations as the preview route.
An explicit human implementation request is sufficient; no duplicate review
turn is added if approved boundaries remain unchanged.

The engine cannot attest to that human request. The workflow/adapter must
distinguish it from an unapproved draft or the agent's own inference.

`start --approval-ref` stores the approval reference with the contract.

Non-concept start uses the ordinary proposal contract. Both routes return ID,
state, version, epoch, base SHA, risk, paths, required evidence, external results,
concept hash, draft allowance, baseline digest, pre-existing paths, allocated
budget, and delegation readiness.

Start is a sequence of committed operations, not an all-or-nothing
supertransaction. For example, an incomplete dependency can
leave a ready, unadmitted proposal containing the confirmed concept contract.
Do not execute outside the engine after such a failure. Inspect the existing
item and resume admission when the prerequisite is satisfied.

Do not silently change its confirmed criteria or create duplicate replacement
items merely to bypass the blocker.

### 15.2 Guarded completion

`complete` requires:

- A live matching epoch.
- The expected current item version.
- State `verification`.
- An admitted contract.
- Measurable, valid baseline and current writer state.
- No concept drift.
- No out-of-scope delta.
- Current passing evidence for every required criterion.
- Passing live rechecks for declared external outcomes.

Validate the version before doing potentially expensive external verification.
Reauthenticate the lease, version, and repository fingerprint when settling.
If the repository changed during completion, require verification again.

On success, set completed, increment version, append a completion event, and
remove the lease in one transaction. Do not create a commit or perform a gated
effect.

An ordinary failure MUST NOT produce completed state. A measured external
recheck failure MAY persist evidence invalidation even though completion fails.

### 15.3 Finish

`finish` is a caller-friendly completion wrapper for leased, running, or
verification states.

It refreshes evidence validity, checks scope and concept, and rechecks external
results. Expected engineering blockers return a successful JSON response such
as:

```json
{
  "id": "work-example",
  "completed": false,
  "state": "running",
  "version": 4,
  "risk": "medium",
  "evidence": {
    "required": ["unit-tests"],
    "satisfied": [],
    "missing": ["unit-tests"],
    "sources": {}
  },
  "blockers": [
    {"code": "evidence_missing", "detail": "unit-tests"}
  ]
}
```

On blockers it does not advance toward completion. Invalid epochs, stale
contracts, unmeasurable state, and malformed input can still be command errors.

If checks pass, advance leased to running when necessary, running to
verification when necessary, and invoke guarded completion.

All risk levels use the same completion rules. There is no automatic
high-risk final review, `approved_by` parameter, or extra human approval merely
because risk is high. Human approval of a changed boundary and effect gates
remain independent requirements.

## 16. Inspection, diagnostics, and recovery

### 16.1 Item inspection

`show` requires only an item ID, not an epoch. Return:

- Objective, state, version, risk, attempts and limit, admission status.
- Paths, proposed criteria, external declarations, concept reference and hash.
- Optional approval reference.
- Owner, expiry status, nonnegative TTL remaining, base SHA, and writer path.
- Budget limit/reserved/used/accounting source and delegation readiness.
- Required, satisfied, and missing evidence with source information.
- Scope status and details, including baseline mode and both pre-existing lists.
- Blockers, graph-eligible transitions, and quarantined follow-ups.

Do not renew leases, claim work, repair state, fetch remotes, or run verification
commands as a side effect of inspection.

Scope statuses MUST distinguish:

| Status | Meaning |
| --- | --- |
| `unknown` | Insufficient contract/lease context for a measurement |
| `unavailable` | Measurement attempted but failed; include the failure reason |
| `clean` | Measured delta has no violations or concept drift |
| `violated` | Measurement found scope or concept problems |

An empty violation array accompanying unavailable measurement MUST NOT be
reported as proof of clean scope.

Evidence summaries in `show` and `status` use stored validity flags; inspection
alone does not invalidate stale rows. They MUST be labeled `"basis": "recorded"`
so that a recorded pass is not mistaken for a completion guarantee. `finish`
and `complete` always re-evaluate freshness.

### 16.2 Status

Compact status is the default. Return profile, context directory, configured
delegation readiness, counts by state, nonterminal item summaries, active/stale
lease information, named blockers, and the count of proposed follow-ups.

Do not include every evidence row, the full configuration, or long enforcement
text in compact output. `status --full` adds those and worktree records.

Useful blocker codes include `dependency_incomplete`, `lease_expired`,
`lease_missing`, `attempts_exhausted`, `evidence_missing`, and
`external_result_failed`. Scope has its own structured measurement.

At workspace level, delegation readiness describes configuration; obtain an
item's actual remaining calls from `show`.

### 16.3 Doctor

Doctor reports structured findings:

```json
{
  "code": "expired_lease",
  "severity": "error",
  "work_id": "work-example",
  "detail": "The lease expired; running work can recover to paused."
}
```

Return authoritative boolean `ok`, findings, configuration when usable,
delegation readiness, and an enforcement report. `ok` is false when any
unresolved error finding exists; do not infer it from prose.

Minimum diagnostics:

- Missing, malformed, or unsupported configuration.
- Unsupported database schema and SQLite integrity failures.
- Expired leases and active lifecycle states with no lease.
- Dependency cycles.
- Missing registered writer worktrees.
- Baseline, writer identity, or ancestry failures preventing scope measurement.
- Scope violations and concept drift for measurable active work.
- Honest notice of protocol-only effects and delegation rules.

Doctor MUST also report stored evidence that no longer matches the current
repository state (`stale_evidence`), configuration or ignore-file changes since
admission (`control_file_drift`), and unsettled reservations
(`outstanding_dispatch`). Doctor MUST NOT imply it reran tests or checked every
remote just because it read evidence rows.

Suggested commands MUST use the launcher's absolute path plus `--repo`, so they
run exactly as printed.

### 16.4 Explicit metadata repair

A read-only doctor response SHOULD include `suggested_repair` with an exact
command and confirmation token when repairable issues exist.

`doctor --fix` requires that token and explicit human confirmation. The token
is derived from the current findings, so a token from an older diagnosis no
longer matches. It is an acknowledgement mechanism, not proof of identity or
authority.

Only deterministic lease/lifecycle metadata repair is allowed:

| Ownerless or expired state | Recovered state |
| --- | --- |
| `leased` | `ready` |
| `running` | `paused` |
| `verification` | `paused` |

Under the repair transaction, re-read each item's state, lease, and expiry, and
skip any item renewed, reclaimed, or changed since the diagnosis. A stale
diagnosis MUST NOT revoke a newly live owner.

Remove the expired lease, increment lifecycle version where changed, and append
repair events. Do not reset attempts, call counters, the contract, or baseline.
Repair does not grant a new epoch; a later claim does.

Doctor MUST NOT delete worktrees, alter code or concepts, widen scope, consume
approvals, rerun commands, publish, or perform external destructive effects.

The repair response is a fresh diagnosis taken after repair, plus `repaired`
(items changed) and `skipped` (items left alone because they changed since the
diagnosis).

### 16.5 Schema mismatch

Schema migration is not provided. Unsupported schemas MUST fail closed with a
precise recovery explanation.

Distinguish a stale database from stale configuration. Offer a human-confirmed
reset of the resolved runtime directory only, which moves it aside as a backup
rather than deleting it. Never automatically delete state or rewrite schema
metadata to make incompatible files appear compatible.

## 17. Export and import

Provide JSON Lines checkpoint export plus optional event-only export.
The first line is a header:

```json
{"record_type":"rivet-export","schema_version":1,"mode":"checkpoint","exported_at":"<ISO-8601 UTC>"}
```

`mode` is `checkpoint` or `events`. Each subsequent record identifies a logical
table and its row data.

A checkpoint MUST contain work items, dependencies, contracts, budgets, leases,
evidence, approvals, follow-ups, baseline events, general events, and
writer-worktree registration. Events MUST appear in their local sequence order.

Export defaults to `.rivet/runtime/work.jsonl`. Relative export and import paths
resolve from the repository root.

`--since <timestamp>` exports only events newer than the supplied bound.
An event-only stream is audit material, not a complete canonical state snapshot
and not an instruction to replay arbitrary state transitions.

Import MUST:

1. Require the recognized header, supported schema, and known mode.
2. Accept only known row types, logical tables, and columns.
3. Require row primary keys.
4. Insert missing rows transactionally with foreign-key validation.
5. Skip a row already present with exactly identical data.
6. Reject a different row with the same primary key.
7. Reject deletion records and ambiguous overwrite/merge semantics.
8. Roll back the import on conflict or invalid data.

Do not import arbitrary SQL identifiers or execute payloads.
Event-only mode cannot change canonical work-item state.

Import does not transfer Git objects, files, configuration, actual external
approvals, or a writer checkout. Absolute paths, live-looking lease metadata,
and fingerprints can refer to the exporting machine. The recipient MUST
diagnose that state before continuing, not assume that database transfer grants
a valid working environment.

Preserve event order where it determines the selected writer baseline.

## 18. CLI surface

Every normal command emits one JSON value on stdout. A domain, filesystem, or
database error emits at least `{"error":"human-readable reason","code":"<stable code>"}`
on stderr and exits nonzero. The `code` is stable and machine-readable. Argument-parser help and syntax errors may use conventional CLI
output and exit codes.

Do not mix subprocess output into JSON stdout. Capture verification output and
put the bounded tail in its result.

`verify` failure results, `finish` blockers, negative `check-effect` results,
scope violations returned by `check-scope`, and doctor findings are successful
measurements: consumers MUST inspect their JSON booleans/outcomes, not just
the process exit code.

The following command contract is sufficient. Angle-bracket arguments are
required; square brackets indicate optional arguments. List-valued options
accept multiple values; external-result and worker-model options repeat.

| Command | Arguments | Principal result |
| --- | --- | --- |
| `init` | None | Initialized workspace and effective configuration |
| `config` | None | Root, configuration, enforcement, delegation |
| `config-init` | `--profile <profile> [--context-dir path] [--max-calls n] [--worker-model name] [--replace] [--dry-run]` | Proposed or persisted configuration and action |
| `concept-fingerprint` | `--concept-ref <path>` | Normalized reference and SHA-256 |
| `concept-proposal` | `--concept-ref <path> --objective <text> --allowed-paths <prefixes> [proposal options] [--confirm hash] [--owner owner] [--ttl-seconds n] [--approval-ref ref]` | Read-only bound preview or started item |
| `propose` | `--objective <text> [--id id] [--risk risk] [--allowed-paths prefixes] [--effects names] [--external-result json] [--depends-on ids] [--max-attempts n]` | Proposed item ID and state |
| `ready` | None | Dependency-unblocked proposed/ready items |
| `admit` | `--id <id> [--required-evidence names] [--approval-ref ref] [--concept-fingerprint hash] [--design-fingerprint hash]` | Frozen contract summary, baseline, full budget, delegation |
| `start` | `--objective <text> [--id id] [--owner owner] [--concept-ref path] [proposal options] [--approval-ref ref] [--ttl-seconds n]` | Running item including epoch and version |
| `claim` | `--id <id> --owner <owner> [--worktree path] [--ttl-seconds n]` | Leased item, new epoch, version, base SHA |
| `heartbeat` | `--id <id> --epoch <epoch> [--ttl-seconds n]` | Renewal confirmation |
| `transition` | `--id <id> --to <state> --expected-version <n> [--epoch epoch] [--reason text]` | New state and version |
| `pause` | `--id <id> --epoch <epoch> --expected-version <n> --reason <text>` | Paused state; lease retained |
| `release` | `--id <id> --epoch <epoch> --expected-version <n> [--reason text]` | Ready state; lease removed |
| `abandon` | `--id <id> --expected-version <n> --reason <text> [--epoch epoch]` | Terminal abandoned state |
| `dispatch` | `--id <id> --epoch <epoch> --model <name> --adapter <name>` | Reservation and remaining calls |
| `settle-dispatch` | `--id <id> --epoch <epoch> --outcome <completed-or-failed>` | Settled and reserved counts |
| `verify` | `--id <id> --epoch <epoch> --criterion <name> [--kind kind] [--artifact ref] [--timeout-seconds n] [--cwd path] -- <argv...>` | Measured result, exit/timeout, duration, fingerprint, output tail |
| `record-evidence` | `--id <id> --epoch <epoch>` plus a single record or `--batch <file-or-dash>` | Recorded IDs, count, asserted source, invalidation count |
| `verify-external` | `--id <id> --epoch <epoch> --name <declared-name> --commit-sha <full-sha>` | Measured external result and reason |
| `check-scope` | `--id <id> --epoch <epoch>` | Validity, violations, concept drift, baseline and path summaries |
| `complete` | `--id <id> --epoch <epoch> --expected-version <n>` | Guarded completed state and version |
| `finish` | `--id <id> --epoch <epoch>` | Completion or structured blockers |
| `show` | `--id <id>` | One item's actionable full view |
| `status` | `[--full]` | Compact or detailed workspace state |
| `doctor` | `[--fix --confirm token]` | Findings, enforcement, optional suggested repair or repairs |
| `approve` | `--id <id> --effect <name> --approver <label>` | Approval ID |
| `check-effect` | `--id <id> --effect <name> [--consume]` | Gated and authorized booleans |
| `propose-followup` | `--id <parent-id> --epoch <epoch> --objective <text>` | Quarantined proposed follow-up |
| `create-worktree` | `--id <id> --epoch <epoch> --path <path> [--branch name]` | Registered writer path, branch, base SHA |
| `export` | `[--out path] [--since timestamp]` | Export count, location, mode |
| `import` | `--in <path>` | Imported/skipped counts and mode |

Shared proposal options are `--id`, `--risk`, `--effects`, `--evidence`,
`--external-result`, `--depends-on`, and `--max-attempts`, plus allowed paths
where not already required. Concept-proposal requires its paths explicitly;
start requires them when concept-linked.

Single asserted evidence uses `--criterion`, `--kind`, `--outcome`, optional
`--command`, `--artifact`, and `--commit-sha`.

Do not add generic reserve/settle-budget commands, host credit-limit flags,
automatic final approvers, or per-item delegated-call overrides.

## 19. Host workflows and agent behavior

These are behavioral requirements, not reusable prompt text.

### 19.1 Configuration workflow

Read current settings if present. Collect profile, context location, maximum
calls, and optional exact model restrictions. Preview the complete candidate,
explain actual enforcement, obtain confirmation, write it through the engine,
initialize runtime state, and report persisted settings.

Do not invent a static model list or treat an empty list as prohibition.
Explain that financial limits belong to the host.

### 19.2 Concept workflow

Read configured context location, write only human-owned intent, preserve draft
status, and fingerprint the resulting document. Do not admit work as a side
effect of writing a concept.

### 19.3 Work workflow

For explicit implementation, establish objective, paths, acceptance, effects,
and external results, then start one item. Clarify genuinely missing boundaries;
do not add a duplicate approval for an already explicit unchanged request.

For existing work, inspect state and claim or recover as appropriate.
Use engine commands for every state mutation. Do not edit SQLite directly,
bypass a failed admission, widen a contract, or silently retry with weakened
requirements.

Prefer measured verification for runnable criteria. Use asserted evidence only
where its limitations are appropriate. Finish or explicitly pause/stop with
blockers.

### 19.4 Status workflow

Use read-only engine and Git facts. Report ownership, TTL, base, writer location,
attempts, remaining delegated calls, scope, concept drift, and acceptance
blockers. Do not create progress from a transcript or mutate state to make the
report look better.

### 19.5 Diagnostics workflow

Report the engine's structured findings and authoritative `ok`. Present the
exact suggested repair, obtain approval, and execute only that repair.
Separate measured guarantees from protocol obligations.

### 19.6 Coordinator role

The coordinator is the sole lease holder and lifecycle owner for its item.
It may work directly or delegate under the configured profile.

Before planning delegation, inspect actual remaining calls and host capability.
Before each worker launch reserve a call; after it returns settle the call.
Retain responsibility for changes, integration, validation, evidence, and final
completion.

Each assignment MUST contain:

- Parent objective and concrete bounded assignment.
- Writer checkout and permitted paths.
- Relevant context and constraints.
- Expected return information and required validation.
- Stop conditions and explicit prohibitions.

A worker result is input to coordinator judgment, not automatic acceptance.
Do not require a commit per assignment or automatic commit of worker output.

A reviewer, if used, is read-only, cannot delegate, and cannot schedule fixes.
Out-of-scope discoveries are proposed follow-ups, not automatic new work.

### 19.7 Worker role

A worker performs exactly one assigned implementation or investigation.
It reads only necessary context, preserves existing user changes, stays inside
assigned paths, runs the appropriate narrow validation, and returns:

Changed paths, concise outcome, verification performed and results, uncertainties,
blockers, and follow-up discoveries.

Workers MUST NOT claim or renew leases, invoke engine commands, record acceptance,
create items, settle calls, delegate, commit, push, merge, publish, or perform
external/destructive/expensive effects.

### 19.8 Human redirection

The latest human request outranks the active item. Persistence is permission
to resume, not an obligation to finish first.

When redirected, pause the current item if its lease is live. If expired, report
the stale state instead of reusing the epoch. Address the new request at its
requested size, and do not resume old work without permission.

Answering a question or writing a draft can be a complete response even while
an implementation item exists.

Planning notes and transcripts can be session-local. Do not add a persistent
repository plan or child-task queue unless the human explicitly requests it.

## 20. Enforcement limits

Do not claim stronger guarantees from agent instructions than the host actually
enforces. A local CLI accessible to the same process as arbitrary shell tools is
not an isolation boundary.

Every admission record carries a baseline. A missing or corrupt baseline is an
error and MUST NOT be measured against some other reference such as claim-time
HEAD.

## 21. Conformance scenarios

Implement these as black-box tests using temporary Git repositories, real
subprocess invocations, and direct inspection of observable JSON.
Use fake clocks or test-only lease aging rather than long sleeps. External
tests can use local bare remotes; no public service or organizational account
is necessary.

### 21.1 Configuration and contract

| ID | Scenario and required observation |
| --- | --- |
| C01 | Initialize without configuration: restricted profile, zero calls, empty model list, mandatory gates |
| C02 | Initialize again: preserve configuration and existing ignore file |
| C03 | Preview orchestrated configuration: no files or runtime state created |
| C04 | Write orchestrated configuration: positive explicit limit, formatted JSON, context directory created |
| C05 | Reject unknown fields, booleans as limits, unsafe context paths, duplicate/blank models, and removed mandatory gates |
| C06 | Reject orchestrated zero/missing limit and restricted positive limit |
| C07 | Replace without flag fails; replacing while any nonterminal admitted item exists fails |
| C08 | An unadmitted proposal alone does not prevent replacement |
| C09 | Empty model allow-list permits an arbitrary declared host model; a nonempty list rejects every unlisted exact name |
| C10 | Admission allocates the entire configured call limit, not one call or a per-item override |
| C11 | Every incomplete dependency blocks admission; ready query is read-only |
| C12 | Re-admission cannot change objective, paths, effects, or acceptance |

### 21.2 Concepts and start

| ID | Scenario and required observation |
| --- | --- |
| C13 | Concept fingerprint equals SHA-256 of exact bytes; the command creates no work state |
| C14 | Reject missing, absolute, traversal, and resolved-outside-context concepts |
| C15 | Preview returns a contract fingerprint but creates no runtime database |
| C16 | Changing concept, scope, criteria, dependency, external declaration, or baseline invalidates confirmation |
| C17 | A matching confirmation starts exactly one running item with a pinned hash and live epoch |
| C18 | Direct explicit concept start works without preview or a duplicate review |
| C19 | Concept start with missing paths or all missing criteria fails before creating work |
| C20 | Incomplete dependency can leave a ready unadmitted confirmed proposal; later admission preserves its criteria |
| C21 | Ordinary propose cannot be used to bypass concept pinning |
| C22 | All risk levels can complete with current evidence without an automatic final approver |

### 21.3 Ownership and lifecycle

| ID | Scenario and required observation |
| --- | --- |
| C23 | Two concurrent claims of one admitted ready item produce exactly one owner |
| C24 | Claim without admission, in an ineligible state, or beyond max attempts fails |
| C25 | A wrong or expired epoch cannot transition, heartbeat, verify, reserve, or settle |
| C26 | A successful authenticated call renews a live lease; show/status do not |
| C27 | Stale expected version cannot mutate lifecycle or invalidate evidence through completion |
| C28 | First claim rejects changes after admission; reclaim preserves the admission baseline |
| C29 | Pause retains the lease; another owner cannot claim until release or valid recovery |
| C30 | Pause/release/reclaim changes the epoch, increases attempts, and preserves counters and contract |
| C31 | Abandon removes the lease without reverting code; terminal items cannot restart |
| C32 | Follow-up capture does not admit new work or widen the parent contract |

### 21.4 Scope

| ID | Scenario and required observation |
| --- | --- |
| C33 | Unchanged pre-existing staged, unstaged, and untracked edits outside scope are reported but not violations |
| C34 | Later index-only or content changes to those same paths are violations |
| C35 | Returning index and worktree exactly to the baseline removes the net violation |
| C36 | Allowed prefix `src` does not allow `src2`; a rename reports both source and destination |
| C37 | Committing an out-of-scope edit does not hide it |
| C38 | Detect deletions, executable-bit changes, and symlink-target changes |
| C39 | New genuine context draft is scope-exempt; a pre-existing untracked context note is not |
| C40 | Editing the admitted concept blocks completion even inside allowed paths |
| C41 | Missing/corrupt baseline, changed writer path, or unavailable ancestry produces unavailable/error, never clean |
| C42 | Runtime files do not pollute snapshots; ignored-untracked files are outside normal measurement |
| C43 | Control-file changes outside scope are violations rather than blanket initialization exemptions |
| C44 | Parent scope does not claim visibility into sibling or submodule source contents |

### 21.5 Evidence and completion

| ID | Scenario and required observation |
| --- | --- |
| C45 | A command exiting 3 is recorded as measured fail with exit code 3 and output tail |
| C46 | Exit zero produces a measured pass; timeout produces fail with timeout metadata |
| C47 | Nonexistent executable is a launch error, not an invented successful measurement |
| C48 | Verification runs in the assigned writer and extends/authenticates its lease |
| C49 | Any relevant tracked, staged, committed, or untracked-content change invalidates completion evidence |
| C50 | A new scope-exempt draft can still invalidate evidence because the whole worktree changed |
| C51 | Caller assertions remain caller-reported; a measured pass retains measured attribution alongside an assertion |
| C52 | A valid evidence batch inserts all records; an invalid batch inserts none |
| C53 | Ordinary evidence cannot forge an external criterion or measured source |
| C54 | Finish with missing criteria returns completed false and structured blockers without advancing state |
| C55 | Current evidence, unchanged concept, and in-scope delta permit completion and remove the lease |
| C56 | Changing the worktree during completion prevents settlement against the earlier fingerprint |
| C58 | Test the selected same-subject pass/fail supersession policy explicitly |

### 21.6 Delegation and effects

| ID | Scenario and required observation |
| --- | --- |
| C59 | Restricted profile rejects dispatch; orchestrated running item permits it |
| C60 | With limit 2, exactly two reservations succeed; a third fails even after successful settlement |
| C61 | Failed settlement outcome consumes a call just like completed; counters never refund |
| C62 | Settlement without outstanding reservation fails; used cannot exceed reserved |
| C63 | Item delegation availability becomes false at remaining zero |
| C64 | Configured gate without approval returns authorized false; approval for another item/effect does not match |
| C65 | Nonconsuming check preserves approval; consumption permits only the matching single use |
| C66 | A completed item does not imply permission to push or publish |
| C67 | Adapter tests prove one worker at a time, no recursive workers, and no launch after failed reservation |

### 21.7 External results

| ID | Scenario and required observation |
| --- | --- |
| C68 | Reject duplicate names, unknown fields, invalid refs, credential-bearing URLs, and command-backed remotes |
| C69 | Unknown result name and malformed/abbreviated SHA fail validation |
| C70 | A published ancestor commit passes even after the declared remote ref advances |
| C71 | Missing or unrelated commit produces measured fail; all declared results are independently required |
| C72 | Parent index gitlink must match; merely checking out a submodule commit is insufficient |
| C73 | Producing the same result from a sibling checkout or submodule is equally acceptable |
| C74 | Remote rewrite or disappearance after a pass blocks completion and invalidates external evidence |
| C75 | External checks do not modify a working checkout, use interactive prompts, or push |

### 21.8 Recovery and portability

| ID | Scenario and required observation |
| --- | --- |
| C76 | Doctor reports expired and ownerless active work with structured error findings |
| C77 | Repair without confirmation fails; leased recovers to ready and started work to paused |
| C78 | Repair does not reset attempts/budgets, consume approvals, delete worktrees, or alter code |
| C79 | Worktree creation records a separate baseline; the controller checkout's pre-existing edits remain visible separately |
| C80 | Worktree creation rejects an edited item or uncommitted/mismatched pinned concept |
| C81 | Reclaim resumes the registered isolated writer, not the controller checkout |
| C82 | Checkpoint import round-trips state and baseline; identical rows are skipped |
| C83 | A conflicting primary key or deletion record aborts the whole import |
| C84 | Event-only import cannot overwrite canonical lifecycle state |
| C85 | Unsupported database/configuration schemas identify the actual incompatible artifact and never auto-reset |
| C86 | Missing checkout or corrupt imported baseline cannot be presented as clean scope |
| C87 | Compact status excludes verbose evidence/configuration; show supplies actionable details without an epoch |
| C88 | Duplicate IDs and database constraints return JSON errors rather than tracebacks or success-shaped output |
| C89 | Human redirection pauses live work and serves the new request without silently finishing or resuming the old item |

Also cover these adversarial cases: a generic transition into `leased` or
`completed`, concurrent approval consumption, lease renewal racing repair,
path and symlink escapes, an empty contract shown through `contract_mode`,
manual configuration drift, contradictory evidence at the same fingerprints,
checkpoint event order, and completion while a reservation is unsettled.

## 22. Example end-to-end use

The following commands illustrate the interface. They assume the launcher is
installed as `rivet`.

First preview configuration:

```sh
rivet config-init --profile orchestrated --max-calls 4 --dry-run
```

After human confirmation:

```sh
rivet config-init --profile orchestrated --max-calls 4
rivet init
```

Write a draft at `.rivet/context/design.md`, review its intent as appropriate,
and fingerprint it:

```sh
rivet concept-fingerprint --concept-ref .rivet/context/design.md
```

Only after an explicit implementation request:

```sh
rivet start --id work-example \
  --concept-ref .rivet/context/design.md \
  --objective "Implement the approved behavior" \
  --allowed-paths src tests \
  --evidence unit-tests \
  --risk medium
```

Read the returned epoch and version; do not invent them. Implement directly or
reserve one worker call immediately before each real invocation. After the host
worker returns, settle that reserved call.

```sh
rivet dispatch --id work-example --epoch <returned-epoch> \
  --model <current-host-model> --adapter <host-name>
rivet settle-dispatch --id work-example --epoch <returned-epoch> \
  --outcome completed
```

Measure acceptance using the target project's actual test command:

```sh
rivet verify --id work-example --epoch <returned-epoch> \
  --criterion unit-tests -- <test-executable> <test-arguments>
rivet finish --id work-example --epoch <returned-epoch>
```

Inspect `outcome` and `completed`, not only shell exit status.
No publication is implied. If publishing is part of the approved boundary,
obtain and consume its matching effect approval immediately before the host
executes that action.

## 23. Definition of a conforming implementation

An implementation conforms when it runs independently in an unrelated Git
project, passes the applicable conformance scenarios, persists and resumes work
across fresh sessions, and accurately distinguishes:

Human intent from execution state; pre-existing edits from post-admission work;
measured results from assertions; per-item lease ownership from repository
isolation; declared worker accounting from actual host enforcement; and external
result verification from control of another repository.
