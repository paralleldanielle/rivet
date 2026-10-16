---
name: rivet-config
description: "Set up or change Rivet for this repository: execution profile (restricted or orchestrated), concept directory, worker-call limit and optional model allow-list, previewed and confirmed before anything is written. Use when initializing Rivet, enabling worker delegation, or changing limits. Keywords: rivet config, rivet init, orchestrated, max calls, worker models."
---

# Rivet configuration

Choose and persist how Rivet runs in this repository. Changes go through the engine after a preview and an explicit human confirmation, never by editing JSON by hand.

**Engine.** Run `rivet <command>`. If `rivet` is not on PATH, run `python3 <plugin>/bin/rivet <command>`, where `<plugin>` is two directories above this skill's folder; resolve it to an absolute path once and reuse it. Every command prints one JSON object. `rivet <command> --help` lists flags.

## Steps
1. Confirm this is a Git repository. If `.rivet/config.json` exists, run `rivet config` and summarize the current settings.
2. Collect:
   - the profile: `restricted` (work directly, no workers) or `orchestrated` (one worker at a time, up to a per-item limit)
   - the concept directory (default `.rivet/context`)
   - for orchestrated, a positive maximum number of worker calls per item
   - optional exact model names to allow. Leave this empty unless the human wants a restriction: an empty list means any model the host offers.
3. Preview: `rivet config-init --profile <profile> [--max-calls <n>] [--context-dir <dir>] [--worker-model <name>]... --dry-run`, adding `--replace` if a configuration exists.
4. Show the human the candidate (`rendered`), the `delegation` verdict, and what the `enforcement` report marks protocol-only. Ask for confirmation.
5. After confirmation, run the same command without `--dry-run`.
6. Run `rivet init`. It creates runtime state and the ignore rule and keeps existing files. Report any `warnings`.
7. Run `rivet config` and report the persisted settings.

## Guardrails
- Replacement is refused while admitted work is unfinished. Report the listed items and suggest finishing or abandoning them first. Never edit the file to get around it.
- Configure and initialize before previewing concept work: creating files afterwards changes the baseline and invalidates the confirmation.
- The five mandatory gates cannot be removed. Extra gates already in the file are kept.
- `max_calls` counts worker dispatches per item. It is not a cost or token budget; spending limits belong to the host's own controls.
- Do not invent a model list from memory; use names the host actually offers.
- If the engine reports an unsupported configuration schema, follow the command in its error once, with the same preview and confirmation.
