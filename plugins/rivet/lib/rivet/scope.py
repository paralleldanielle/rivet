"""Scope measurement against the admission or isolated-writer baseline."""

import json
import os

from . import config, db, gitio, snapshot, util
from .errors import RivetError


def writer_baseline(conn, work_id):
    """The baseline that measures the current writer: its worktree baseline if
    one was registered, else the admission baseline."""
    return db.baseline(conn, work_id, "worktree") or db.baseline(conn, work_id, "admission")


def load_snapshot(row):
    """Decode a stored baseline and verify its digest. A corrupt or missing
    baseline is never reinterpreted as a clean checkout."""
    if row is None:
        raise RivetError("scope_unavailable", "the admission baseline is missing")
    try:
        snap = json.loads(row["snapshot"])
    except (TypeError, ValueError):
        raise RivetError("scope_unavailable", "the stored baseline is corrupt (not valid JSON)")
    if not isinstance(snap, dict) or snap.get("format") != snapshot.FORMAT or not isinstance(snap.get("entries"), dict):
        raise RivetError("scope_unavailable", "the stored baseline is corrupt (unrecognized structure)")
    if snapshot.digest(snap) != row["digest"]:
        raise RivetError("scope_unavailable", "the stored baseline is corrupt (digest mismatch)")
    if snap.get("writer") != row["writer_path"]:
        raise RivetError("scope_unavailable", "the stored baseline is corrupt (writer path mismatch)")
    return snap


def concept_drift(writer, contract):
    """None when the admitted concept is unchanged, else a reason string."""
    ref = contract.get("concept_ref")
    if not ref:
        return None
    path = os.path.join(writer, ref)
    if not os.path.isfile(path):
        return f"admitted concept {ref} is missing"
    if util.sha256_file(path) != contract["concept_fingerprint"]:
        return f"admitted concept {ref} changed after admission"
    return None


class Measurement:
    """Current writer state compared with the item's baseline."""

    def __init__(self, conn, ws, work_id, contract, lease=None):
        self.contract = contract
        self.work_id = work_id
        row = writer_baseline(conn, work_id)
        self.baseline_mode = row["kind"] if row else "admission"
        self.baseline = load_snapshot(row)
        self.baseline_digest = row["digest"]
        admission_row = db.baseline(conn, work_id, "admission")
        self.original_preexisting = (
            load_snapshot(admission_row)["preexisting"] if row["kind"] != "admission" else self.baseline["preexisting"]
        )
        writer = row["writer_path"]
        if lease is not None and lease["writer_path"] != writer:
            raise RivetError("scope_unavailable", f"lease writer {lease['writer_path']} differs from the baseline writer {writer}")
        self.writer = writer
        if not os.path.isdir(writer):
            raise RivetError("scope_unavailable", f"writer checkout is missing: {writer}")
        if os.path.realpath(writer) != writer:
            raise RivetError("scope_unavailable", f"writer path now resolves elsewhere: {writer}")
        self.current = snapshot.capture(writer)
        base_head = self.baseline.get("head")
        if base_head:
            head = self.current.get("head")
            try:
                ok = bool(head) and gitio.is_ancestor(base_head, head, writer)
            except RivetError as exc:
                raise RivetError("scope_unavailable", f"admission commit ancestry unavailable: {exc.message}")
            if not ok:
                raise RivetError("scope_unavailable", f"admission commit {base_head} is no longer an ancestor of HEAD")
        self.current_digest = snapshot.digest(self.current)
        self.fingerprint = snapshot.state_fingerprint(self.baseline_digest, self.current_digest)
        self.delta = snapshot.delta(self.baseline, self.current)
        self.drift = concept_drift(writer, contract)
        self._classify(ws)

    def _classify(self, ws):
        contract = self.contract
        allowed = contract["allowed_paths"]
        self.unrestricted = not allowed
        context_dir = contract["draft_allowance"]
        concept_ref = contract.get("concept_ref")
        control = {
            config.CONFIG_REL: contract.get("config_fingerprint"),
            config.IGNORE_REL: contract.get("ignore_fingerprint"),
        }
        self.violations, self.drafts = [], []
        for path in self.delta:
            if self.unrestricted or any(util.path_under(path, prefix) for prefix in allowed):
                continue
            if path == concept_ref and self.drift is None:
                continue
            if (util.path_under(path, context_dir) and path != concept_ref
                    and not snapshot.present(self.baseline, path)):
                self.drafts.append(path)
                continue
            if path in control and control[path] is not None:
                if util.file_fingerprint(os.path.join(self.writer, path)) == control[path]:
                    continue
            self.violations.append(path)

    @property
    def valid(self):
        return not self.violations and self.drift is None

    def view(self):
        return {
            "status": "clean" if self.valid else "violated",
            "valid": self.valid,
            "scope_mode": "unrestricted" if self.unrestricted else "bounded",
            "violations": self.violations,
            "concept_drift": self.drift,
            "draft_allowance": self.contract["draft_allowance"],
            "draft_paths": self.drafts,
            "baseline_mode": self.baseline_mode,
            "writer_path": self.writer,
            "post_admission_paths": self.delta,
            "preexisting_paths": self.baseline["preexisting"],
            "original_preexisting_paths": self.original_preexisting,
        }


def measure_view(conn, ws, work_id, contract, lease):
    """Read-only scope summary for show/doctor; never raises for measurement failures."""
    if contract is None or lease is None:
        return {
            "status": "unknown",
            "valid": None,
            "violations": [],
            "concept_drift": None,
            "detail": "no admitted contract" if contract is None else "no lease: scope is measured for leased work",
        }
    try:
        return Measurement(conn, ws, work_id, contract, lease).view()
    except RivetError as exc:
        return {"status": "unavailable", "valid": None, "violations": [], "concept_drift": None, "detail": exc.message}
