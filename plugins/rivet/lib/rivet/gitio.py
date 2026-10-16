"""Git subprocess helpers.

All listings use NUL-delimited output with quoting disabled so unusual path
names survive. Prompts are disabled so the engine never blocks on input.
"""

import os
import subprocess

from .errors import RivetError

GIT_TIMEOUT = 120


def _env(extra=None):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_OPTIONAL_LOCKS", "0")
    if extra:
        env.update(extra)
    return env


def run(args, cwd, check=True, timeout=GIT_TIMEOUT, env=None, input=None):
    cmd = ["git", "-c", "core.quotepath=off", *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            input=input,
            stdin=None if input is not None else subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            env=_env(env),
        )
    except FileNotFoundError:
        raise RivetError("git_missing", "git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise RivetError("git_timeout", f"git {args[0]} timed out after {timeout}s")
    if check and result.returncode != 0:
        detail = os.fsdecode(result.stderr).strip()[-500:]
        raise RivetError("git_failed", f"git {' '.join(args[:3])} failed: {detail}")
    return result


def text(args, cwd, **kwargs):
    return os.fsdecode(run(args, cwd, **kwargs).stdout).rstrip("\n")


def split_z(data):
    return [os.fsdecode(part) for part in data.split(b"\0") if part]


def resolve_controller(cwd):
    """Return the controller checkout for ``cwd``.

    Engine state belongs to the main worktree of the repository, so a call made
    from a subdirectory or from a linked writer worktree reaches the same
    database (spec 3.1). Submodules and bare layouts fall back to their own
    top level.
    """
    top = run(["rev-parse", "--show-toplevel"], cwd, check=False)
    if top.returncode != 0:
        raise RivetError("not_a_git_repo", f"not inside a Git repository: {cwd}")
    toplevel = os.fsdecode(top.stdout).strip()
    common = run(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd, check=False)
    if common.returncode == 0:
        common_dir = os.fsdecode(common.stdout).strip()
        if os.path.basename(common_dir) == ".git":
            candidate = os.path.dirname(common_dir)
            if os.path.isdir(candidate):
                return os.path.realpath(candidate)
    return os.path.realpath(toplevel)


def head(root):
    result = run(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], root, check=False)
    if result.returncode != 0:
        return None
    return os.fsdecode(result.stdout).strip()


def is_ancestor(ancestor, descendant, cwd):
    result = run(["merge-base", "--is-ancestor", ancestor, descendant], cwd, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = os.fsdecode(result.stderr).strip()
    raise RivetError("git_failed", f"cannot check ancestry of {ancestor}: {detail}")


def check_ref_format(ref, cwd):
    return run(["check-ref-format", ref], cwd, check=False).returncode == 0


def index_entries(root, paths=None):
    """Parse ``git ls-files -s -z`` into {path: [[mode, oid, stage], ...]}."""
    args = ["ls-files", "-s", "-z"]
    if paths:
        args += ["--", *paths]
    entries = {}
    for record in split_z(run(args, root).stdout):
        meta, _, path = record.partition("\t")
        mode, oid, stage = meta.split(" ")
        entries.setdefault(path, []).append([mode, oid, int(stage)])
    for value in entries.values():
        value.sort(key=lambda item: item[2])
    return entries
