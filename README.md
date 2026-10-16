# Rivet

Rivet is a lightweight workflow for context persistence and agentic orchestration. It lets a coding agent run **one bounded work item at a time**:
- an exclusive, expiring lease;
- a frozen contract that says what may change and what proves success;
- Git-measured scope;
- recorded evidence;
- counted worker delegation;
- explicit approval for consequential effects.

All of this persists locally, so a fresh session can pick up where the last one stopped.

It ships as one plugin for **Claude Code, Codex, GitHub Copilot (CLI and VS Code) and Cursor**. The plugin contains:
- a deterministic engine (Python 3.10+, standard library only);
- five workflows: config, concept, work, status and doctor;
- two agent roles: a lease-owning coordinator and a non-delegating worker.

> This README is for people. Agents get what they need from the skills and from the engine's own JSON output.

## How it works

Three layers each own one kind of truth:

| Layer | Owns |
| --- | --- |
| Human-owned Markdown in `.rivet/context/` | Intent: outcomes, decisions, boundaries, acceptance |
| The engine's local SQLite state in `.rivet/runtime/` (Git-ignored) | Execution: contracts, leases, versions, evidence, call accounting, approvals, events |
| Git and your tests | The actual changes and results |

A typical item:

```
concept (optional) -> start -> work directly or with one worker at a time -> verify -> finish
```

1. `start` freezes the contract (objective, allowed paths, acceptance criteria) and snapshots the repository. It also claims a lease and moves the item to `running`.
2. Scope is the net change since that snapshot. It covers staged, unstaged, committed and untracked changes, plus mode and symlink changes. Edits that were already there are visible but not blamed on the item.
3. `verify` runs your real test command with no shell and records a **measured** result. That result is tied to the exact repository state, so any later change makes it stale.
4. `finish` completes the item only when scope is clean, the concept is unchanged and every criterion has current passing evidence. Otherwise it returns structured blockers.

## Install

Clone this repository, or let your host fetch it from GitHub (`paralleldanielle/rivet`). The plugin itself lives in `plugins/rivet/`.

| Host | Install | Notes |
| --- | --- | --- |
| Claude Code | `/plugin marketplace add paralleldanielle/rivet` then `/plugin install rivet@rivet` | `rivet` is on the Bash tool's PATH while the plugin is enabled. Skills show as `/rivet:rivet-work` etc. |
| Copilot CLI | `copilot plugin install paralleldanielle/rivet:plugins/rivet` | From a local clone: `copilot plugin marketplace add /path/to/rivet`, then `copilot plugin install rivet@rivet`. Agents come from `com.github.copilot/agents/`. |
| VS Code (Copilot) | Command Palette → **Chat: Install Plugin From Source** → `https://github.com/paralleldanielle/rivet` | Requires `chat.plugins.enabled`. |
| Codex | `codex plugin marketplace add paralleldanielle/rivet`, then `/plugins` → install **rivet** | Run `rivet bootstrap --host codex` to install the `rivet_worker` agent. Skills are invoked as `$rivet-work`. |
| Cursor | `rivet bootstrap --host cursor` (links the plugin into `~/.cursor/plugins/local/rivet`), or `/add-plugin` with the local path | Reload Cursor afterwards. |

Then, from the repository you want to work in:

```sh
/path/to/rivet/plugins/rivet/bin/rivet bootstrap --host <claude-code|codex|copilot|cursor|all> --link-bin
```

`bootstrap` does the following:
- puts `rivet` on your PATH (`~/.local/bin/rivet`);
- installs what a host's plugin system cannot carry;
- runs `rivet init` in the current repository;
- prints the remaining host commands for you to run.

It never overwrites files that aren't Rivet's. Add `--dry-run` to preview, or `--instructions` to add a short pointer block to `AGENTS.md` / `CLAUDE.md`. See [docs/hosts.md](docs/hosts.md) for per-host details.

Requirements: Linux or macOS, Python 3.10 or newer, and Git 2.31 or newer.

## Quick start

Ask your agent in plain language ("set up rivet", "write a concept for X", "implement the concept", "what's the rivet status?"), and the matching skill takes over. The same steps by hand look like this:

```sh
rivet config-init --profile orchestrated --max-calls 4 --dry-run   # preview, then confirm
rivet config-init --profile orchestrated --max-calls 4
rivet init

# write .rivet/context/design.md, then:
rivet concept-fingerprint --concept-ref .rivet/context/design.md

# only after an explicit request to implement:
rivet start --concept-ref .rivet/context/design.md --objective "Implement the design" \
  --allowed-paths src tests --evidence unit-tests --risk medium
rivet verify --id <id> --epoch <epoch> --criterion unit-tests -- python3 -m pytest -q
rivet finish --id <id> --epoch <epoch>
```

Take `id`, `epoch` and `version` from the JSON the engine prints; never make them up. For measurements, read the verdict field (`outcome`, `completed`, `authorized`, `valid`, `ok`), not just the exit code. `rivet --help` lists every command, and [docs/cli.md](docs/cli.md) documents them.

## What is enforced, and what is not

| Tool-mediated: the engine checks it | Protocol-only: agents are told, and the engine records |
| --- | --- |
| Lease epochs, expiry, and compare-and-set versions | Approval for gated effects comes from a real human |
| Per-item call limit frozen at admission; no refunds | Which model actually ran a worker |
| Attempts per item | One worker at a time; no recursive workers |
| Net Git scope since admission | That draft capture is only used for genuine notes |
| Current measured or asserted evidence for every criterion | Edits outside scope are detected afterwards, not prevented |
| Pinned concept bytes (drift blocks completion) | Cost, tokens, and time: use your host's spending controls |
| Live remote checks for declared external results | Only this Git root is measured |

The engine runs as a local CLI in the same environment as your agent's other tools. It coordinates work; it is not a security boundary.

## Repository layout

```
plugins/rivet/                the installable plugin
  bin/rivet                   launcher (works by absolute path from any repository)
  lib/rivet/                  engine
  skills/rivet-*/SKILL.md     the five workflows (shared by every host)
  agents/                     Claude Code roles
  cursor/agents/              Cursor roles
  com.github.copilot/agents/  Copilot roles
  codex/agents/               Codex worker (installed by bootstrap)
  plugin.json, .plugin/, .claude-plugin/, .cursor-plugin/   host manifests
adapters/                     role and host-note sources for the generated agent files
tools/gen_adapters.py         regenerates role files; --check is enforced by tests
tests/                        black-box conformance suite (C01–C89) and adapter tests
docs/                         CLI, formats, hosts, hardening choices, conformance map
.rivet/context/               the portable specification (this repository's own concept directory)
```

## Development

```sh
python3 -m unittest discover -s tests        # about a minute; needs git
python3 tools/gen_adapters.py                # after editing adapters/
```

The behavior follows `.rivet/context/rivet-portable-specification.md`. Deliberate differences from the reference behavior, and the hardening backlog, are listed in [docs/hardening.md](docs/hardening.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
