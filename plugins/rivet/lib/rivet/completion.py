"""Guarded completion and the finish wrapper (spec sections 15.2 and 15.3)."""

from . import db, evidence, external, lifecycle
from .errors import RivetError

COMPLETABLE = ("verification",)
FINISHABLE = ("leased", "running", "verification")


def _outstanding(conn, work_id):
    budget = db.budget(conn, work_id)
    return 0 if budget is None else budget["reserved"] - budget["used"]


def _require(conn, work_id):
    item = db.item(conn, work_id)
    contract = db.contract(conn, work_id)
    if contract is None:
        raise RivetError("not_admitted", f"{work_id} has no admitted contract")
    return item, contract


def complete(ws, work_id, epoch, expected_version):
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        item, contract = _require(conn, work_id)
        lease = lifecycle.authenticate(conn, work_id, epoch, now)
        lifecycle.check_version(item, expected_version)
        if item["state"] not in COMPLETABLE:
            raise RivetError("invalid_state", f"complete needs verification; {work_id} is {item['state']}")
    subject = evidence.Subject(conn, ws, work_id, contract, lease)
    if subject.measurement.drift:
        raise RivetError("concept_drift", f"concept drift blocks completion: {subject.measurement.drift}",
                         concept_ref=contract["concept_ref"])
    failures = external.recheck(ws, conn, work_id, contract, subject, epoch)
    if failures:
        raise RivetError("external_result_failed", "external result recheck failed: "
                         + "; ".join(f"{f['criterion']}: {f['detail']}" for f in failures), failures=failures)
    settle = evidence.Subject(conn, ws, work_id, contract, lease)
    if settle.diff_fingerprint != subject.diff_fingerprint:
        raise RivetError("worktree_changed", "the repository changed during completion; verify again before completing")
    now = ws.now()
    with db.write_txn(conn):
        item = db.item(conn, work_id)
        lifecycle.authenticate(conn, work_id, epoch, now, renew=False)
        lifecycle.check_version(item, expected_version)
        if item["state"] not in COMPLETABLE:
            raise RivetError("invalid_state", f"complete needs verification; {work_id} is {item['state']}")
        evidence.invalidate_stale(conn, work_id, settle)
        if settle.measurement.violations:
            raise RivetError("scope_violation", "out-of-scope changes block completion: "
                             + ", ".join(settle.measurement.violations), violations=settle.measurement.violations)
        summary = evidence.summary(conn, work_id, contract["required_evidence"], basis="measured")
        if summary["missing"]:
            raise RivetError("evidence_missing", "missing current passing evidence for: " + ", ".join(summary["missing"]),
                             missing=summary["missing"])
        outstanding = _outstanding(conn, work_id)
        if outstanding:
            raise RivetError("outstanding_dispatch", f"{outstanding} dispatched worker call(s) are not settled; "
                             "wait for the worker and settle-dispatch before completing")
        conn.execute("DELETE FROM leases WHERE work_id = ?", (work_id,))
        lifecycle.set_state(conn, item, "completed", now, "completed", {"evidence": summary["sources"]})
    return {"id": work_id, "state": "completed", "version": item["version"], "evidence": summary}


def finish(ws, work_id, epoch):
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        item, contract = _require(conn, work_id)
        if item["state"] not in FINISHABLE:
            raise RivetError("invalid_state", f"finish needs leased, running or verification work; {work_id} is {item['state']}")
        lease = lifecycle.authenticate(conn, work_id, epoch, now)
    subject = evidence.Subject(conn, ws, work_id, contract, lease)
    with db.write_txn(conn):
        lifecycle.authenticate(conn, work_id, epoch, ws.now())
        evidence.invalidate_stale(conn, work_id, subject)
    measurement = subject.measurement
    failures = [] if measurement.drift else external.recheck(ws, conn, work_id, contract, subject, epoch)
    summary = evidence.summary(conn, work_id, contract["required_evidence"], basis="measured")
    blockers = []
    if measurement.drift:
        blockers.append({"code": "concept_drift", "detail": measurement.drift})
    failed = {f["criterion"] for f in failures}
    for failure in failures + [f for f in external.latest_failures(conn, work_id, contract, summary["satisfied"])
                               if f["criterion"] not in failed]:
        blockers.append({"code": "external_result_failed", "criterion": failure["criterion"], "detail": failure["detail"]})
    if summary["missing"]:
        blockers.append({"code": "evidence_missing", "detail": ", ".join(summary["missing"])})
    if measurement.violations:
        blockers.append({"code": "scope_violation", "detail": ", ".join(measurement.violations)})
    outstanding = _outstanding(conn, work_id)
    if outstanding:
        blockers.append({"code": "outstanding_dispatch", "detail": f"{outstanding} reserved worker call(s) not settled"})
    item = db.item(conn, work_id)
    result = {
        "id": work_id,
        "risk": item["risk"],
        "evidence": summary,
        "scope": {
            "valid": measurement.valid,
            "violations": measurement.violations,
            "concept_drift": measurement.drift,
            "draft_paths": measurement.drafts,
        },
    }
    if blockers:
        result.update({"completed": False, "state": item["state"], "version": item["version"], "blockers": blockers})
        return result
    version = item["version"]
    if item["state"] == "leased":
        version = lifecycle.transition(ws, work_id, "running", version, epoch=epoch, reason="finish")["version"]
    if db.item(conn, work_id)["state"] == "running":
        version = lifecycle.transition(ws, work_id, "verification", version, epoch=epoch, reason="finish")["version"]
    done = complete(ws, work_id, epoch, version)
    result.update({"completed": True, "state": done["state"], "version": done["version"], "evidence": done["evidence"], "blockers": []})
    return result
