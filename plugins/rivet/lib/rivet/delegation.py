"""Delegated-call accounting, effect approvals, and follow-ups (spec sections 10, 13, 5.6)."""

from . import config, db, lifecycle, util
from .errors import RivetError


def _frozen_delegation(contract, budget):
    return config.delegation(
        contract["execution_profile"], contract["delegation_limit"], contract["worker_models"],
        remaining=budget["call_limit"] - budget["reserved"],
    )


def dispatch(ws, work_id, epoch, model, adapter):
    """Reserve one worker call immediately before the host launches it."""
    model = util.require_text(model, "--model").strip()
    adapter = util.require_text(adapter, "--adapter").strip()
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        item = db.item(conn, work_id)
        lifecycle.authenticate(conn, work_id, epoch, now)
        contract = db.contract(conn, work_id)
        if contract is None:
            raise RivetError("not_admitted", f"{work_id} has no admitted contract")
        if contract["execution_profile"] != "orchestrated":
            raise RivetError("delegation_unavailable", f"{work_id} was admitted under the restricted profile; "
                             "orchestration is required to dispatch workers. Work directly.")
        if item["state"] != "running":
            raise RivetError("invalid_state", f"dispatch needs running work; {work_id} is {item['state']}")
        models = contract["worker_models"]
        if models and model not in models:
            raise RivetError("model_not_allowed", f"model {model!r} is not in the exact allow-list: {', '.join(models)}")
        budget = db.budget(conn, work_id)
        remaining = budget["call_limit"] - budget["reserved"]
        if remaining <= 0:
            raise RivetError("budget_exhausted", f"no delegated calls remain ({budget['reserved']}/{budget['call_limit']} "
                             "reserved); do not launch a worker, continue directly")
        conn.execute(
            "UPDATE budgets SET reserved = reserved + 1 WHERE work_id = ? AND name = 'delegated_calls'", (work_id,)
        )
        db.add_event(conn, work_id, "dispatch", {"model": model, "adapter": adapter}, now)
        budget = db.budget(conn, work_id)
    return {
        "id": work_id,
        "reserved": budget["reserved"],
        "used": budget["used"],
        "limit": budget["call_limit"],
        "remaining_calls": budget["call_limit"] - budget["reserved"],
        "model": model,
        "adapter": adapter,
        "next": "launch exactly one worker now, wait for it, then settle-dispatch",
    }


def settle(ws, work_id, epoch, outcome):
    if outcome not in ("completed", "failed"):
        raise RivetError("invalid_argument", "--outcome must be completed or failed")
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        db.item(conn, work_id)
        lifecycle.authenticate(conn, work_id, epoch, now)
        budget = db.budget(conn, work_id)
        if budget is None or budget["reserved"] - budget["used"] <= 0:
            raise RivetError("settlement_invalid", f"{work_id} has no outstanding reserved call; dispatch first")
        conn.execute("UPDATE budgets SET used = used + 1 WHERE work_id = ? AND name = 'delegated_calls'", (work_id,))
        db.add_event(conn, work_id, "settle", {"outcome": outcome}, now)
        budget = db.budget(conn, work_id)
    return {
        "id": work_id,
        "name": "delegated_calls",
        "outcome": outcome,
        "used": budget["used"],
        "reserved": budget["reserved"],
        "remaining_calls": budget["call_limit"] - budget["reserved"],
    }


def _gates(ws, conn, work_id):
    contract = db.contract(conn, work_id)
    if contract is not None:
        return set(contract["gated_effects"])
    return set(ws.config()["gated_effects"]) | set(config.MANDATORY_GATES)


def approve(ws, work_id, effect, approver):
    effect = util.require_text(effect, "--effect").strip()
    approver = util.require_text(approver, "--approver").strip()
    conn = ws.connect(create=True)
    now = ws.now()
    approval_id = util.new_id("appr")
    with db.write_txn(conn):
        db.item(conn, work_id)
        conn.execute(
            "INSERT INTO approvals (id, work_id, effect, approver, created_at, consumed) VALUES (?, ?, ?, ?, ?, 0)",
            (approval_id, work_id, effect, approver, util.iso(now)),
        )
        db.add_event(conn, work_id, "approval", {"effect": effect, "approver": approver, "approval_id": approval_id}, now)
    return {"approval_id": approval_id, "id": work_id, "effect": effect, "approver": approver}


def check_effect(ws, work_id, effect, consume=False):
    effect = util.require_text(effect, "--effect").strip()
    conn = ws.connect(create=consume)
    now = ws.now()
    with db.write_txn(conn):
        db.item(conn, work_id)
        gated = effect in _gates(ws, conn, work_id)
        result = {"id": work_id, "effect": effect, "gated": gated, "consumed": False, "approval_id": None}
        if not gated:
            result["authorized"] = True
            return result
        row = conn.execute(
            "SELECT id FROM approvals WHERE work_id = ? AND effect = ? AND consumed = 0 ORDER BY rowid LIMIT 1",
            (work_id, effect),
        ).fetchone()
        if row is None:
            result["authorized"] = False
            return result
        result["authorized"] = True
        result["approval_id"] = row["id"]
        if consume:
            updated = conn.execute(
                "UPDATE approvals SET consumed = 1, consumed_at = ? WHERE id = ? AND consumed = 0",
                (util.iso(now), row["id"]),
            ).rowcount
            if updated != 1:
                result["authorized"] = False
                result["approval_id"] = None
                return result
            result["consumed"] = True
            db.add_event(conn, work_id, "approval_consumed", {"effect": effect, "approval_id": row["id"]}, now)
    return result


def propose_followup(ws, parent_id, epoch, objective):
    util.require_text(objective, "--objective")
    conn = ws.connect(create=True)
    now = ws.now()
    followup_id = util.new_id("followup")
    with db.write_txn(conn):
        db.item(conn, parent_id)
        lifecycle.authenticate(conn, parent_id, epoch, now)
        conn.execute(
            "INSERT INTO followups (id, parent_id, objective, state, created_at) VALUES (?, ?, ?, 'proposed', ?)",
            (followup_id, parent_id, objective, util.iso(now)),
        )
        db.add_event(conn, parent_id, "followup", {"followup_id": followup_id}, now)
    return {"id": followup_id, "parent_work_id": parent_id, "state": "proposed", "objective": objective}
