"""SQLite state (spec section 5) and record helpers."""

import json
import os
import sqlite3
from contextlib import contextmanager

from . import util
from .errors import RivetError

DB_SCHEMA_VERSION = 1

STATES = (
    "proposed", "ready", "leased", "running", "verification",
    "paused", "blocked", "completed", "abandoned",
)
_STATE_SQL = ", ".join(f"'{s}'" for s in STATES)

SCHEMA = f"""
CREATE TABLE schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE work_items (
    id TEXT PRIMARY KEY,
    objective TEXT NOT NULL CHECK (length(trim(objective)) > 0),
    state TEXT NOT NULL CHECK (state IN ({_STATE_SQL})),
    risk TEXT NOT NULL CHECK (risk IN ('low', 'medium', 'high', 'critical')),
    allowed_paths TEXT NOT NULL,
    required_effects TEXT NOT NULL,
    concept_ref TEXT,
    concept_fingerprint TEXT,
    proposed_evidence TEXT,
    external_results TEXT NOT NULL,
    approval_ref TEXT,
    version INTEGER NOT NULL CHECK (version >= 1),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((concept_ref IS NULL) = (concept_fingerprint IS NULL))
);
CREATE TABLE dependencies (
    work_id TEXT NOT NULL REFERENCES work_items(id),
    depends_on TEXT NOT NULL REFERENCES work_items(id),
    PRIMARY KEY (work_id, depends_on),
    CHECK (work_id <> depends_on)
);
CREATE TABLE baselines (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work_items(id),
    kind TEXT NOT NULL CHECK (kind IN ('admission', 'worktree')),
    writer_path TEXT NOT NULL,
    head TEXT,
    snapshot TEXT NOT NULL,
    digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (work_id, kind)
);
CREATE TABLE contracts (
    work_id TEXT PRIMARY KEY REFERENCES work_items(id),
    objective TEXT NOT NULL,
    allowed_paths TEXT NOT NULL,
    effects TEXT NOT NULL,
    required_evidence TEXT NOT NULL,
    approval_ref TEXT,
    design_fingerprint TEXT,
    concept_ref TEXT,
    concept_fingerprint TEXT,
    external_results TEXT NOT NULL,
    draft_allowance TEXT NOT NULL,
    config_fingerprint TEXT,
    ignore_fingerprint TEXT,
    execution_profile TEXT NOT NULL CHECK (execution_profile IN ('restricted', 'orchestrated')),
    delegation_limit INTEGER NOT NULL CHECK (delegation_limit >= 0),
    worker_models TEXT NOT NULL,
    gated_effects TEXT NOT NULL,
    baseline_id TEXT NOT NULL REFERENCES baselines(id),
    admitted_at TEXT NOT NULL
);
CREATE TABLE leases (
    work_id TEXT PRIMARY KEY REFERENCES work_items(id),
    owner TEXT NOT NULL CHECK (length(trim(owner)) > 0),
    epoch TEXT NOT NULL UNIQUE,
    base_sha TEXT,
    writer_path TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    ttl_seconds INTEGER NOT NULL CHECK (ttl_seconds >= 0),
    heartbeat_at TEXT NOT NULL
);
CREATE TABLE budgets (
    work_id TEXT NOT NULL REFERENCES work_items(id),
    name TEXT NOT NULL CHECK (name = 'delegated_calls'),
    call_limit INTEGER NOT NULL CHECK (call_limit >= 0),
    reserved INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (work_id, name),
    CHECK (0 <= used AND used <= reserved AND reserved <= call_limit)
);
CREATE TABLE evidence (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work_items(id),
    criterion TEXT NOT NULL CHECK (length(trim(criterion)) > 0),
    kind TEXT NOT NULL CHECK (length(trim(kind)) > 0),
    outcome TEXT NOT NULL CHECK (outcome IN ('pass', 'fail')),
    source TEXT NOT NULL CHECK (source IN ('engine_measured', 'caller_reported')),
    valid INTEGER NOT NULL CHECK (valid IN (0, 1)),
    input_fingerprint TEXT,
    diff_fingerprint TEXT,
    command TEXT,
    exit_code INTEGER,
    timed_out INTEGER CHECK (timed_out IN (0, 1)),
    elapsed_seconds REAL,
    output_tail TEXT,
    artifact TEXT,
    commit_sha TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES work_items(id),
    effect TEXT NOT NULL CHECK (length(trim(effect)) > 0),
    approver TEXT NOT NULL CHECK (length(trim(approver)) > 0),
    created_at TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0, 1)),
    consumed_at TEXT
);
CREATE TABLE followups (
    id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL REFERENCES work_items(id),
    objective TEXT NOT NULL CHECK (length(trim(objective)) > 0),
    state TEXT NOT NULL CHECK (state = 'proposed'),
    created_at TEXT NOT NULL
);
CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    work_id TEXT,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE writer_worktrees (
    work_id TEXT PRIMARY KEY REFERENCES work_items(id),
    path TEXT NOT NULL UNIQUE,
    branch TEXT NOT NULL UNIQUE,
    base_sha TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX evidence_by_item ON evidence(work_id, criterion);
CREATE INDEX events_by_item ON events(work_id);
"""

# Logical tables in foreign-key order, with their primary-key columns. Used by
# export/import and status --full.
TABLES = (
    ("work_items", ("id",)),
    ("dependencies", ("work_id", "depends_on")),
    ("baselines", ("id",)),
    ("contracts", ("work_id",)),
    ("leases", ("work_id",)),
    ("budgets", ("work_id", "name")),
    ("evidence", ("id",)),
    ("approvals", ("id",)),
    ("followups", ("id",)),
    ("writer_worktrees", ("work_id",)),
    ("events", ("id",)),
)

JSON_COLUMNS = {
    "work_items": ("allowed_paths", "required_effects", "proposed_evidence", "external_results"),
    "contracts": ("allowed_paths", "effects", "required_evidence", "external_results", "worker_models", "gated_effects"),
    "events": ("detail",),
}


def connect(path):
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def create(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("BEGIN IMMEDIATE")
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
        ).fetchone()
        if not exists and not conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute("INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)", (str(DB_SCHEMA_VERSION),))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return conn


def schema_version(conn):
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.DatabaseError:
        return None
    return row["value"] if row else None


def check_schema(conn, path, launcher=None):
    version = schema_version(conn)
    if version != str(DB_SCHEMA_VERSION):
        hint = f"{launcher} doctor" if launcher else "rivet doctor"
        raise RivetError(
            "db_schema_unsupported",
            f"runtime database schema {version!r} is not supported (expected {DB_SCHEMA_VERSION}); "
            f"no migration is attempted. Run `{hint}` for the recovery steps.",
            path=path,
        )


@contextmanager
def write_txn(conn):
    """Serialize checks and writes: the write lock is taken up front."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def add_event(conn, work_id, kind, detail, timestamp):
    conn.execute(
        "INSERT INTO events (id, work_id, kind, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (util.new_id("evt", 8), work_id, kind, util.canonical_json(detail), util.iso(timestamp)),
    )


def decode(table, row):
    if row is None:
        return None
    data = dict(row)
    for column in JSON_COLUMNS.get(table, ()):
        if column in data and data[column] is not None:
            data[column] = json.loads(data[column])
    return data


def item(conn, work_id, required=True):
    row = decode("work_items", conn.execute("SELECT * FROM work_items WHERE id = ?", (work_id,)).fetchone())
    if row is None and required:
        raise RivetError("not_found", f"no work item with id {work_id!r}")
    return row


def contract(conn, work_id):
    return decode("contracts", conn.execute("SELECT * FROM contracts WHERE work_id = ?", (work_id,)).fetchone())


def lease(conn, work_id):
    return decode("leases", conn.execute("SELECT * FROM leases WHERE work_id = ?", (work_id,)).fetchone())


def budget(conn, work_id):
    return decode(
        "budgets",
        conn.execute("SELECT * FROM budgets WHERE work_id = ? AND name = 'delegated_calls'", (work_id,)).fetchone(),
    )


def dependencies(conn, work_id):
    rows = conn.execute(
        "SELECT d.depends_on AS id, w.state AS state FROM dependencies d "
        "JOIN work_items w ON w.id = d.depends_on WHERE d.work_id = ? ORDER BY d.depends_on",
        (work_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def baseline(conn, work_id, kind):
    return decode(
        "baselines",
        conn.execute("SELECT * FROM baselines WHERE work_id = ? AND kind = ?", (work_id, kind)).fetchone(),
    )


def worktree(conn, work_id):
    return decode("writer_worktrees", conn.execute("SELECT * FROM writer_worktrees WHERE work_id = ?", (work_id,)).fetchone())


def budget_view(row):
    if row is None:
        return {}
    return {
        "delegated_calls": {
            "limit": row["call_limit"],
            "reserved": row["reserved"],
            "used": row["used"],
            "remaining": row["call_limit"] - row["reserved"],
            "outstanding": row["reserved"] - row["used"],
            "accounting": "engine_settled",
        }
    }
