"""Evidence: freshness, summaries, measured verification, and assertions (spec section 11)."""

import json
import os
import shlex
import signal
import subprocess
import sys
import time

from . import db, lifecycle, scope, util
from .errors import RivetError

TAIL_CHARS = 4096
DEFAULT_TIMEOUT = 1800
BATCH_FIELDS = {"criterion", "kind", "outcome", "command", "artifact", "commit_sha"}


def is_external(criterion):
    return criterion.startswith("external:")


def check_ordinary_criterion(criterion):
    util.require_text(criterion, "criterion")
    if is_external(criterion):
        raise RivetError(
            "reserved_criterion",
            f"{criterion!r} is reserved for declared external results; use verify-external",
        )
    return criterion


class Subject:
    """The evidence subject for an item right now: (claim-time HEAD, state fingerprint)."""

    def __init__(self, conn, ws, work_id, contract, lease):
        self.measurement = scope.Measurement(conn, ws, work_id, contract, lease)
        self.input_fingerprint = lease["base_sha"]
        self.diff_fingerprint = self.measurement.fingerprint


def invalidate_stale(conn, work_id, subject):
    cursor = conn.execute(
        "UPDATE evidence SET valid = 0 WHERE work_id = ? AND valid = 1 "
        "AND (input_fingerprint IS NOT ? OR diff_fingerprint IS NOT ?)",
        (work_id, subject.input_fingerprint, subject.diff_fingerprint),
    )
    return cursor.rowcount


def summary(conn, work_id, required, basis="recorded"):
    """Required, satisfied, and missing criteria with the source of each pass.

    Ordinary criteria are satisfied by any valid pass, measured or asserted
    (reference semantics; a later failure does not supersede an earlier pass).
    External criteria need a valid engine-measured external_result pass.
    """
    rows = conn.execute(
        "SELECT criterion, kind, source, commit_sha FROM evidence "
        "WHERE work_id = ? AND valid = 1 AND outcome = 'pass'",
        (work_id,),
    ).fetchall()
    sources = {}
    for row in rows:
        criterion = row["criterion"]
        if is_external(criterion):
            if row["source"] != "engine_measured" or row["kind"] != "external_result" or not row["commit_sha"]:
                continue
        if row["source"] == "engine_measured" or criterion not in sources:
            sources[criterion] = row["source"]
    satisfied = [c for c in required if c in sources]
    return {
        "required": list(required),
        "satisfied": satisfied,
        "missing": [c for c in required if c not in sources],
        "sources": {c: sources[c] for c in satisfied},
        "basis": basis,
    }


def _insert(conn, work_id, subject, now, **fields):
    evidence_id = util.new_id("ev")
    row = {
        "id": evidence_id,
        "work_id": work_id,
        "valid": 1,
        "input_fingerprint": subject.input_fingerprint,
        "diff_fingerprint": subject.diff_fingerprint,
        "created_at": util.iso(now),
    }
    row.update(fields)
    columns = ", ".join(row)
    conn.execute(f"INSERT INTO evidence ({columns}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
    return evidence_id


def _resolve_cwd(writer, cwd):
    real_writer = os.path.realpath(writer)
    if cwd is None:
        return real_writer
    candidate = os.path.realpath(cwd if os.path.isabs(cwd) else os.path.join(real_writer, cwd))
    if not util.within(candidate, real_writer):
        raise RivetError("cwd_invalid", f"--cwd must stay inside the writer checkout {real_writer}: {cwd}")
    if not os.path.isdir(candidate):
        raise RivetError("cwd_invalid", f"--cwd is not a directory: {cwd}")
    return candidate


def _run(argv, cwd, timeout):
    """Run argv without a shell. Returns (exit_code, timed_out, stdout, stderr, seconds)."""
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except (FileNotFoundError, PermissionError, NotADirectoryError, OSError) as exc:
        raise RivetError(
            "launch_failed",
            f"could not launch {argv[0]!r}: {exc.strerror or exc}. Nothing was recorded. "
            "Pass the program and its arguments separately after --; no shell is used.",
        )
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, False, out, err, time.monotonic() - started
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        return None, True, out, err, time.monotonic() - started


def verify(ws, work_id, epoch, criterion, argv, kind=None, artifact=None, timeout=None, cwd=None):
    check_ordinary_criterion(criterion)
    kind = kind or "command"
    util.require_text(kind, "--kind")
    if kind == "external_result":
        raise RivetError("reserved_criterion", "kind external_result is reserved for verify-external")
    if not argv:
        raise RivetError("invalid_argument", "verify needs a command after --")
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout
    if timeout <= 0:
        raise RivetError("invalid_argument", "--timeout-seconds must be positive")
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        db.item(conn, work_id)
        lease = lifecycle.authenticate(conn, work_id, epoch, now, extra_seconds=timeout)
        if db.contract(conn, work_id) is None:
            raise RivetError("not_admitted", f"{work_id} has no admitted contract")
    run_dir = _resolve_cwd(lease["writer_path"], cwd)
    exit_code, timed_out, out, err, seconds = _run(argv, run_dir, timeout)
    tail = (out or b"").decode("utf-8", "replace") + (err or b"").decode("utf-8", "replace")
    tail = tail[-TAIL_CHARS:]
    outcome = "pass" if exit_code == 0 and not timed_out else "fail"
    contract = db.contract(conn, work_id)
    lease = db.lease(conn, work_id) or lease
    subject = Subject(conn, ws, work_id, contract, lease)
    now = ws.now()
    with db.write_txn(conn):
        lifecycle.authenticate(conn, work_id, epoch, now)
        invalidated = invalidate_stale(conn, work_id, subject)
        command = shlex.join(argv)
        evidence_id = _insert(
            conn, work_id, subject, now,
            criterion=criterion, kind=kind, outcome=outcome, source="engine_measured",
            command=command, exit_code=exit_code, timed_out=1 if timed_out else 0,
            elapsed_seconds=round(seconds, 3), output_tail=tail, artifact=artifact,
        )
        db.add_event(conn, work_id, "evidence", {"criterion": criterion, "outcome": outcome, "source": "engine_measured"}, now)
    return {
        "id": work_id,
        "evidence_id": evidence_id,
        "criterion": criterion,
        "kind": kind,
        "outcome": outcome,
        "source": "engine_measured",
        "command": command,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_seconds": round(seconds, 3),
        "input_fingerprint": subject.input_fingerprint,
        "diff_fingerprint": subject.diff_fingerprint,
        "invalidated": invalidated,
        "artifact": artifact,
        "output_tail": tail,
    }


def _read_batch(source):
    try:
        text = sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()
    except OSError as exc:
        raise RivetError("invalid_argument", f"cannot read batch {source}: {exc.strerror}")
    try:
        records = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RivetError("invalid_argument", f"batch is not valid JSON: {exc}")
    if not isinstance(records, list) or not records:
        raise RivetError("invalid_argument", "batch must be a nonempty JSON array")
    return records


def _validate_record(record, position):
    where = f"batch record {position}" if position is not None else "evidence"
    if not isinstance(record, dict):
        raise RivetError("invalid_argument", f"{where} must be an object")
    unknown = sorted(set(record) - BATCH_FIELDS)
    if unknown:
        raise RivetError("invalid_argument", f"{where} has unknown fields: {', '.join(unknown)}")
    for field in ("criterion", "kind", "outcome"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise RivetError("invalid_argument", f"{where} needs a nonblank {field}")
    check_ordinary_criterion(record["criterion"])
    if record["kind"] == "external_result":
        raise RivetError("reserved_criterion", f"{where}: kind external_result is reserved for verify-external")
    if record["outcome"] not in ("pass", "fail"):
        raise RivetError("invalid_argument", f"{where}: outcome must be pass or fail")
    for field in ("command", "artifact"):
        if record.get(field) is not None and not isinstance(record[field], str):
            raise RivetError("invalid_argument", f"{where}: {field} must be a string")
    sha = record.get("commit_sha")
    clean = dict(record)
    if sha is not None:
        clean["commit_sha"] = util.normalize_sha(sha, f"{where} commit_sha")
    return clean


def record(ws, work_id, epoch, single=None, batch=None):
    if batch is not None and single:
        raise RivetError("invalid_argument", "use either --batch or --criterion/--kind/--outcome, not both")
    if batch is not None:
        records = [_validate_record(r, i) for i, r in enumerate(_read_batch(batch))]
    elif single:
        records = [_validate_record(single, None)]
    else:
        raise RivetError("invalid_argument", "record-evidence needs --criterion, --kind and --outcome, or --batch")
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        db.item(conn, work_id)
        lease = lifecycle.authenticate(conn, work_id, epoch, now)
        contract = db.contract(conn, work_id)
        if contract is None:
            raise RivetError("not_admitted", f"{work_id} has no admitted contract")
    subject = Subject(conn, ws, work_id, contract, lease)
    now = ws.now()
    ids = []
    with db.write_txn(conn):
        lifecycle.authenticate(conn, work_id, epoch, now)
        invalidated = invalidate_stale(conn, work_id, subject)
        for rec in records:
            ids.append(_insert(
                conn, work_id, subject, now,
                criterion=rec["criterion"], kind=rec["kind"], outcome=rec["outcome"], source="caller_reported",
                command=rec.get("command"), artifact=rec.get("artifact"), commit_sha=rec.get("commit_sha"),
            ))
        db.add_event(conn, work_id, "evidence", {"recorded": len(ids), "source": "caller_reported"}, now)
    return {
        "id": work_id,
        "evidence_ids": ids,
        "recorded": len(ids),
        "source": "caller_reported",
        "invalidated": invalidated,
        "diff_fingerprint": subject.diff_fingerprint,
    }
