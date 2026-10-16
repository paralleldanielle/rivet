"""Read-only views (show, status, check-scope) and diagnostics with limited repair (spec section 16)."""

import os
import shlex

from . import config, db, evidence, external, gitio, lifecycle, scope, util
from .errors import RivetError

REPAIR = {"leased": "ready", "running": "paused", "verification": "paused", "human_review": "paused"}


def _blockers(conn, item, contract, lease, summary, now):
    out = []
    state = item["state"]
    if state in lifecycle.TERMINAL:
        return out
    pending = [d["id"] for d in db.dependencies(conn, item["id"]) if d["state"] != "completed"]
    if pending and contract is None:
        out.append({"code": "dependency_incomplete", "detail": ", ".join(pending)})
    if lease is not None and lease["expires_at"] <= now:
        out.append({"code": "lease_expired", "detail": f"expired at {util.iso(lease['expires_at'])}; recover with the doctor workflow"})
    if state in lifecycle.ACTIVE and lease is None:
        out.append({"code": "lease_missing", "detail": f"{state} with no lease; recover with the doctor workflow"})
    if state in ("ready", "paused") and item["attempts"] >= item["max_attempts"]:
        out.append({"code": "attempts_exhausted", "detail": f"{item['attempts']}/{item['max_attempts']}"})
    if summary is not None and state in ("verification", "human_review") and summary["missing"]:
        out.append({"code": "evidence_missing", "detail": ", ".join(summary["missing"])})
    if contract is not None and summary is not None:
        for failure in external.latest_failures(conn, item["id"], contract, summary["satisfied"]):
            out.append({"code": "external_result_failed", "criterion": failure["criterion"], "detail": failure["detail"]})
    budget = db.budget(conn, item["id"])
    if budget is not None and budget["reserved"] > budget["used"]:
        out.append({"code": "outstanding_dispatch", "detail": f"{budget['reserved'] - budget['used']} reserved call(s) not settled"})
    return out


def show(ws, work_id):
    conn = ws.connect()
    now = ws.now()
    item = db.item(conn, work_id)
    contract = db.contract(conn, work_id)
    lease = db.lease(conn, work_id)
    budget = db.budget(conn, work_id)
    required = contract["required_evidence"] if contract else []
    summary = evidence.summary(conn, work_id, required) if contract else None
    followups = [dict(r) for r in conn.execute(
        "SELECT id, objective, state FROM followups WHERE parent_id = ? ORDER BY created_at, id", (work_id,))]
    if contract is not None:
        delegation = config.delegation(
            contract["execution_profile"], contract["delegation_limit"], contract["worker_models"],
            remaining=budget["call_limit"] - budget["reserved"],
        )
    else:
        delegation = config.workspace_delegation(ws.config())
    worktree = db.worktree(conn, work_id)
    return {
        "id": work_id,
        "objective": item["objective"],
        "state": item["state"],
        "version": item["version"],
        "risk": item["risk"],
        "attempts": item["attempts"],
        "max_attempts": item["max_attempts"],
        "admitted": contract is not None,
        "allowed_paths": (contract or item)["allowed_paths"],
        "effects": item["required_effects"],
        "proposed_evidence": item["proposed_evidence"],
        "required_evidence": required,
        "external_results": item["external_results"],
        "concept_ref": item["concept_ref"],
        "concept_fingerprint": item["concept_fingerprint"],
        "approval_ref": item["approval_ref"],
        "depends_on": db.dependencies(conn, work_id),
        "contract_mode": None if contract is None else {
            "scope": "bounded" if contract["allowed_paths"] else "unrestricted",
            "evidence": "required" if contract["required_evidence"] else "none_required",
        },
        "lease": lifecycle.lease_view(lease, now),
        "writer_worktree": None if worktree is None else {"path": worktree["path"], "branch": worktree["branch"]},
        "budgets": db.budget_view(budget),
        "delegation": delegation,
        "evidence": summary,
        "scope": scope.measure_view(conn, ws, work_id, contract, lease),
        "blockers": _blockers(conn, item, contract, lease, summary, now),
        "next_transitions": lifecycle.next_transitions(item["state"]),
        "followups": followups,
    }


def check_scope(ws, work_id, epoch):
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        db.item(conn, work_id)
        lease = lifecycle.authenticate(conn, work_id, epoch, now)
        contract = db.contract(conn, work_id)
        if contract is None:
            raise RivetError("not_admitted", f"{work_id} has no admitted contract")
    view = scope.Measurement(conn, ws, work_id, contract, lease).view()
    view["id"] = work_id
    return view


def status(ws, full=False):
    cfg = ws.config()
    base = {
        "initialized": ws.db_exists(),
        "execution_profile": cfg["execution_profile"],
        "context_dir": cfg["context_dir"],
        "delegation": config.workspace_delegation(cfg),
    }
    if not ws.db_exists():
        base.update({"by_state": {}, "active": [], "blockers": [], "open_followups": 0,
                     "detail": f"run `{ws.command('init')}` to create runtime state"})
        return base
    conn = ws.connect()
    now = ws.now()
    by_state = {r["state"]: r["n"] for r in conn.execute("SELECT state, COUNT(*) AS n FROM work_items GROUP BY state")}
    active, blockers = [], []
    for row in conn.execute("SELECT id FROM work_items WHERE state NOT IN ('completed', 'abandoned') ORDER BY created_at, id"):
        item = db.item(conn, row["id"])
        contract = db.contract(conn, item["id"])
        lease = db.lease(conn, item["id"])
        summary = evidence.summary(conn, item["id"], contract["required_evidence"]) if contract else None
        active.append({
            "id": item["id"],
            "objective": item["objective"],
            "state": item["state"],
            "version": item["version"],
            "attempts": item["attempts"],
            "max_attempts": item["max_attempts"],
            "admitted": contract is not None,
            "lease": lifecycle.lease_view(lease, now),
        })
        for blocker in _blockers(conn, item, contract, lease, summary, now):
            blockers.append(dict(blocker, id=item["id"]))
    followups = conn.execute(
        "SELECT COUNT(*) AS n FROM followups f JOIN work_items w ON w.id = f.parent_id"
    ).fetchone()["n"]
    base.update({
        "by_state": by_state,
        "active": active,
        "blockers": blockers,
        "open_followups": followups,
    })
    if not full:
        base["detail"] = "use show --id <id> for one item, or status --full for raw records"
        return base
    tables = {}
    for table, _ in db.TABLES:
        if table == "baselines":
            rows = conn.execute("SELECT id, work_id, kind, writer_path, head, digest, created_at FROM baselines ORDER BY rowid")
        elif table == "events":
            rows = conn.execute("SELECT * FROM events ORDER BY seq")
        else:
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
        tables[table] = [db.decode(table, r) for r in rows]
    for lease in tables["leases"]:
        lease["expired"] = lease["expires_at"] <= now
        lease["expires_at"] = util.iso(lease["expires_at"])
    base.update({
        "config": cfg,
        "enforcement": config.enforcement_report(),
        "records": tables,
        "detail": "records are raw diagnostic data, not a stable interface",
    })
    return base


# -- doctor ---------------------------------------------------------------------------

def _finding(code, severity, detail, work_id=None, **extra):
    finding = {"code": code, "severity": severity, "work_id": work_id, "detail": detail}
    finding.update(extra)
    return finding


def _cycles(conn):
    edges = {}
    for row in conn.execute("SELECT work_id, depends_on FROM dependencies"):
        edges.setdefault(row["work_id"], []).append(row["depends_on"])
    seen_cycles, cycles = set(), []
    state = {}

    def visit(node, path):
        state[node] = "active"
        path.append(node)
        for nxt in edges.get(node, []):
            if state.get(nxt) == "active":
                cycle = path[path.index(nxt):]
                key = frozenset(cycle)
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    cycles.append(cycle)
            elif nxt not in state:
                visit(nxt, path)
        path.pop()
        state[node] = "done"

    for node in sorted(edges):
        if node not in state:
            visit(node, [])
    return cycles


def _repairable(conn, now):
    """Expired or missing leases on active work, with their target state."""
    out = []
    for row in conn.execute("SELECT id FROM work_items WHERE state IN ('leased', 'running', 'verification', 'human_review') ORDER BY id"):
        item = db.item(conn, row["id"])
        lease = db.lease(conn, item["id"])
        if lease is None:
            out.append(("orphaned_item", item, None))
        elif lease["expires_at"] <= now:
            out.append(("expired_lease", item, lease))
    return out


def _token(repairs):
    material = [[code, item["id"], item["state"], item["version"], lease["epoch"] if lease else None]
                for code, item, lease in repairs]
    return "repair-" + util.canonical_hash(material)[:16]


def diagnose(ws):
    findings = []
    try:
        cfg = ws.read_config()
    except RivetError as exc:
        findings.append(_finding("configuration_unusable", "error", exc.message,
                                 fix=ws.command("config-init", "--profile", "<restricted|orchestrated>", "--replace", "--dry-run")))
        return {"ok": False, "findings": findings, "root": ws.root}, None
    if cfg is None:
        findings.append(_finding("not_initialized", "error", f"no configuration; run `{ws.command('init')}`"))
        return {"ok": False, "findings": findings, "root": ws.root}, None
    report = {
        "root": ws.root,
        "config": cfg,
        "enforcement": config.enforcement_report(),
        "delegation": config.workspace_delegation(cfg),
    }
    if not ws.db_exists():
        findings.append(_finding("not_initialized", "error", f"no runtime state; run `{ws.command('init')}`"))
        report.update({"ok": False, "findings": findings})
        return report, None
    conn = db.connect(ws.db_path)
    version = db.schema_version(conn)
    if version != str(db.DB_SCHEMA_VERSION):
        conn.close()
        backup = f"{ws.runtime_dir}.bak-{util.iso(ws.now())[:19].replace(':', '').replace('-', '')}"
        findings.append(_finding(
            "schema_mismatch", "error",
            f"runtime database {ws.rel(ws.db_path)} has schema {version!r}; this engine supports {db.DB_SCHEMA_VERSION}. "
            "No migration is attempted. Moving the runtime directory aside discards local leases, evidence and "
            "accounting for this repository (configuration and concepts are kept). The configuration is not affected.",
            artifact=ws.rel(ws.db_path),
        ))
        report.update({
            "ok": False,
            "findings": findings,
            "suggested_repair": {
                "command": f"mv {shlex.quote(ws.runtime_dir)} {shlex.quote(backup)} && {ws.command('init')}",
                "confirm_token": None,
                "note": "Destructive for local runtime state: run only after explicit human confirmation. "
                        "The old state is kept at the backup path.",
            },
        })
        return report, None
    ws._conn = conn
    now = ws.now()
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        findings.append(_finding("integrity_failed", "error", f"SQLite integrity check: {integrity}"))
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        findings.append(_finding("integrity_failed", "error", f"{len(fk)} foreign-key violation(s)"))
    if not _runtime_ignored(ws):
        findings.append(_finding("runtime_not_ignored", "error",
                                 f"{config.RUNTIME_REL}/ is visible to Git; add 'runtime/' to {config.IGNORE_REL}"))
    repairs = _repairable(conn, now)
    for code, item, lease in repairs:
        target = REPAIR[item["state"]]
        detail = (f"the lease expired at {util.iso(lease['expires_at'])}; {item['state']} work can recover to {target}"
                  if lease else f"{item['state']} work has no lease; it can recover to {target}")
        findings.append(_finding(code, "error", detail, item["id"]))
    for cycle in _cycles(conn):
        findings.append(_finding("dependency_cycle", "error", " -> ".join(cycle + [cycle[0]]), cycle[0]))
    for row in conn.execute("SELECT w.work_id, w.path FROM writer_worktrees w JOIN work_items i ON i.id = w.work_id "
                            "WHERE i.state NOT IN ('completed', 'abandoned')"):
        if not os.path.isdir(row["path"]):
            findings.append(_finding("missing_worktree", "error", f"registered writer worktree is missing: {row['path']}", row["work_id"]))
    for row in conn.execute("SELECT work_id FROM leases ORDER BY work_id"):
        lease = db.lease(conn, row["work_id"])
        item = db.item(conn, row["work_id"])
        if not os.path.isdir(lease["writer_path"]):
            findings.append(_finding("writer_missing", "error",
                                     f"lease writer checkout does not exist on this machine: {lease['writer_path']}", item["id"]))
            continue
        if lease["expires_at"] <= now or item["state"] in lifecycle.TERMINAL:
            continue
        contract = db.contract(conn, item["id"])
        if contract is None:
            continue
        try:
            measurement = scope.Measurement(conn, ws, item["id"], contract, lease)
        except RivetError as exc:
            findings.append(_finding("scope_check_failed", "error", exc.message, item["id"]))
            continue
        if measurement.violations:
            findings.append(_finding("scope_violation", "error", ", ".join(measurement.violations), item["id"]))
        if measurement.drift:
            findings.append(_finding("concept_drift", "error", measurement.drift, item["id"]))
        stale = conn.execute(
            "SELECT COUNT(*) AS n FROM evidence WHERE work_id = ? AND valid = 1 "
            "AND (input_fingerprint IS NOT ? OR diff_fingerprint IS NOT ?)",
            (item["id"], lease["base_sha"], measurement.fingerprint),
        ).fetchone()["n"]
        if stale:
            findings.append(_finding("stale_evidence", "warning",
                                     f"{stale} recorded result(s) no longer match the current repository state; rerun them",
                                     item["id"]))
        for rel, key in ((config.CONFIG_REL, "config_fingerprint"), (config.IGNORE_REL, "ignore_fingerprint")):
            if contract.get(key) and util.file_fingerprint(os.path.join(ws.root, rel)) != contract[key]:
                findings.append(_finding("control_file_drift", "warning",
                                         f"{rel} changed since admission; the item keeps its admitted settings", item["id"]))
        budget = db.budget(conn, item["id"])
        if budget and budget["reserved"] > budget["used"]:
            findings.append(_finding("outstanding_dispatch", "warning",
                                     f"{budget['reserved'] - budget['used']} reserved call(s) not settled", item["id"]))
    findings.append(_finding(
        "enforcement_notice", "info",
        "Leases, versions, scope measurement, evidence, external checks and call accounting are tool-mediated. "
        "Effect approvals, worker models, one-worker-at-a-time, draft capture and cost limits are protocol-only: "
        "the engine records them but cannot stop a host from acting outside them. Only this Git root is measured.",
    ))
    report["findings"] = findings
    report["ok"] = not any(f["severity"] == "error" for f in findings)
    if repairs:
        token = _token(repairs)
        report["suggested_repair"] = {
            "command": ws.command("doctor", "--fix", "--confirm", token),
            "confirm_token": token,
            "note": "Recovers lease/lifecycle metadata only (leased -> ready; running, verification, human_review -> paused). "
                    "Run only after explicit human confirmation.",
        }
    return report, repairs


def _runtime_ignored(ws):
    probe = os.path.join(config.RUNTIME_REL, "state.db")
    return gitio.run(["check-ignore", "-q", "--no-index", probe], ws.root, check=False).returncode == 0


def doctor(ws, fix=False, confirm=None):
    report, repairs = diagnose(ws)
    if not fix:
        return report
    if not confirm:
        raise RivetError("confirmation_required", "doctor --fix requires --confirm <token> from a read-only doctor run "
                         "and explicit human confirmation")
    if not repairs or _token(repairs) != confirm:
        raise RivetError("repair_token_invalid", "the confirmation token does not match the current findings; "
                         "run doctor again and confirm the new suggestion")
    conn = ws.connect(create=False)
    now = ws.now()
    repaired, skipped = [], []
    with db.write_txn(conn):
        for code, snapshot_item, _ in repairs:
            item = db.item(conn, snapshot_item["id"])
            lease = db.lease(conn, item["id"])
            if item["state"] not in REPAIR or (lease is not None and lease["expires_at"] > now):
                skipped.append({"id": item["id"], "reason": "renewed, reclaimed or changed since diagnosis"})
                continue
            target = REPAIR[item["state"]]
            if lease is not None:
                conn.execute("DELETE FROM leases WHERE work_id = ?", (item["id"],))
            source = item["state"]
            lifecycle.set_state(conn, item, target, now, "repair", {"finding": code})
            repaired.append({"id": item["id"], "from": source, "to": target, "version": item["version"]})
    after, _ = diagnose(ws)
    after["repaired"] = repaired
    after["skipped"] = skipped
    return after

