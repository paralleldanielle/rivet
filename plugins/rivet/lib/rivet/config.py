"""Workspace configuration (spec section 4)."""

import json
import os

from . import util
from .errors import RivetError

SCHEMA_VERSION = 1
PROFILES = ("restricted", "orchestrated")
MANDATORY_GATES = ("destructive_external", "external_compute", "merge", "pull_request", "push")
DEFAULT_CONTEXT_DIR = ".rivet/context"

RIVET_DIR = ".rivet"
CONFIG_REL = ".rivet/config.json"
IGNORE_REL = ".rivet/.gitignore"
RUNTIME_REL = ".rivet/runtime"
IGNORE_CONTENT = "runtime/\nruntime.bak-*/\n"

TOP_LEVEL_FIELDS = {"schema_version", "context_dir", "execution_profile", "orchestration", "gated_effects"}
ORCHESTRATION_FIELDS = {"max_calls", "worker_models"}


def default_config():
    return {
        "schema_version": SCHEMA_VERSION,
        "context_dir": DEFAULT_CONTEXT_DIR,
        "execution_profile": "restricted",
        "orchestration": {"max_calls": 0, "worker_models": []},
        "gated_effects": sorted(MANDATORY_GATES),
    }


def _fix_command(launcher):
    prefix = f"{launcher} " if launcher else "rivet "
    return f"{prefix}config-init --profile <restricted|orchestrated> --replace --dry-run"


def validate(raw, root=None, launcher=None):
    """Validate and normalize a configuration object, filling optional defaults."""
    if not isinstance(raw, dict):
        raise RivetError("config_invalid", "configuration must be a JSON object")
    unknown = sorted(set(raw) - TOP_LEVEL_FIELDS)
    if unknown:
        raise RivetError("config_invalid", f"unknown configuration fields: {', '.join(unknown)}")
    version = raw.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise RivetError(
            "config_schema_unsupported",
            f"configuration schema_version {version!r} is not supported (expected {SCHEMA_VERSION}); "
            f"no migration is attempted. Review and rewrite it with: {_fix_command(launcher)}",
            path=CONFIG_REL,
        )

    context_dir = raw.get("context_dir", DEFAULT_CONTEXT_DIR)
    try:
        context_dir = util.normalize_rel_path(context_dir, "context_dir")
    except RivetError as exc:
        raise RivetError("config_invalid", f"context_dir must be a safe relative directory: {exc.message}")
    if util.path_under(context_dir, RUNTIME_REL):
        raise RivetError("config_invalid", "context_dir must not be inside .rivet/runtime")
    if root is not None:
        real_root = os.path.realpath(root)
        resolved = os.path.realpath(os.path.join(real_root, context_dir))
        if resolved == real_root or not util.within(resolved, real_root):
            raise RivetError("config_invalid", f"context_dir resolves outside the repository: {context_dir}")

    profile = raw.get("execution_profile", "restricted")
    if profile not in PROFILES:
        raise RivetError("config_invalid", f"execution_profile must be one of {', '.join(PROFILES)}")

    orchestration = raw.get("orchestration", {})
    if not isinstance(orchestration, dict):
        raise RivetError("config_invalid", "orchestration must be an object")
    unknown = sorted(set(orchestration) - ORCHESTRATION_FIELDS)
    if unknown:
        raise RivetError("config_invalid", f"unknown orchestration fields: {', '.join(unknown)}")
    if "max_calls" in orchestration:
        max_calls = orchestration["max_calls"]
        if type(max_calls) is not int or max_calls < 0:
            raise RivetError("config_invalid", "orchestration.max_calls must be a nonnegative integer")
    elif profile == "orchestrated":
        raise RivetError("config_invalid", "the orchestrated profile requires an explicit positive orchestration.max_calls")
    else:
        max_calls = 0
    if profile == "restricted" and max_calls != 0:
        raise RivetError("config_invalid", "the restricted profile requires orchestration.max_calls = 0")
    if profile == "orchestrated" and max_calls <= 0:
        raise RivetError("config_invalid", "the orchestrated profile requires a positive orchestration.max_calls")
    models = normalize_models(orchestration.get("worker_models", []))

    gates = raw.get("gated_effects", list(MANDATORY_GATES))
    if not isinstance(gates, list) or any(not isinstance(g, str) or not g.strip() for g in gates):
        raise RivetError("config_invalid", "gated_effects must be an array of nonempty effect names")
    gates = sorted({g.strip() for g in gates})
    missing = [g for g in MANDATORY_GATES if g not in gates]
    if missing:
        raise RivetError("config_invalid", f"mandatory gates cannot be removed: {', '.join(missing)}")

    return {
        "schema_version": SCHEMA_VERSION,
        "context_dir": context_dir,
        "execution_profile": profile,
        "orchestration": {"max_calls": max_calls, "worker_models": models},
        "gated_effects": gates,
    }


def normalize_models(models):
    if not isinstance(models, list):
        raise RivetError("config_invalid", "orchestration.worker_models must be an array of model names")
    seen = []
    for name in models:
        if not isinstance(name, str) or not name.strip():
            raise RivetError("config_invalid", "worker model names must be nonblank strings")
        trimmed = name.strip()
        if trimmed in seen:
            raise RivetError("config_invalid", f"duplicate worker model name: {trimmed}")
        seen.append(trimmed)
    return sorted(seen)


def read(path, root=None, launcher=None):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return None
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RivetError("config_invalid", f"{CONFIG_REL} is not valid UTF-8 JSON: {exc}")
    return validate(raw, root=root, launcher=launcher)


def render(cfg):
    return json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"


def write(path, cfg):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(render(cfg))
    os.replace(tmp, path)


def delegation(profile, max_calls, worker_models, remaining=None):
    """Engine eligibility for worker delegation; not host capability discovery."""
    if profile != "orchestrated":
        available, reason = False, "Restricted profile: work directly; worker delegation is disabled."
    elif max_calls <= 0:
        available, reason = False, "max_calls is 0: worker delegation is disabled."
    elif remaining is not None and remaining <= 0:
        available, reason = False, "This item's delegated-call budget is exhausted; continue directly."
    else:
        available = True
        target = "an orchestrated item has calls remaining" if remaining is not None else "orchestrated profile"
        rule = "any host model may be declared" if not worker_models else "declare an exact listed model name"
        reason = f"Available ({target}): one worker at a time; {rule}."
    block = {
        "available": available,
        "max_calls": max_calls,
        "worker_models": list(worker_models),
        "reason": reason,
    }
    if remaining is not None:
        block["remaining_calls"] = remaining
    return block


def workspace_delegation(cfg):
    orch = cfg["orchestration"]
    return delegation(cfg["execution_profile"], orch["max_calls"], orch["worker_models"])


ENFORCEMENT = {
    "lease_epoch": ("tool-mediated", "lifecycle, evidence, delegation and completion calls require the live epoch"),
    "state_compare_and_set": ("tool-mediated", "lifecycle mutations require the expected version"),
    "delegated_calls": ("tool-mediated", "dispatch reserves from a per-item limit frozen at admission; counts never refund"),
    "attempts": ("tool-mediated", "claims beyond max_attempts are refused"),
    "scope_and_evidence": ("tool-mediated", "completion measures the net Git delta since admission and requires current evidence"),
    "evidence_source": ("tool-mediated", "measured results are labeled engine_measured; assertions are caller_reported"),
    "external_results": ("tool-mediated", "published commits are checked live against the declared remote ref"),
    "concept_fingerprint": ("tool-mediated", "the admitted concept's bytes are pinned and rechecked before completion"),
    "draft_capture": ("protocol-only", "any new file under context_dir is exempt from scope; agents limit this to genuine drafts"),
    "gated_effects": ("protocol-only", "approvals are recorded and consumed by the engine, but it cannot stop a host from acting without one"),
    "worker_models": ("protocol-only", "the declared model name is recorded; the engine cannot see which model actually ran"),
    "worker_concurrency": ("protocol-only", "one worker at a time and no recursive workers are host-side rules"),
    "repository_scope": ("protocol-only", "only this Git root is measured; edits are detected after the fact, not prevented"),
    "execution_profile": ("tool-mediated", "dispatch is refused unless the item was admitted under the orchestrated profile"),
    "cost": ("protocol-only", "Rivet does not measure or limit tokens, credits or time; use the host's spending controls"),
}


def enforcement_report():
    return {key: {"level": level, "note": note} for key, (level, note) in ENFORCEMENT.items()}
