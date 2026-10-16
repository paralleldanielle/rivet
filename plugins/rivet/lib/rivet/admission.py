"""Proposals, readiness, concepts, admission, and direct start (spec sections 7 and 15.1)."""

import os

from . import config, db, external, lifecycle, snapshot, util
from .errors import RivetError

RISKS = ("low", "medium", "high", "critical")
DEFAULT_MAX_ATTEMPTS = 3


# -- validation ---------------------------------------------------------------

def _paths(values):
    return sorted({util.normalize_rel_path(v, "allowed path") for v in values or []})


def _criteria(values):
    criteria = util.unique_sorted(values, "evidence criterion")
    for criterion in criteria:
        if criterion.startswith("external:"):
            raise RivetError("reserved_criterion", f"{criterion!r} is reserved; declare --external-result instead")
    return criteria


def _fields(ws, objective, work_id=None, risk=None, allowed_paths=None, effects=None, evidence=None,
            external_results=None, depends_on=None, max_attempts=None):
    util.require_text(objective, "--objective")
    if work_id is not None:
        util.validate_id(work_id, "--id")
    risk = risk or "low"
    if risk not in RISKS:
        raise RivetError("invalid_argument", f"--risk must be one of {', '.join(RISKS)}")
    max_attempts = DEFAULT_MAX_ATTEMPTS if max_attempts is None else max_attempts
    if type(max_attempts) is not int or max_attempts < 1:
        raise RivetError("invalid_argument", "--max-attempts must be a positive integer")
    deps = util.unique_sorted(depends_on, "dependency")
    for dep in deps:
        util.validate_id(dep, "dependency")
    if work_id is not None and work_id in deps:
        raise RivetError("invalid_argument", "an item cannot depend on itself")
    return {
        "id": work_id,
        "objective": objective,
        "risk": risk,
        "allowed_paths": _paths(allowed_paths),
        "effects": util.unique_sorted(effects, "effect"),
        "evidence": None if evidence is None else _criteria(evidence),
        "external_results": external.validate_declarations(external_results, ws.root),
        "depends_on": deps,
        "max_attempts": max_attempts,
    }


def resolve_concept(ws, ref, cfg=None):
    """Validate a concept reference and hash its exact bytes."""
    cfg = cfg or ws.config()
    context_dir = cfg["context_dir"]
    if not isinstance(ref, str) or not ref.strip():
        raise RivetError("concept_invalid", "--concept-ref must be a relative file path")
    if os.path.isabs(ref):
        raise RivetError("concept_invalid", f"concept reference must be relative, not absolute: {ref}")
    try:
        norm = util.normalize_rel_path(ref, "concept reference")
    except RivetError as exc:
        raise RivetError("concept_invalid", exc.message)
    if not util.path_under(norm, context_dir) or norm == context_dir:
        raise RivetError("concept_invalid", f"concept reference must be a file inside the context directory {context_dir}: {ref}")
    real_root = os.path.realpath(ws.root)
    real_context = os.path.realpath(os.path.join(real_root, context_dir))
    real = os.path.realpath(os.path.join(real_root, norm))
    if not util.within(real, real_root) or not util.within(real, real_context) or real == real_context:
        raise RivetError("concept_invalid", f"concept reference resolves outside the context directory {context_dir}: {ref}")
    if not os.path.isfile(real):
        raise RivetError("concept_invalid", f"concept file does not exist: {norm}")
    return norm, util.sha256_file(real)


def concept_fingerprint(ws, ref):
    norm, digest = resolve_concept(ws, ref)
    return {"concept_ref": norm, "concept_fingerprint": digest}


# -- item creation --------------------------------------------------------------

def _create_item(conn, ws, fields, now, concept=None, approval_ref=None):
    work_id = fields["id"] or util.new_id("work")
    if db.item(conn, work_id, required=False) is not None:
        raise RivetError("duplicate_id", f"a work item with id {work_id!r} already exists")
    for dep in fields["depends_on"]:
        if db.item(conn, dep, required=False) is None:
            raise RivetError("not_found", f"dependency {dep!r} does not exist")
    conn.execute(
        "INSERT INTO work_items (id, objective, state, risk, allowed_paths, required_effects, concept_ref, "
        "concept_fingerprint, proposed_evidence, external_results, approval_ref, version, attempts, max_attempts, "
        "created_at, updated_at) VALUES (?, ?, 'proposed', ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)",
        (
            work_id, fields["objective"], fields["risk"], util.canonical_json(fields["allowed_paths"]),
            util.canonical_json(fields["effects"]), concept[0] if concept else None, concept[1] if concept else None,
            None if fields["evidence"] is None else util.canonical_json(fields["evidence"]),
            util.canonical_json(fields["external_results"]), approval_ref, fields["max_attempts"],
            util.iso(now), util.iso(now),
        ),
    )
    for dep in fields["depends_on"]:
        conn.execute("INSERT INTO dependencies (work_id, depends_on) VALUES (?, ?)", (work_id, dep))
    db.add_event(conn, work_id, "proposed", {"objective": fields["objective"], "concept_ref": concept[0] if concept else None}, now)
    return work_id


def propose(ws, concept_ref=None, approval_ref=None, **kwargs):
    if concept_ref:
        raise RivetError(
            "concept_requires_start",
            "concept-linked work cannot use propose; use start --concept-ref or concept-proposal so the concept is pinned",
        )
    fields = _fields(ws, **kwargs)
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        work_id = _create_item(conn, ws, fields, now, approval_ref=approval_ref)
    return {"id": work_id, "state": "proposed", "version": 1}


def ready(ws):
    if not ws.db_exists():
        ws.config()
        return {"ready": [], "initialized": False}
    conn = ws.connect()
    rows = conn.execute(
        "SELECT id FROM work_items WHERE state IN ('proposed', 'ready') ORDER BY created_at, id"
    ).fetchall()
    out = []
    for row in rows:
        item = db.item(conn, row["id"])
        if all(d["state"] == "completed" for d in db.dependencies(conn, item["id"])):
            out.append({
                "id": item["id"],
                "objective": item["objective"],
                "state": item["state"],
                "risk": item["risk"],
                "admitted": db.contract(conn, item["id"]) is not None,
            })
    return {"ready": out}


# -- admission --------------------------------------------------------------------

def contract_mode(allowed_paths, required):
    return {
        "scope": "bounded" if allowed_paths else "unrestricted",
        "evidence": "required" if required else "none_required",
    }


def admit(ws, work_id, required_evidence=None, approval_ref=None, concept_fingerprint_arg=None,
          design_fingerprint=None, expected_baseline=None):
    cfg = ws.config()
    requested = None if required_evidence is None else _criteria(required_evidence)
    conn = ws.connect(create=True)
    item = db.item(conn, work_id)
    if item["state"] != "ready":
        raise RivetError("invalid_state", f"admission needs state ready; {work_id} is {item['state']}")
    snap = snapshot.capture(ws.root)
    now = ws.now()
    with db.write_txn(conn):
        item = db.item(conn, work_id)
        if item["state"] != "ready":
            raise RivetError("invalid_state", f"admission needs state ready; {work_id} is {item['state']}")
        if db.contract(conn, work_id) is not None:
            raise RivetError("already_admitted", f"{work_id} already has a contract; changed scope or acceptance needs a new work item")
        pending = [d["id"] for d in db.dependencies(conn, work_id) if d["state"] != "completed"]
        if pending:
            raise RivetError("dependency_incomplete", f"dependencies are not completed: {', '.join(pending)}", dependencies=pending)
        if item["proposed_evidence"] is not None:
            if requested is not None and requested != item["proposed_evidence"]:
                raise RivetError(
                    "criteria_mismatch",
                    f"{work_id} was proposed with criteria {item['proposed_evidence']}; admission cannot replace them",
                )
            criteria = list(item["proposed_evidence"])
        else:
            criteria = requested or []
        criteria += [f"external:{d['name']}" for d in item["external_results"]]
        if item["concept_ref"]:
            _, current = resolve_concept(ws, item["concept_ref"], cfg)
            if current != item["concept_fingerprint"]:
                raise RivetError("concept_drift", f"concept {item['concept_ref']} changed since it was proposed; preview again")
            if concept_fingerprint_arg and concept_fingerprint_arg != current:
                raise RivetError("concept_drift", "--concept-fingerprint does not match the concept's current bytes")
        elif concept_fingerprint_arg:
            raise RivetError("invalid_argument", "--concept-fingerprint applies only to concept-linked items")
        digest = snapshot.digest(snap)
        if expected_baseline is not None and digest != expected_baseline:
            raise RivetError(
                "confirmation_mismatch",
                "the worktree changed after the proposal was computed; preview the proposal again",
            )
        baseline_id = util.new_id("bl")
        conn.execute(
            "INSERT INTO baselines (id, work_id, kind, writer_path, head, snapshot, digest, created_at) "
            "VALUES (?, ?, 'admission', ?, ?, ?, ?, ?)",
            (baseline_id, work_id, snap["writer"], snap["head"], util.canonical_json(snap), digest, util.iso(now)),
        )
        profile = cfg["execution_profile"]
        limit = cfg["orchestration"]["max_calls"] if profile == "orchestrated" else 0
        models = cfg["orchestration"]["worker_models"]
        approval = approval_ref if approval_ref is not None else item["approval_ref"]
        conn.execute(
            "INSERT INTO contracts (work_id, objective, allowed_paths, effects, required_evidence, approval_ref, "
            "design_fingerprint, concept_ref, concept_fingerprint, external_results, draft_allowance, "
            "config_fingerprint, ignore_fingerprint, execution_profile, delegation_limit, worker_models, gated_effects, "
            "baseline_id, admitted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                work_id, item["objective"], util.canonical_json(item["allowed_paths"]),
                util.canonical_json(item["required_effects"]), util.canonical_json(criteria), approval,
                design_fingerprint, item["concept_ref"], item["concept_fingerprint"],
                util.canonical_json(item["external_results"]), cfg["context_dir"],
                util.file_fingerprint(ws.config_path), util.file_fingerprint(ws.ignore_path),
                profile, limit, util.canonical_json(models), util.canonical_json(cfg["gated_effects"]),
                baseline_id, util.iso(now),
            ),
        )
        if approval != item["approval_ref"]:
            conn.execute("UPDATE work_items SET approval_ref = ? WHERE id = ?", (approval, work_id))
        conn.execute(
            "INSERT INTO budgets (work_id, name, call_limit, reserved, used) VALUES (?, 'delegated_calls', ?, 0, 0)",
            (work_id, limit),
        )
        db.add_event(conn, work_id, "admitted", {"required_evidence": criteria, "delegation_limit": limit, "baseline": baseline_id}, now)
        budget = db.budget(conn, work_id)
    return {
        "id": work_id,
        "admitted": True,
        "state": item["state"],
        "version": item["version"],
        "risk": item["risk"],
        "allowed_paths": item["allowed_paths"],
        "effects": item["required_effects"],
        "required_evidence": criteria,
        "external_results": item["external_results"],
        "concept_ref": item["concept_ref"],
        "concept_fingerprint": item["concept_fingerprint"],
        "approval_ref": approval,
        "draft_allowance": cfg["context_dir"],
        "contract_mode": contract_mode(item["allowed_paths"], criteria),
        "execution_profile": profile,
        "baseline_fingerprint": digest,
        "preexisting_paths": snap["preexisting"],
        "budgets": db.budget_view(budget),
        "delegation": config.delegation(profile, limit, models, remaining=limit),
    }


# -- concept proposals ------------------------------------------------------------

def build_proposal(ws, objective, concept_ref, allowed_paths, **kwargs):
    cfg = ws.config()
    if not allowed_paths:
        raise RivetError("bounds_required", "concept work requires explicit nonempty --allowed-paths")
    fields = _fields(ws, objective, allowed_paths=allowed_paths, **kwargs)
    criteria = fields["evidence"] or []
    if not criteria and not fields["external_results"]:
        raise RivetError("bounds_required", "concept work requires at least one --evidence criterion or --external-result")
    norm, digest = resolve_concept(ws, concept_ref, cfg)
    if ws.db_exists():
        conn = ws.connect()
        if fields["id"] is not None and db.item(conn, fields["id"], required=False) is not None:
            raise RivetError("duplicate_id", f"a work item with id {fields['id']!r} already exists")
        for dep in fields["depends_on"]:
            if db.item(conn, dep, required=False) is None:
                raise RivetError("not_found", f"dependency {dep!r} does not exist")
    elif fields["depends_on"]:
        raise RivetError("not_found", f"dependency {fields['depends_on'][0]!r} does not exist")
    snap = snapshot.capture(ws.root)
    proposal = {
        "id": fields["id"],
        "objective": fields["objective"],
        "risk": fields["risk"],
        "max_attempts": fields["max_attempts"],
        "allowed_paths": fields["allowed_paths"],
        "effects": fields["effects"],
        "depends_on": fields["depends_on"],
        "concept_ref": norm,
        "concept_fingerprint": digest,
        "required_evidence": criteria,
        "external_results": fields["external_results"],
        "baseline_fingerprint": snapshot.digest(snap),
        "preexisting_paths": snap["preexisting"],
    }
    return proposal, util.canonical_hash(proposal)


def concept_proposal(ws, confirm=None, owner=None, ttl=None, approval_ref=None, **kwargs):
    proposal, fingerprint = build_proposal(ws, **kwargs)
    if confirm is None:
        return {
            "preview": True,
            "proposal": proposal,
            "proposal_fingerprint": fingerprint,
            "concept_fingerprint": proposal["concept_fingerprint"],
            "next": "after explicit human confirmation, repeat the same command with --confirm <proposal_fingerprint>",
        }
    if confirm != fingerprint:
        raise RivetError(
            "confirmation_mismatch",
            "the proposal changed since the preview (concept bytes, scope, criteria, dependencies, external results, "
            "or worktree); preview again and confirm the new fingerprint",
        )
    result = _start_from_proposal(ws, proposal, owner, ttl, approval_ref)
    result["proposal_fingerprint"] = fingerprint
    return result


# -- start ---------------------------------------------------------------------------

def _start_from_proposal(ws, proposal, owner, ttl, approval_ref):
    fields = {
        "id": proposal["id"],
        "objective": proposal["objective"],
        "risk": proposal["risk"],
        "allowed_paths": proposal["allowed_paths"],
        "effects": proposal["effects"],
        "evidence": proposal["required_evidence"],
        "external_results": proposal["external_results"],
        "depends_on": proposal["depends_on"],
        "max_attempts": proposal["max_attempts"],
    }
    concept = (proposal["concept_ref"], proposal["concept_fingerprint"])
    return _start_sequence(ws, fields, owner, ttl, approval_ref, concept, proposal["baseline_fingerprint"])


def start(ws, objective, concept_ref=None, owner=None, ttl=None, approval_ref=None, **kwargs):
    lifecycle.check_ttl(ttl)
    if concept_ref:
        proposal, _ = build_proposal(ws, objective=objective, concept_ref=concept_ref, **kwargs)
        return _start_from_proposal(ws, proposal, owner, ttl, approval_ref)
    ws.config()
    fields = _fields(ws, objective, **kwargs)
    return _start_sequence(ws, fields, owner, ttl, approval_ref, None, None)


def _start_sequence(ws, fields, owner, ttl, approval_ref, concept, expected_baseline):
    """propose -> ready -> admit -> claim -> running as separate committed steps.

    A failure part-way (for example an incomplete dependency) leaves the
    committed steps in place; inspect the item and resume rather than creating
    a duplicate.
    """
    owner = owner or "coordinator"
    util.require_text(owner, "--owner")
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        work_id = _create_item(conn, ws, fields, now, concept=concept, approval_ref=approval_ref)
    item = db.item(conn, work_id)
    lifecycle.transition(ws, work_id, "ready", item["version"], reason="start")
    try:
        admitted = admit(ws, work_id, approval_ref=approval_ref, expected_baseline=expected_baseline)
    except RivetError as exc:
        exc.details.setdefault("id", work_id)
        exc.details.setdefault("state", db.item(conn, work_id)["state"])
        exc.message += f" ({work_id} remains ready and unadmitted with its contract fields; resume with admit once resolved)"
        raise
    claimed = lifecycle.claim(ws, work_id, owner, ttl=ttl)
    running = lifecycle.transition(ws, work_id, "running", claimed["version"], epoch=claimed["epoch"], reason="start")
    result = dict(admitted)
    result.update({
        "state": "running",
        "version": running["version"],
        "epoch": claimed["epoch"],
        "owner": claimed["owner"],
        "base_sha": claimed["base_sha"],
        "writer_path": claimed["writer_path"],
        "attempts": claimed["attempts"],
        "max_attempts": claimed["max_attempts"],
        "expires_at": claimed["expires_at"],
        "ttl_seconds": claimed["ttl_seconds"],
    })
    return result
