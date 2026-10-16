# Hosts

One plugin directory, `plugins/rivet/`, serves every host. Skills follow the Agent Skills format (`skills/<name>/SKILL.md` with `name` and `description` only), so every host reads the same five workflows. What differs between hosts is the manifest, the agent-file format, how a worker is launched, and how much each host can enforce.

| | Claude Code | Codex | GitHub Copilot | Cursor |
| --- | --- | --- | --- | --- |
| Manifest | `.claude-plugin/plugin.json` | `plugin.json` (Agent Plugins 1.0) | `plugin.json` (Agent Plugins 1.0); `.plugin/plugin.json` for CLI versions without Agent Plugins support | `.cursor-plugin/plugin.json` |
| Skills | `skills/` | `skills/` | `skills/` | `skills/` |
| Agent roles | `agents/*.md` | `codex/agents/rivet_worker.toml` via `rivet bootstrap --host codex` | `com.github.copilot/agents/*.agent.md` | `cursor/agents/*.md` |
| `--adapter` label | `claude-code` | `codex` | `copilot` | `cursor` |
| Engine on PATH | automatic (plugin `bin/`) | `rivet bootstrap --link-bin` | `rivet bootstrap --link-bin` | `rivet bootstrap --link-bin` |
| Worker can't spawn agents | host-enforced (`tools` omits `Agent`) | protocol | host-enforced (`tools` omits `agent`) | protocol |
| Worker hidden from users | protocol | protocol | host-enforced (`user-invocable: false`) | protocol |
| Coordinator limited to the worker | protocol (`claude --agent` only) | protocol | VS Code: host-enforced (`agents: [rivet-worker]`); CLI: protocol | protocol |

If `rivet` is not on PATH, every skill and agent explains how to reach `python3 <plugin>/bin/rivet` relative to its own file.

## Claude Code

```
/plugin marketplace add paralleldanielle/rivet      # or a local clone's path
/plugin install rivet@rivet
```

For a single session: `claude --plugin-dir /path/to/rivet/plugins/rivet`. The skills appear as `/rivet:rivet-config`, `/rivet:rivet-concept`, `/rivet:rivet-work`, `/rivet:rivet-status` and `/rivet:rivet-doctor`. The coordinator persona can be the main agent: `claude --agent rivet:rivet-coordinator`. Workers launch through the Agent tool as `rivet:rivet-worker`.

## Codex

```
codex plugin marketplace add paralleldanielle/rivet  # reads .agents/plugins/marketplace.json
# in Codex: /plugins -> install rivet
rivet bootstrap --host codex                         # installs ~/.codex/agents/rivet_worker.toml
```

Use `--scope project` to write `.codex/agents/rivet_worker.toml` into the repository instead. Invoke skills with `$rivet-work` and so on. Codex starts subagents only when asked, so the work skill asks for `rivet_worker` by name. If that agent is unavailable, the coordinator falls back to the built-in `worker` with the brief from `skills/rivet-work/references/worker.md`.

## GitHub Copilot

- **Copilot CLI:** `copilot plugin install paralleldanielle/rivet:plugins/rivet`. From a local clone, run `copilot plugin marketplace add /path/to/rivet`, then `copilot plugin install rivet@rivet`.
- **VS Code:** run **Chat: Install Plugin From Source** with the repository URL. Plugins require `chat.plugins.enabled`.

Copilot versions that support Agent Plugins read the root `plugin.json` and load agents from `com.github.copilot/agents/`. Older versions (for example Copilot CLI 1.0.63) check `.plugin/plugin.json` first, and that file points at the same folder. Either way, Copilot never picks up the Claude-format files in `agents/`. The CLI lists plugin agents as `rivet:rivet-coordinator` and `rivet:rivet-worker`. It does not support the coordinator's `agents:` allowlist (VS Code does), so on the CLI that rule is protocol-only. Pick **rivet-coordinator** in the agent picker to run it as the main agent. The worker is hidden from the picker and can be called only as a subagent.

## Cursor

```
rivet bootstrap --host cursor     # symlinks ~/.cursor/plugins/local/rivet -> plugins/rivet
```

Or run `/add-plugin` with the local plugin path, then reload Cursor. Cursor reads `.cursor-plugin/plugin.json`, which points at `skills/` and `cursor/agents/`. If the agents do not appear, check that Cursor picked the Cursor manifest. As a fallback, copy `plugins/rivet/cursor/agents/*.md` into `~/.cursor/agents/`. Some Cursor versions may run subagents on the parent model; the engine records the model the coordinator *declares*.

## Bootstrap reference

```
rivet bootstrap [--host claude-code|codex|copilot|cursor|all]... [--link-bin [DIR]]
                [--scope user|project] [--instructions] [--no-init] [--dry-run]
```

| Flag | Effect |
| --- | --- |
| `--link-bin [DIR]` | Symlink `DIR/rivet` (default `~/.local/bin`) to the launcher, and report whether `DIR` is on PATH |
| `--host codex` | Write the `rivet_worker` agent to `~/.codex/agents/` (or `.codex/agents/` with `--scope project`) |
| `--host cursor` | Symlink the plugin into `~/.cursor/plugins/local/rivet` |
| `--host claude-code`, `--host copilot` | No files; prints the install commands |
| `--instructions` | Add or refresh a `<!-- rivet:begin/end -->` block in `AGENTS.md` (Codex, Copilot, Cursor) and `CLAUDE.md` (Claude Code) |
| `--no-init` | Skip `rivet init` in the current repository |
| `--dry-run` | Report every action as `would_*` without changing anything |

Bootstrap never overwrites a file it did not generate; such files are reported under `conflicts`. It also refuses to write into the repository (`--instructions`, or `--scope project`) while admitted work is unfinished, because that would change the work's baseline. It never runs a host's own CLI for you.
