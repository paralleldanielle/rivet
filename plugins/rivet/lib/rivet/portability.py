"""JSON Lines checkpoint/event export and transactional import (spec section 17).

Format (docs/formats.md):
  line 1: {"record_type": "rivet-export", "schema_version": 1, "mode": "checkpoint" | "events", "exported_at": ...}
  then:   {"record_type": "row", "table": <logical table>, "row": {<column>: <value>}}

Checkpoints list tables in foreign-key order. Events follow their local
sequence, which is preserved on import by inserting in file order.
"""

import json
import os
import sqlite3

from . import config, db, util
from .errors import RivetError

HEADER_TYPE = "rivet-export"
MODES = ("checkpoint", "events")
DEFAULT_OUT = os.path.join(config.RUNTIME_REL, "work.jsonl")


def _columns(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def _resolve(ws, path, default=None):
    path = path or default
    return path if os.path.isabs(path) else os.path.join(ws.root, path)


def export(ws, out=None, since=None):
    conn = ws.connect()
    target = _resolve(ws, out, DEFAULT_OUT)
    mode = "events" if since else "checkpoint"
    if since:
        try:
            bound = util.parse_iso(since)
        except ValueError:
            raise RivetError("invalid_argument", f"--since must be an ISO-8601 timestamp: {since!r}")
    lines = [{"record_type": HEADER_TYPE, "schema_version": db.DB_SCHEMA_VERSION, "mode": mode,
              "exported_at": util.iso(ws.now())}]
    count = 0
    tables = [("events", ("id",))] if since else db.TABLES
    for table, _ in tables:
        order = "seq" if table == "events" else "rowid"
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            data = dict(row)
            if table == "events":
                data.pop("seq")
                if since and util.parse_iso(data["created_at"]) <= bound:
                    continue
            lines.append({"record_type": "row", "table": table, "row": data})
            count += 1
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n")
    os.replace(tmp, target)
    result = {"exported": count, "path": target, "mode": mode}
    runtime = os.path.realpath(ws.runtime_dir)
    real = os.path.realpath(target)
    if util.within(real, ws.root) and not util.within(real, runtime):
        result["warning"] = ("the export is inside the repository and not under .rivet/runtime; it counts as a "
                             "repository change for active work and can invalidate evidence")
    return result


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            raw_lines = [line for line in handle.read().splitlines() if line.strip()]
    except OSError as exc:
        raise RivetError("import_invalid", f"cannot read {path}: {exc.strerror}")
    except UnicodeDecodeError:
        raise RivetError("import_invalid", f"{path} is not UTF-8 text")
    records = []
    for number, line in enumerate(raw_lines, 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RivetError("import_invalid", f"line {number} is not valid JSON: {exc}")
        if not isinstance(record, dict):
            raise RivetError("import_invalid", f"line {number} must be a JSON object")
        records.append(record)
    if not records:
        raise RivetError("import_invalid", "the import file is empty")
    return records


def import_(ws, source):
    path = _resolve(ws, source)
    records = _read(path)
    header = records[0]
    if header.get("record_type") != HEADER_TYPE:
        raise RivetError("import_invalid", "missing rivet-export header")
    if header.get("schema_version") != db.DB_SCHEMA_VERSION:
        raise RivetError("import_invalid", f"unsupported export schema {header.get('schema_version')!r}")
    mode = header.get("mode")
    if mode not in MODES:
        raise RivetError("import_invalid", f"unknown export mode {mode!r}")
    known = dict(db.TABLES)
    conn = ws.connect(create=True)
    columns = {table: [c for c in _columns(conn, table) if c != "seq"] for table in known}
    imported = skipped = 0
    try:
        with db.write_txn(conn):
            for number, record in enumerate(records[1:], 2):
                if record.get("record_type") != "row" or set(record) != {"record_type", "table", "row"}:
                    raise RivetError("import_invalid", f"line {number}: only row records are accepted (no deletions or merges)")
                table, row = record["table"], record["row"]
                if table not in known:
                    raise RivetError("import_invalid", f"line {number}: unknown table {table!r}")
                if mode == "events" and table != "events":
                    raise RivetError("import_invalid", f"line {number}: event-only imports cannot change {table}")
                if not isinstance(row, dict) or set(row) != set(columns[table]):
                    raise RivetError("import_invalid", f"line {number}: {table} rows need exactly the columns {', '.join(columns[table])}")
                keys = known[table]
                if any(row.get(k) is None for k in keys):
                    raise RivetError("import_invalid", f"line {number}: {table} row is missing its primary key")
                where = " AND ".join(f"{k} = ?" for k in keys)
                existing = conn.execute(f"SELECT * FROM {table} WHERE {where}", tuple(row[k] for k in keys)).fetchone()
                if existing is not None:
                    current = {k: existing[k] for k in columns[table]}
                    if current == row:
                        skipped += 1
                        continue
                    raise RivetError("import_conflict", f"line {number}: {table} row {[row[k] for k in keys]} conflicts with existing state")
                cols = columns[table]
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    tuple(row[c] for c in cols),
                )
                imported += 1
    except sqlite3.IntegrityError as exc:
        raise RivetError("import_invalid", f"import rejected by database constraints: {exc}")
    return {
        "imported": imported,
        "skipped": skipped,
        "mode": mode,
        "next": "run doctor: imported leases, writer paths and fingerprints may refer to the exporting machine",
    }
