"""Writer snapshots, digests, and net deltas (spec section 9).

A snapshot records fingerprints, never contents:

    {"format": 1, "writer": <abs path>, "head": <sha or null>,
     "entries": {path: {"index": [[mode, oid, stage], ...], "worktree": {...}}},
     "preexisting": [paths dirty vs HEAD, staged, unmerged, or untracked]}

``entries`` covers every index path and every nonignored untracked path.
Worktree objects are {"type": "missing"}, {"type": "file", "sha256", "mode"},
{"type": "symlink", "target_sha256"}, or {"type": "dir"} (also submodules).
Runtime state under .rivet/runtime is never part of a snapshot.
"""

import os
import stat

from . import gitio, util
from .errors import RivetError

FORMAT = 1
ABSENT = {"index": [], "worktree": {"type": "missing"}}


def excluded(path):
    return path == ".rivet/runtime" or path.startswith(".rivet/runtime/") or path.startswith(".rivet/runtime.bak-")


def _worktree_state(root, rel, tracked):
    full = os.path.join(root, rel)
    parent = os.path.realpath(os.path.dirname(full))
    if not util.within(parent, root):
        raise RivetError("snapshot_unmeasurable", f"{rel}: parent directory resolves outside the checkout")
    try:
        info = os.lstat(full)
    except (FileNotFoundError, NotADirectoryError):
        if tracked:
            return {"type": "missing"}
        raise RivetError("snapshot_unmeasurable", f"untracked path {rel} disappeared during capture")
    except OSError as exc:
        raise RivetError("snapshot_unmeasurable", f"{rel}: cannot inspect ({exc.strerror})")
    if stat.S_ISLNK(info.st_mode):
        target = os.fsencode(os.readlink(full))
        return {"type": "symlink", "target_sha256": util.sha256_hex(target)}
    if stat.S_ISREG(info.st_mode):
        try:
            digest = util.sha256_file(full)
        except FileNotFoundError:
            if tracked:
                return {"type": "missing"}
            raise RivetError("snapshot_unmeasurable", f"untracked path {rel} disappeared during capture")
        except OSError as exc:
            raise RivetError("snapshot_unmeasurable", f"{rel}: cannot read ({exc.strerror})")
        return {"type": "file", "sha256": digest, "mode": format(stat.S_IMODE(info.st_mode) & 0o777, "o")}
    if stat.S_ISDIR(info.st_mode):
        return {"type": "dir"}
    raise RivetError("snapshot_unmeasurable", f"{rel}: unsupported file type")


def _preexisting(root, head, index, untracked):
    paths = set(untracked)
    if head:
        for args in (["diff", "--name-only", "-z", "--no-renames", "HEAD"],
                     ["diff", "--cached", "--name-only", "-z", "--no-renames", "HEAD"]):
            paths.update(gitio.split_z(gitio.run(args, root).stdout))
    else:
        paths.update(index)
    paths.update(path for path, stages in index.items() if any(s[2] != 0 for s in stages))
    return sorted(p for p in paths if not excluded(p))


def capture(root):
    root = os.path.realpath(root)
    if not os.path.isdir(root):
        raise RivetError("snapshot_unmeasurable", f"writer checkout does not exist: {root}")
    head = gitio.head(root)
    index = gitio.index_entries(root)
    untracked = [p.rstrip("/") for p in gitio.split_z(gitio.run(["ls-files", "-z", "--others", "--exclude-standard"], root).stdout)]
    untracked = [p for p in untracked if not excluded(p)]
    entries = {}
    for path in sorted(set(index) | set(untracked)):
        if excluded(path):
            continue
        entries[path] = {
            "index": index.get(path, []),
            "worktree": _worktree_state(root, path, tracked=path in index),
        }
    return {
        "format": FORMAT,
        "writer": root,
        "head": head,
        "entries": entries,
        "preexisting": _preexisting(root, head, index, untracked),
    }


def digest(snapshot):
    return util.canonical_hash(snapshot)


def state_fingerprint(baseline_digest, current_digest):
    return util.sha256_hex((baseline_digest + current_digest).encode("ascii"))


def delta(baseline, current):
    """Paths whose complete index/worktree state differs (net-state measurement)."""
    old, new = baseline["entries"], current["entries"]
    return sorted(p for p in set(old) | set(new) if old.get(p, ABSENT) != new.get(p, ABSENT))


def present(snapshot, path):
    return snapshot["entries"].get(path, ABSENT) != ABSENT
