"""Lifecycle graph, leases, and ownership (spec sections 6 and 8)."""

import hmac
import os

from . import db, gitio, scope, snapshot, util
from .errors import RivetError

GRAPH = {
    "proposed": ("ready", "abandoned"),
    "ready": ("leased", "blocked", "abandoned"),
    "leased": ("running", "ready", "paused", "abandoned"),
    "running": ("verification", "paused", "blocked", "abandoned"),
    "verification": ("running", "paused", "abandoned", "completed"),
    "paused": ("ready", "leased", "running", "verification", "abandoned"),
    "blocked": ("ready", "abandoned"),
    "completed": (),
    "abandoned": (),
}
TERMINAL = ("completed", "abandoned")
ACTIVE = ("leased", "running", "verification")
# Generic transitions never create ownership or completion.
SPECIALIZED = {"leased": "claim", "completed": "finish or complete"}
NEEDS_LEASE = ("running", "verification", "paused")
RELINQUISH = ("ready", "blocked", "abandoned")
DEFAULT_TTL = 3600


def next_transitions(state):
    return sorted(s for s in GRAPH[state] if s not in SPECIALIZED)


def check_ttl(ttl):
    if ttl is None:
        return DEFAULT_TTL
    if ttl < 0:
        raise RivetError("invalid_argument", "--ttl-seconds must be zero or positive")
    return ttl


def authenticate(conn, work_id, epoch, now, renew=True, extra_seconds=0):
    """Require the current live epoch; renew it to at least now + TTL.

    Expired epochs are never revived. Read-only inspection does not call this.
    """
    lease = db.lease(conn, work_id)
    if lease is None:
        raise RivetError("lease_missing", f"{work_id} has no lease; claim it first")
    if not epoch or not hmac.compare_digest(str(epoch), lease["epoch"]):
        raise RivetError("epoch_mismatch", f"epoch does not match the current lease on {work_id}")
    if lease["expires_at"] <= now:
        raise RivetError(
            "lease_expired",
            f"the lease on {work_id} expired at {util.iso(lease['expires_at'])}; run the doctor workflow before continuing",
        )
    if renew:
        expires = max(lease["expires_at"], now + lease["ttl_seconds"] + extra_seconds)
        conn.execute(
            "UPDATE leases SET expires_at = ?, heartbeat_at = ? WHERE work_id = ?",
            (expires, util.iso(now), work_id),
        )
        lease["expires_at"] = expires
    return lease


def check_version(item, expected):
    if expected is None or item["version"] != expected:
        raise RivetError(
            "version_conflict",
            f"expected version {expected} but {item['id']} is at version {item['version']}; re-read it with show",
            current_version=item["version"],
        )


def set_state(conn, item, state, now, kind, detail):
    version = item["version"] + 1
    conn.execute(
        "UPDATE work_items SET state = ?, version = ?, updated_at = ? WHERE id = ? AND version = ?",
        (state, version, util.iso(now), item["id"], item["version"]),
    )
    db.add_event(conn, item["id"], kind, dict(detail, **{"from": item["state"], "to": state, "version": version}), now)
    item["state"], item["version"] = state, version
    return version


def transition(ws, work_id, to, expected_version, epoch=None, reason=None):
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        item = db.item(conn, work_id)
        check_version(item, expected_version)
        if to in SPECIALIZED:
            raise RivetError("invalid_transition", f"use {SPECIALIZED[to]} to move {work_id} to {to}; generic transition cannot")
        if to not in GRAPH.get(item["state"], ()):
            allowed = ", ".join(next_transitions(item["state"])) or "none"
            raise RivetError("invalid_transition", f"{item['state']} -> {to} is not allowed (allowed: {allowed})")
        if to == "abandoned" and not (reason or "").strip():
            raise RivetError("invalid_argument", "abandoning requires a reason")
        lease = db.lease(conn, work_id)
        if lease is not None:
            authenticate(conn, work_id, epoch, now)
        elif to in NEEDS_LEASE:
            raise RivetError("lease_missing", f"{work_id} has no lease; claim it before moving to {to}")
        if to in RELINQUISH and lease is not None:
            conn.execute("DELETE FROM leases WHERE work_id = ?", (work_id,))
        set_state(conn, item, to, now, "transition", {"reason": reason or ""})
    return {"id": work_id, "state": to, "version": item["version"], "lease_released": to in RELINQUISH and lease is not None}


def pause(ws, work_id, epoch, expected_version, reason):
    util.require_text(reason, "--reason")
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        item = db.item(conn, work_id)
        check_version(item, expected_version)
        if "paused" not in GRAPH[item["state"]]:
            raise RivetError("invalid_state", f"cannot pause {work_id} in state {item['state']}")
        authenticate(conn, work_id, epoch, now)
        set_state(conn, item, "paused", now, "paused", {"reason": reason})
    return {"id": work_id, "state": "paused", "version": item["version"], "lease_retained": True}


def release(ws, work_id, epoch, expected_version, reason=None):
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        item = db.item(conn, work_id)
        check_version(item, expected_version)
        if item["state"] not in ("leased", "paused"):
            raise RivetError("invalid_state", f"release needs leased or paused work; {work_id} is {item['state']} (pause it first)")
        authenticate(conn, work_id, epoch, now)
        conn.execute("DELETE FROM leases WHERE work_id = ?", (work_id,))
        set_state(conn, item, "ready", now, "released", {"reason": reason or ""})
    return {"id": work_id, "state": "ready", "version": item["version"]}


def abandon(ws, work_id, expected_version, reason, epoch=None):
    util.require_text(reason, "--reason")
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        item = db.item(conn, work_id)
        check_version(item, expected_version)
        if item["state"] in TERMINAL:
            raise RivetError("invalid_state", f"{work_id} is already {item['state']}")
        if db.lease(conn, work_id) is not None:
            authenticate(conn, work_id, epoch, now)
            conn.execute("DELETE FROM leases WHERE work_id = ?", (work_id,))
        set_state(conn, item, "abandoned", now, "abandoned", {"reason": reason})
    return {"id": work_id, "state": "abandoned", "version": item["version"]}


def claim(ws, work_id, owner, worktree=None, ttl=None):
    util.require_text(owner, "--owner")
    ttl = check_ttl(ttl)
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        item = db.item(conn, work_id)
        contract = db.contract(conn, work_id)
        if contract is None:
            raise RivetError("not_admitted", f"{work_id} has no admitted contract; admit it first")
        current = db.lease(conn, work_id)
        if current is not None and current["expires_at"] > now:
            raise RivetError(
                "lease_held",
                f"{work_id} is leased by {current['owner']} until {util.iso(current['expires_at'])} "
                f"({int(current['expires_at'] - now)}s remaining)",
            )
        if item["state"] not in ("ready", "paused"):
            raise RivetError("invalid_state", f"claim needs ready or paused work; {work_id} is {item['state']}")
        if item["attempts"] >= item["max_attempts"]:
            raise RivetError(
                "attempts_exhausted",
                f"{work_id} used {item['attempts']}/{item['max_attempts']} attempts; abandon it or propose follow-up work",
            )
        row = scope.writer_baseline(conn, work_id)
        base = scope.load_snapshot(row)
        writer = row["writer_path"]
        if worktree is not None and os.path.realpath(worktree) != writer:
            raise RivetError(
                "writer_mismatch",
                f"claim cannot switch the writer to {worktree}; the item's writer is {writer} (use create-worktree on untouched work)",
            )
        if not os.path.isdir(writer):
            raise RivetError("scope_unavailable", f"writer checkout is missing: {writer}")
        if item["attempts"] == 0:
            current_snap = snapshot.capture(writer)
            if snapshot.digest(current_snap) != row["digest"]:
                changed = snapshot.delta(base, current_snap)
                raise RivetError(
                    "worktree_changed",
                    "the worktree changed since admission; restore it before the first claim or start a new item",
                    changed_paths=changed,
                )
        head = gitio.head(writer)
        if current is not None:
            conn.execute("DELETE FROM leases WHERE work_id = ?", (work_id,))
        epoch = util.new_epoch()
        conn.execute(
            "INSERT INTO leases (work_id, owner, epoch, base_sha, writer_path, acquired_at, expires_at, ttl_seconds, heartbeat_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (work_id, owner.strip(), epoch, head, writer, util.iso(now), now + ttl, ttl, util.iso(now)),
        )
        conn.execute("UPDATE work_items SET attempts = attempts + 1 WHERE id = ?", (work_id,))
        item["attempts"] += 1
        set_state(conn, item, "leased", now, "claimed", {"owner": owner.strip(), "attempt": item["attempts"]})
    return {
        "id": work_id,
        "state": "leased",
        "version": item["version"],
        "epoch": epoch,
        "owner": owner.strip(),
        "base_sha": head,
        "writer_path": writer,
        "attempts": item["attempts"],
        "max_attempts": item["max_attempts"],
        "ttl_seconds": ttl,
        "expires_at": util.iso(now + ttl),
    }


def heartbeat(ws, work_id, epoch, ttl=None):
    ttl = check_ttl(ttl)
    conn = ws.connect(create=True)
    now = ws.now()
    with db.write_txn(conn):
        db.item(conn, work_id)
        authenticate(conn, work_id, epoch, now, renew=False)
        conn.execute(
            "UPDATE leases SET expires_at = ?, ttl_seconds = ?, heartbeat_at = ? WHERE work_id = ?",
            (now + ttl, ttl, util.iso(now), work_id),
        )
    return {"id": work_id, "renewed": True, "ttl_seconds": ttl, "expires_at": util.iso(now + ttl)}


def lease_view(lease, now):
    if lease is None:
        return None
    return {
        "owner": lease["owner"],
        "expired": lease["expires_at"] <= now,
        "expires_at": util.iso(lease["expires_at"]),
        "ttl_seconds_remaining": max(0, int(lease["expires_at"] - now)),
        "base_sha": lease["base_sha"],
        "writer_path": lease["writer_path"],
    }
