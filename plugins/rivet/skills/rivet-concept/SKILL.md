---
name: rivet-concept
description: "Write or amend human-owned intent in this repository's Rivet context directory: a concept, design decision or acceptance boundary that a fresh session can implement from. Use when someone wants intent recorded before implementation. Not for progress notes, coding plans, or starting work. Keywords: concept, design doc, ADR, acceptance criteria."
---

# Rivet concept

Record lasting human intent so a fresh session can implement it without the conversation. A concept is a short Markdown document in the configured context directory. Writing one never starts, admits or claims work.

**Engine.** Run `rivet <command>`. If `rivet` is not on PATH, run `python3 <plugin>/bin/rivet <command>`, where `<plugin>` is two directories above this skill's folder; resolve it to an absolute path once and reuse it. Every command prints one JSON object.

## Steps
1. Run `rivet config` and use `config.context_dir`; never guess a location. If the repository is not initialized, use the rivet-config workflow first.
2. Read the existing concepts there that touch the same area.
3. Write or amend the document.
4. Run `rivet concept-fingerprint --concept-ref <path>` and report the path and hash. Say whether it is a draft or approved by the human.

## What a concept contains
- The problem, the desired outcome, and the stable context needed to understand it
- Decisions and their reasons
- Boundaries and invariants: what must not change, and which paths are in play
- Interfaces and assumptions, where they affect implementation
- Observable acceptance: how to tell it is done, with stable pointers such as test names or commands
- Open decisions that genuinely block implementation

Leave out empty template sections, copied file inventories, runtime values, test output, step-by-step implementation plans and transcripts.

## Boundaries
- A draft is not approval. Never mark a concept approved, or start work from it, unless the human says so.
- Changing an approved design or acceptance boundary needs the human's explicit approval before you edit it.
- If an active item was admitted against this concept, editing it is drift and blocks that item's completion. Prefer a new document. If this session holds that item's lease, pause the item first.
- A new file in the context directory during active work is draft capture: allowed, and it does not expand the active item.
- The latest human instruction wins. A request to write a concept is complete once the draft is written and fingerprinted. Implementation happens later, on an explicit request, through the rivet-work workflow (`rivet start --concept-ref ...`).
