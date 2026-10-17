# Improvement backlog

Possible future changes to Rivet. Nothing here is committed to. Each entry says what Rivet does today and what could change.

## Contracts

- **Empty contracts.** Today, work started without a concept may have no allowed paths (unrestricted scope) or no acceptance criteria (no evidence needed). Every view shows this through `contract_mode`. Concept work always needs both. *Possible change:* reject empty contracts, or require an explicit flag such as `--unrestricted`.
- **Configuration drift.** Today, the profile, call limit, model allow-list and gates are frozen at admission, and `dispatch` and `check-effect` use the frozen values. Edits to control files outside scope are violations, and `doctor` reports `control_file_drift`. *Possible change:* refuse mutations while the live configuration differs from the admitted one.

## Evidence

- **Latest result wins.** Today, any valid pass satisfies an ordinary criterion, and a later failure on the same repository state does not cancel it (C58). External criteria already use the latest measurement. *Possible change:* an option to apply latest-wins to ordinary criteria too.
- **Live freshness in read-only views.** Today, `show` and `status` report stored validity, labeled `"basis": "recorded"`. `finish` and `complete` always re-check, and `doctor` reports `stale_evidence`. *Possible change:* check freshness live in `show` and `status`, at the cost of a full snapshot per call.

## Scope

- **Draft capture.** Today, any new file under the context directory is exempt from scope. *Possible change:* limit the exemption to Markdown files.
- **Snapshot performance.** Today, every snapshot rehashes every file. *Possible change:* cache hashes by inode, size and mtime for very large repositories.
