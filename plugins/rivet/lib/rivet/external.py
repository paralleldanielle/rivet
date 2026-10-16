"""External Git result declarations and verification (spec section 12)."""

import json
import os
import re
import shutil
import tempfile
import time
from urllib.parse import urlsplit

from . import db, evidence, gitio, lifecycle, util
from .errors import RivetError

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
FIELDS = {"name", "remote", "ref", "gitlink"}
SCHEMES = {"https", "http", "ssh", "git", "file"}
SCP = re.compile(r"(?:(?P<user>[A-Za-z0-9._-]+)@)?(?P<host>[A-Za-z0-9][A-Za-z0-9.-]*):(?P<path>[^/].*|/.+)\Z")
NETWORK_BOUND = 120
SAFE_GIT = ["-c", "protocol.ext.allow=never", "-c", "core.askPass=", "-c", "credential.helper=",
            "-c", "core.sshCommand=ssh -o BatchMode=yes"]


def validate_remote(remote):
    if not isinstance(remote, str) or not remote or remote != remote.strip():
        raise RivetError("external_invalid", "remote must be a nonblank URL or path without surrounding whitespace")
    if remote.startswith("-"):
        raise RivetError("external_invalid", f"remote must not look like an option: {remote!r}")
    if "::" in remote or remote.startswith("ext:"):
        raise RivetError("external_invalid", f"command-backed remote transports are not allowed: {remote!r}")
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", remote):
        parts = urlsplit(remote)
        if parts.scheme.lower() not in SCHEMES:
            raise RivetError("external_invalid", f"unsupported remote scheme {parts.scheme!r} (allowed: {', '.join(sorted(SCHEMES))})")
        if parts.query or parts.fragment or "?" in remote or "#" in remote:
            raise RivetError("external_invalid", "remote URLs must not contain a query string or fragment")
        if parts.password is not None:
            raise RivetError("external_invalid", "remote URLs must not embed a password or token")
        if parts.username is not None:
            if parts.scheme.lower() != "ssh" or not re.fullmatch(r"[A-Za-z0-9._-]+", parts.username):
                raise RivetError("external_invalid", "remote URLs may carry only a simple SSH transport username, never credentials")
        if parts.scheme.lower() != "file" and not parts.hostname:
            raise RivetError("external_invalid", "remote URL needs a host")
        return remote
    if SCP.match(remote) and "/" not in remote.split(":", 1)[0]:
        return remote
    if ":" in remote.split("/", 1)[0]:
        raise RivetError("external_invalid", f"unrecognized remote syntax: {remote!r}")
    return remote


def validate_declarations(raw_items, root):
    """Parse --external-result JSON strings (or dicts) into sorted descriptors."""
    results, names = [], set()
    for raw in raw_items or []:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RivetError("external_invalid", f"--external-result must be a JSON object: {exc}")
        if not isinstance(raw, dict):
            raise RivetError("external_invalid", "each external result must be a JSON object")
        unknown = sorted(set(raw) - FIELDS)
        if unknown:
            raise RivetError("external_invalid", f"unknown external result fields: {', '.join(unknown)}")
        name, remote, ref = raw.get("name"), raw.get("remote"), raw.get("ref")
        if not isinstance(name, str) or not NAME.match(name):
            raise RivetError("external_invalid", f"external result name must match [A-Za-z0-9][A-Za-z0-9_.-]*: {name!r}")
        if name in names:
            raise RivetError("external_invalid", f"duplicate external result name: {name}")
        names.add(name)
        validate_remote(remote)
        if not isinstance(ref, str) or not ref.startswith("refs/") or not gitio.check_ref_format(ref, root):
            raise RivetError("external_invalid", f"ref must be a full, valid Git ref starting with refs/: {ref!r}")
        descriptor = {"name": name, "remote": remote, "ref": ref}
        if raw.get("gitlink") is not None:
            descriptor["gitlink"] = util.normalize_rel_path(raw["gitlink"], "gitlink")
        results.append(descriptor)
    return sorted(results, key=lambda d: d["name"])


def _resolve_remote(remote, controller):
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", remote) or (SCP.match(remote) and "/" not in remote.split(":", 1)[0]):
        return remote
    return remote if os.path.isabs(remote) else os.path.normpath(os.path.join(controller, remote))


def check(descriptor, sha, controller, writer):
    """Measure one published result. Returns (passed, reason or None)."""
    fmt = "sha256" if len(sha) == 64 else "sha1"
    tmp = tempfile.mkdtemp(prefix="rivet-external-")
    try:
        try:
            gitio.run(["init", "--quiet", "--bare", f"--object-format={fmt}", tmp], tmp, timeout=NETWORK_BOUND)
            remote = _resolve_remote(descriptor["remote"], controller)
            fetched = gitio.run(
                [*SAFE_GIT, "fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "--",
                 remote, f"+{descriptor['ref']}:refs/rivet/target"],
                tmp, check=False, timeout=NETWORK_BOUND,
            )
        except RivetError as exc:
            return False, exc.message
        if fetched.returncode != 0:
            detail = os.fsdecode(fetched.stderr).strip().splitlines()
            return False, f"could not fetch {descriptor['ref']} from {descriptor['remote']}: {detail[-1] if detail else 'fetch failed'}"
        kind = gitio.run(["cat-file", "-t", sha], tmp, check=False, timeout=NETWORK_BOUND)
        if kind.returncode != 0:
            return False, f"commit {sha} was not found in the fetched history of {descriptor['ref']}"
        if os.fsdecode(kind.stdout).strip() != "commit":
            return False, f"object {sha} is not a commit"
        try:
            reachable = gitio.is_ancestor(sha, "refs/rivet/target", tmp)
        except RivetError as exc:
            return False, exc.message
        if not reachable:
            return False, f"commit {sha} is not reachable from {descriptor['ref']}"
        if descriptor.get("gitlink"):
            path = descriptor["gitlink"]
            entries = gitio.index_entries(writer, [path]).get(path, [])
            if len(entries) != 1 or entries[0][2] != 0 or entries[0][0] != "160000" or entries[0][1] != sha:
                found = ", ".join(f"{m} {o} stage {s}" for m, o, s in entries) or "no entry"
                return False, f"parent index gitlink at {path} is {found}; expected stage-0 160000 {sha}"
        return True, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def verify_external(ws, work_id, epoch, name, commit_sha):
    sha = util.normalize_sha(commit_sha, "--commit-sha")
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        db.item(conn, work_id)
        lease = lifecycle.authenticate(conn, work_id, epoch, now, extra_seconds=NETWORK_BOUND)
        contract = db.contract(conn, work_id)
        if contract is None:
            raise RivetError("not_admitted", f"{work_id} has no admitted contract")
    descriptor = next((d for d in contract["external_results"] if d["name"] == name), None)
    if descriptor is None:
        declared = ", ".join(d["name"] for d in contract["external_results"]) or "none"
        raise RivetError("external_invalid", f"{name!r} is not a declared external result (declared: {declared})")
    started = time.monotonic()
    passed, reason = check(descriptor, sha, ws.root, lease["writer_path"])
    seconds = round(time.monotonic() - started, 3)
    subject = evidence.Subject(conn, ws, work_id, contract, db.lease(conn, work_id) or lease)
    now = ws.now()
    criterion = f"external:{name}"
    with db.write_txn(conn):
        lifecycle.authenticate(conn, work_id, epoch, now)
        invalidated = evidence.invalidate_stale(conn, work_id, subject)
        invalidated += conn.execute(
            "UPDATE evidence SET valid = 0 WHERE work_id = ? AND criterion = ? AND valid = 1",
            (work_id, criterion),
        ).rowcount
        evidence_id = evidence._insert(
            conn, work_id, subject, now,
            criterion=criterion, kind="external_result", outcome="pass" if passed else "fail",
            source="engine_measured", commit_sha=sha, elapsed_seconds=seconds, detail=reason,
        )
        db.add_event(conn, work_id, "external_verified", {"name": name, "commit_sha": sha, "passed": passed}, now)
    return {
        "id": work_id,
        "evidence_id": evidence_id,
        "criterion": criterion,
        "outcome": "pass" if passed else "fail",
        "source": "engine_measured",
        "commit_sha": sha,
        "duration_seconds": seconds,
        "invalidated": invalidated,
        "detail": reason,
    }


def recheck(ws, conn, work_id, contract, subject, epoch):
    """Repeat live checks for current external passes before completion.

    A failure invalidates the old pass and records a measured failure with its
    reason, even though the caller will then refuse to complete.
    Returns [{"criterion", "detail"}] for failures.
    """
    failures = []
    for descriptor in contract["external_results"]:
        criterion = f"external:{descriptor['name']}"
        row = conn.execute(
            "SELECT commit_sha FROM evidence WHERE work_id = ? AND criterion = ? AND valid = 1 AND outcome = 'pass' "
            "AND source = 'engine_measured' AND kind = 'external_result' ORDER BY rowid DESC LIMIT 1",
            (work_id, criterion),
        ).fetchone()
        if row is None:
            continue
        passed, reason = check(descriptor, row["commit_sha"], ws.root, subject.measurement.writer)
        if passed:
            continue
        now = ws.now()
        with db.write_txn(conn):
            lifecycle.authenticate(conn, work_id, epoch, now)
            conn.execute(
                "UPDATE evidence SET valid = 0 WHERE work_id = ? AND criterion = ? AND valid = 1",
                (work_id, criterion),
            )
            evidence._insert(
                conn, work_id, subject, now,
                criterion=criterion, kind="external_result", outcome="fail", source="engine_measured",
                commit_sha=row["commit_sha"], detail=f"completion recheck failed: {reason}",
            )
            db.add_event(conn, work_id, "external_recheck_failed", {"name": descriptor["name"], "reason": reason}, now)
        failures.append({"criterion": criterion, "detail": reason})
    return failures


def latest_failures(conn, work_id, contract, satisfied):
    """Unsatisfied external criteria whose latest measurement failed, for blockers."""
    out = []
    for descriptor in (contract or {}).get("external_results", []):
        criterion = f"external:{descriptor['name']}"
        if criterion in satisfied:
            continue
        row = conn.execute(
            "SELECT outcome, detail FROM evidence WHERE work_id = ? AND criterion = ? AND source = 'engine_measured' "
            "ORDER BY rowid DESC LIMIT 1",
            (work_id, criterion),
        ).fetchone()
        if row is not None and row["outcome"] == "fail":
            out.append({"criterion": criterion, "detail": row["detail"] or "external result failed"})
    return out
