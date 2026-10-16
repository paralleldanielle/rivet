"""Command-line interface.

Every normal command prints exactly one JSON value on stdout and exits 0.
Domain, filesystem, and database errors print {"error": ..., "code": ...} on
stderr and exit 1. Argument errors use conventional usage text and exit 2.
Measurements (verify, verify-external, finish, check-effect, check-scope,
doctor) succeed with exit 0: read their JSON verdict fields.
"""

import argparse
import json
import os
import sqlite3
import sys

from . import VERSION
from .errors import RivetError

LIST = {"nargs": "+", "default": None}

OVERVIEW = """\
Bounded-work orchestration engine. Each command prints one JSON value; read
verdict fields (outcome, completed, authorized, valid, ok), not only the exit
code. Errors are JSON on stderr with a stable "code". `rivet COMMAND --help`.

  setup       init config config-init bootstrap
  concepts    concept-fingerprint concept-proposal
  lifecycle   start propose ready admit claim heartbeat transition pause release abandon
  evidence    verify record-evidence verify-external check-scope finish complete
  delegation  dispatch settle-dispatch propose-followup create-worktree
  effects     approve check-effect
  inspect     show status doctor export import version"""


def _evidence(parser):
    parser.add_argument("--evidence", "--required-evidence", dest="evidence", metavar="CRITERION", **LIST,
                        help="acceptance criteria names (space-separated)")


def _proposal(parser, paths_required=False):
    parser.add_argument("--objective", required=True)
    parser.add_argument("--id")
    parser.add_argument("--risk", choices=("low", "medium", "high", "critical"))
    parser.add_argument("--allowed-paths", metavar="PREFIX", required=paths_required,
                        help="repository-relative path prefixes (not globs)", **LIST)
    parser.add_argument("--effects", metavar="EFFECT", help="anticipated effects; not permissions", **LIST)
    _evidence(parser)
    parser.add_argument("--external-result", action="append", metavar="JSON",
                        help='repeatable: {"name","remote","ref"[,"gitlink"]}')
    parser.add_argument("--depends-on", metavar="ID", **LIST)
    parser.add_argument("--max-attempts", type=int)
    parser.add_argument("--approval-ref")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="rivet",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=OVERVIEW,
    )
    parser.add_argument("--repo", metavar="PATH", help="target repository (default: current directory)")
    parser.add_argument("--version", action="version", version=f"rivet {VERSION}")
    sub = parser.add_subparsers(dest="subcommand", metavar="COMMAND", help=argparse.SUPPRESS)
    sub.required = True

    def add(name, help_text=None):
        return sub.add_parser(name, description=help_text)

    add("init", "create default config, runtime state and ignore rule (keeps existing files)")
    add("config", "report configuration, enforcement and delegation readiness")
    p = add("config-init", "preview (--dry-run) or write the workspace configuration")
    p.add_argument("--profile", required=True, choices=("restricted", "orchestrated"))
    p.add_argument("--context-dir")
    p.add_argument("--max-calls", type=int)
    p.add_argument("--worker-model", action="append", metavar="NAME", help="repeatable exact host model name")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    p = add("concept-fingerprint", "SHA-256 of a concept's exact bytes; creates no state")
    p.add_argument("--concept-ref", required=True)

    p = add("concept-proposal", "read-only bound preview; --confirm <fingerprint> starts the item")
    p.add_argument("--concept-ref", required=True)
    _proposal(p, paths_required=True)
    p.add_argument("--confirm", metavar="FINGERPRINT", help="proposal_fingerprint from an unchanged preview")
    p.add_argument("--owner")
    p.add_argument("--ttl-seconds", type=int)

    p = add("propose", "create a non-concept item in proposed state")
    _proposal(p)
    p.add_argument("--concept-ref", help=argparse.SUPPRESS)

    add("ready", "proposed or ready items whose dependencies are completed (read-only)")

    p = add("admit", "freeze the contract, baseline and call budget for a ready item")
    p.add_argument("--id", required=True)
    _evidence(p)
    p.add_argument("--approval-ref")
    p.add_argument("--concept-fingerprint")
    p.add_argument("--design-fingerprint")

    p = add("start", "propose, ready, admit, claim and run one item")
    _proposal(p)
    p.add_argument("--concept-ref")
    p.add_argument("--owner")
    p.add_argument("--ttl-seconds", type=int)

    p = add("claim", "take the exclusive lease on an admitted ready or paused item")
    p.add_argument("--id", required=True)
    p.add_argument("--owner", required=True)
    p.add_argument("--worktree")
    p.add_argument("--ttl-seconds", type=int)

    p = add("heartbeat", "renew the lease with a new TTL")
    p.add_argument("--id", required=True)
    p.add_argument("--epoch", required=True)
    p.add_argument("--ttl-seconds", type=int)

    p = add("transition", "compare-and-set lifecycle change (not into leased or completed)")
    p.add_argument("--id", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--expected-version", type=int, required=True)
    p.add_argument("--epoch")
    p.add_argument("--reason")

    p = add("pause", "pause work; the lease is kept")
    for flag in ("--id", "--epoch", "--reason"):
        p.add_argument(flag, required=True)
    p.add_argument("--expected-version", type=int, required=True)

    p = add("release", "return leased or paused work to ready and drop the lease")
    p.add_argument("--id", required=True)
    p.add_argument("--epoch", required=True)
    p.add_argument("--expected-version", type=int, required=True)
    p.add_argument("--reason")

    p = add("abandon", "terminal stop; code is not reverted")
    p.add_argument("--id", required=True)
    p.add_argument("--expected-version", type=int, required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--epoch")

    p = add("dispatch", "reserve one worker call immediately before launching it")
    for flag in ("--id", "--epoch", "--model", "--adapter"):
        p.add_argument(flag, required=True)

    p = add("settle-dispatch", "settle the reserved call after the worker returns")
    p.add_argument("--id", required=True)
    p.add_argument("--epoch", required=True)
    p.add_argument("--outcome", required=True, choices=("completed", "failed"))

    p = add("verify", "run a command (after --, no shell) and record measured evidence")
    p.add_argument("--id", required=True)
    p.add_argument("--epoch", required=True)
    p.add_argument("--criterion", required=True)
    p.add_argument("--kind")
    p.add_argument("--artifact")
    p.add_argument("--timeout-seconds", type=int)
    p.add_argument("--cwd", help="directory inside the writer checkout")

    p = add("record-evidence", "record caller-reported evidence (single or --batch file|-)")
    p.add_argument("--id", required=True)
    p.add_argument("--epoch", required=True)
    p.add_argument("--criterion")
    p.add_argument("--kind")
    p.add_argument("--outcome", choices=("pass", "fail"))
    p.add_argument("--command")
    p.add_argument("--artifact")
    p.add_argument("--commit-sha")
    p.add_argument("--batch", metavar="FILE", help="JSON array of records; - reads stdin")

    p = add("verify-external", "check a published commit against a declared external result")
    for flag in ("--id", "--epoch", "--name", "--commit-sha"):
        p.add_argument(flag, required=True)

    p = add("check-scope", "measure the net delta since admission")
    p.add_argument("--id", required=True)
    p.add_argument("--epoch", required=True)

    p = add("complete", "guarded completion")
    p.add_argument("--id", required=True)
    p.add_argument("--epoch", required=True)
    p.add_argument("--expected-version", type=int, required=True)

    p = add("finish", "complete, or report structured blockers")
    p.add_argument("--id", required=True)
    p.add_argument("--epoch", required=True)

    p = add("show", "one item's actionable view (no epoch, no side effects)")
    p.add_argument("--id", required=True)

    p = add("status", "compact workspace state for handoff")
    p.add_argument("--full", action="store_true")

    p = add("doctor", "diagnostics; --fix --confirm <token> runs the suggested repair")
    p.add_argument("--fix", action="store_true")
    p.add_argument("--confirm")

    p = add("approve", "record a human/host-mediated approval for one effect")
    for flag in ("--id", "--effect", "--approver"):
        p.add_argument(flag, required=True)

    p = add("check-effect", "is this effect gated and authorized? --consume uses one approval")
    p.add_argument("--id", required=True)
    p.add_argument("--effect", required=True)
    p.add_argument("--consume", action="store_true")

    p = add("propose-followup", "quarantine an out-of-scope discovery")
    for flag in ("--id", "--epoch", "--objective"):
        p.add_argument(flag, required=True)

    p = add("create-worktree", "isolate an untouched item in a new linked worktree")
    for flag in ("--id", "--epoch", "--path"):
        p.add_argument(flag, required=True)
    p.add_argument("--branch")

    p = add("export", "JSONL checkpoint (or events with --since)")
    p.add_argument("--out")
    p.add_argument("--since")

    p = add("import", "transactional JSONL import")
    p.add_argument("--in", dest="source", required=True)

    p = add("bootstrap", "set up hosts (PATH shim, Codex agent, Cursor link) and init this repo")
    p.add_argument("--host", action="append", choices=("claude-code", "codex", "copilot", "cursor", "all"))
    p.add_argument("--link-bin", nargs="?", const="~/.local/bin", metavar="DIR")
    p.add_argument("--scope", choices=("user", "project"), default="user")
    p.add_argument("--instructions", action="store_true", help="add a rivet pointer to AGENTS.md / CLAUDE.md")
    p.add_argument("--no-init", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    add("version", "print the engine version")
    return parser


def _proposal_kwargs(args):
    return {
        "work_id": args.id,
        "risk": args.risk,
        "allowed_paths": args.allowed_paths,
        "effects": args.effects,
        "evidence": args.evidence,
        "external_results": args.external_result,
        "depends_on": args.depends_on,
        "max_attempts": args.max_attempts,
    }


def dispatch_command(args, command_argv, launcher):
    from . import (admission, completion, configure, delegation, evidence, external, lifecycle, portability,
                   views, worktree)
    from .workspace import Workspace

    name = args.subcommand
    if name == "version":
        return {"version": VERSION}
    if name == "bootstrap":
        from .hosts import bootstrap
        return bootstrap.run(args, launcher)
    ws = Workspace(cwd=args.repo, launcher=launcher)
    try:
        if name == "init":
            return configure.init(ws)
        if name == "config":
            return configure.show_config(ws)
        if name == "config-init":
            return configure.config_init(ws, args.profile, args.context_dir, args.max_calls, args.worker_model,
                                         args.replace, args.dry_run)
        if name == "concept-fingerprint":
            return admission.concept_fingerprint(ws, args.concept_ref)
        if name == "concept-proposal":
            kwargs = _proposal_kwargs(args)
            return admission.concept_proposal(ws, confirm=args.confirm, owner=args.owner, ttl=args.ttl_seconds,
                                              approval_ref=args.approval_ref, objective=args.objective,
                                              concept_ref=args.concept_ref, **kwargs)
        if name == "propose":
            return admission.propose(ws, concept_ref=args.concept_ref, approval_ref=args.approval_ref,
                                     objective=args.objective, **_proposal_kwargs(args))
        if name == "ready":
            return admission.ready(ws)
        if name == "admit":
            return admission.admit(ws, args.id, args.evidence, args.approval_ref, args.concept_fingerprint,
                                   args.design_fingerprint)
        if name == "start":
            return admission.start(ws, args.objective, concept_ref=args.concept_ref, owner=args.owner,
                                   ttl=args.ttl_seconds, approval_ref=args.approval_ref, **_proposal_kwargs(args))
        if name == "claim":
            return lifecycle.claim(ws, args.id, args.owner, args.worktree, args.ttl_seconds)
        if name == "heartbeat":
            return lifecycle.heartbeat(ws, args.id, args.epoch, args.ttl_seconds)
        if name == "transition":
            return lifecycle.transition(ws, args.id, args.to, args.expected_version, args.epoch, args.reason)
        if name == "pause":
            return lifecycle.pause(ws, args.id, args.epoch, args.expected_version, args.reason)
        if name == "release":
            return lifecycle.release(ws, args.id, args.epoch, args.expected_version, args.reason)
        if name == "abandon":
            return lifecycle.abandon(ws, args.id, args.expected_version, args.reason, args.epoch)
        if name == "dispatch":
            return delegation.dispatch(ws, args.id, args.epoch, args.model, args.adapter)
        if name == "settle-dispatch":
            return delegation.settle(ws, args.id, args.epoch, args.outcome)
        if name == "verify":
            return evidence.verify(ws, args.id, args.epoch, args.criterion, command_argv, args.kind, args.artifact,
                                   args.timeout_seconds, args.cwd)
        if name == "record-evidence":
            single = None
            if any(getattr(args, f) is not None for f in ("criterion", "kind", "outcome", "command", "artifact", "commit_sha")):
                single = {k: v for k, v in (("criterion", args.criterion), ("kind", args.kind),
                                            ("outcome", args.outcome), ("command", args.command),
                                            ("artifact", args.artifact), ("commit_sha", args.commit_sha))
                          if v is not None}
            return evidence.record(ws, args.id, args.epoch, single=single, batch=args.batch)
        if name == "verify-external":
            return external.verify_external(ws, args.id, args.epoch, args.name, args.commit_sha)
        if name == "check-scope":
            return views.check_scope(ws, args.id, args.epoch)
        if name == "complete":
            return completion.complete(ws, args.id, args.epoch, args.expected_version)
        if name == "finish":
            return completion.finish(ws, args.id, args.epoch)
        if name == "show":
            return views.show(ws, args.id)
        if name == "status":
            return views.status(ws, args.full)
        if name == "doctor":
            return views.doctor(ws, args.fix, args.confirm)
        if name == "approve":
            return delegation.approve(ws, args.id, args.effect, args.approver)
        if name == "check-effect":
            return delegation.check_effect(ws, args.id, args.effect, args.consume)
        if name == "propose-followup":
            return delegation.propose_followup(ws, args.id, args.epoch, args.objective)
        if name == "create-worktree":
            return worktree.create(ws, args.id, args.epoch, args.path, args.branch)
        if name == "export":
            return portability.export(ws, args.out, args.since)
        if name == "import":
            return portability.import_(ws, args.source)
    finally:
        ws.close()
    raise RivetError("invalid_argument", f"unknown command {name}")


def _emit(stream, payload):
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    stream.buffer.write(text.encode("utf-8", "surrogateescape"))
    stream.flush()


def main(argv=None, launcher=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command_argv = []
    if "--" in argv:
        split = argv.index("--")
        argv, command_argv = argv[:split], argv[split + 1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    if command_argv and args.subcommand != "verify":
        parser.error("arguments after -- are only accepted by verify")
    if launcher is None:
        launcher = os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else "rivet"
    try:
        result = dispatch_command(args, command_argv, launcher)
    except RivetError as exc:
        _emit(sys.stderr, exc.to_json())
        return 1
    except sqlite3.IntegrityError as exc:
        _emit(sys.stderr, {"error": f"database constraint failed: {exc}", "code": "db_constraint"})
        return 1
    except sqlite3.Error as exc:
        _emit(sys.stderr, {"error": f"database error: {exc}", "code": "db_error"})
        return 1
    except OSError as exc:
        _emit(sys.stderr, {"error": f"{exc.strerror or exc}: {exc.filename or ''}".strip(": "), "code": "os_error"})
        return 1
    except Exception as exc:  # never print a traceback in place of the JSON contract
        if os.environ.get("RIVET_DEBUG"):
            raise
        _emit(sys.stderr, {"error": f"internal error: {type(exc).__name__}: {exc}", "code": "internal_error"})
        return 1
    _emit(sys.stdout, result)
    return 0
