"""Shared helpers: canonical JSON, hashing, identifiers, time, and path rules.

Canonical JSON (docs/formats.md) is ``json.dumps`` with sorted keys, the
separators ``,`` and ``:``, ASCII-only output (non-ASCII and undecodable path
bytes become ``\\u`` escapes), and integers only. Digests are SHA-256 of that
text encoded as ASCII.
"""

import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone

from .errors import RivetError

ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def canonical_hash(value):
    return sha256_hex(canonical_json(value).encode("ascii"))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_fingerprint(path):
    """SHA-256 of a file's bytes, or None when the file does not exist."""
    try:
        return sha256_file(path)
    except FileNotFoundError:
        return None


def new_id(prefix, nbytes=6):
    return f"{prefix}-{secrets.token_hex(nbytes)}"


def new_epoch():
    # Fencing token: unpredictable and unique per claim.
    return f"epoch-{secrets.token_hex(16)}"


def now():
    """Trusted wall clock for leases.

    RIVET_TEST_CLOCK_OFFSET (seconds) shifts it for conformance tests that age
    leases without sleeping. The database is not a security boundary, so this
    adds no new capability to a process that can already rewrite it.
    """
    offset = os.environ.get("RIVET_TEST_CLOCK_OFFSET")
    return time.time() + (float(offset) if offset else 0.0)


def iso(timestamp):
    moment = datetime.fromtimestamp(timestamp, timezone.utc)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_iso(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def require_text(value, what):
    if not isinstance(value, str) or not value.strip():
        raise RivetError("invalid_argument", f"{what} must be a nonblank string")
    return value


def validate_id(value, what="id"):
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        raise RivetError("invalid_argument", f"{what} must match [A-Za-z0-9][A-Za-z0-9_.-]*: {value!r}")
    return value


def normalize_sha(value, what="commit SHA"):
    if not isinstance(value, str) or not SHA_PATTERN.match(value):
        raise RivetError("invalid_argument", f"{what} must be a full 40- or 64-character hex object ID: {value!r}")
    return value.lower()


def normalize_rel_path(value, what="path"):
    """Validate a safe repository-relative path and drop trailing slashes.

    Rejects absolute paths, empty or '.' paths, and any '', '.' or '..'
    component. Paths are literal prefixes, never glob patterns.
    """
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise RivetError("invalid_path", f"{what} must be a nonblank relative path")
    if value.startswith("/"):
        raise RivetError("invalid_path", f"{what} must be relative to the repository root, not absolute: {value!r}")
    trimmed = value.rstrip("/")
    parts = trimmed.split("/")
    if trimmed in ("", ".") or any(part in ("", ".", "..") for part in parts):
        raise RivetError("invalid_path", f"{what} must be a safe relative path without '.', '..' or empty components: {value!r}")
    return trimmed


def path_under(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


def within(path, root):
    """True when the resolved ``path`` is ``root`` or inside it (both already real paths)."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def unique_sorted(values, what):
    result = set()
    for value in values or []:
        if not isinstance(value, str) or not value.strip():
            raise RivetError("invalid_argument", f"{what} entries must be nonblank strings")
        result.add(value.strip())
    return sorted(result)


def json_list(text):
    return json.loads(text) if text is not None else None
