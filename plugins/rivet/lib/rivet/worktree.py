"""Optional isolated writer worktrees (spec section 14)."""

import os

from . import db, gitio, lifecycle, scope, snapshot, util
from .errors import RivetError


def create(ws, work_id, epoch, path, branch=None):
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        item = db.item(conn, work_id)
        lease = lifecycle.authenticate(conn, work_id, epoch, now)
        contract = db.contract(conn, work_id)
        if contract is None:
            raise RivetError("not_admitted", f"{work_id} has no admitted contract")
        if db.worktree(conn, work_id) is not None:
            raise RivetError("worktree_invalid", f"{work_id} already has a registered writer worktree")
    destination = os.path.realpath(path if os.path.isabs(path) else os.path.join(ws.root, path))
    if os.path.lexists(destination):
        raise RivetError("worktree_invalid", f"destination already exists: {destination}")
    measurement = scope.Measurement(conn, ws, work_id, contract, lease)
    if measurement.delta:
        raise RivetError("worktree_invalid", "the item already has post-admission changes; isolate only untouched work",
                         post_admission_paths=measurement.delta)
    if measurement.drift:
        raise RivetError("concept_drift", measurement.drift)
    base = lease["base_sha"]
    if not base:
        raise RivetError("worktree_invalid", "the repository has no commit to branch from")
    if contract.get("concept_ref"):
        shown = gitio.run(["show", f"{base}:{contract['concept_ref']}"], ws.root, check=False)
        if shown.returncode != 0 or util.sha256_hex(shown.stdout) != contract["concept_fingerprint"]:
            raise RivetError(
                "worktree_invalid",
                f"the pinned concept {contract['concept_ref']} must be committed unchanged in {base} before isolating; "
                "commit it first or keep working in the controller checkout",
            )
    branch = branch or f"rivet/{work_id}/{epoch[-8:]}"
    if not gitio.run(["check-ref-format", "--branch", branch], ws.root, check=False).returncode == 0:
        raise RivetError("invalid_argument", f"invalid branch name: {branch}")
    gitio.run(["worktree", "add", "--quiet", "-b", branch, destination, base], ws.root)
    try:
        snap = snapshot.capture(destination)
        now = ws.now()
        with db.write_txn(conn):
            lifecycle.authenticate(conn, work_id, epoch, now)
            if db.worktree(conn, work_id) is not None:
                raise RivetError("worktree_invalid", f"{work_id} already has a registered writer worktree")
            conn.execute(
                "INSERT INTO writer_worktrees (work_id, path, branch, base_sha, created_at) VALUES (?, ?, ?, ?, ?)",
                (work_id, snap["writer"], branch, base, util.iso(now)),
            )
            conn.execute(
                "INSERT INTO baselines (id, work_id, kind, writer_path, head, snapshot, digest, created_at) "
                "VALUES (?, ?, 'worktree', ?, ?, ?, ?, ?)",
                (util.new_id("bl"), work_id, snap["writer"], snap["head"], util.canonical_json(snap),
                 snapshot.digest(snap), util.iso(now)),
            )
            conn.execute("UPDATE leases SET writer_path = ? WHERE work_id = ?", (snap["writer"], work_id))
            db.add_event(conn, work_id, "worktree_created", {"path": snap["writer"], "branch": branch, "base_sha": base}, now)
    except RivetError as exc:
        exc.message += f" (the Git worktree {destination} and branch {branch} were created and remain; remove them by hand if unwanted)"
        exc.details.update({"surviving_path": destination, "surviving_branch": branch})
        raise
    return {"id": work_id, "path": snap["writer"], "branch": branch, "base_sha": base, "state": item["state"]}
