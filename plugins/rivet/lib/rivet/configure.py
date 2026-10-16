"""init, config, and config-init (spec section 4.2)."""

import os

from . import config, gitio
from .errors import RivetError


def _runtime_ignore_warning(ws):
    probe = os.path.join(config.RUNTIME_REL, "state.db")
    ignored = gitio.run(["check-ignore", "-q", "--no-index", probe], ws.root, check=False).returncode == 0
    if ignored:
        return None
    return {"code": "runtime_not_ignored",
            "detail": f"{config.IGNORE_REL} exists but does not ignore runtime state; add the line 'runtime/' to it"}


def init(ws):
    created = []
    cfg = ws.read_config()
    if cfg is None:
        cfg = config.default_config()
        config.write(ws.config_path, cfg)
        created.append(config.CONFIG_REL)
    context = os.path.join(ws.root, cfg["context_dir"])
    if not os.path.isdir(context):
        os.makedirs(context, exist_ok=True)
        created.append(cfg["context_dir"] + "/")
    warnings = []
    if not os.path.exists(ws.ignore_path):
        with open(ws.ignore_path, "w", encoding="utf-8") as handle:
            handle.write(config.IGNORE_CONTENT)
        created.append(config.IGNORE_REL)
    else:
        warning = _runtime_ignore_warning(ws)
        if warning:
            warnings.append(warning)
    if not ws.db_exists():
        created.append(os.path.join(config.RUNTIME_REL, "state.db"))
    ws._config = cfg
    ws.connect(create=True)
    return {
        "initialized": True,
        "root": ws.root,
        "created": created,
        "config": cfg,
        "warnings": warnings,
        "delegation": config.workspace_delegation(cfg),
    }


def show_config(ws):
    cfg = ws.config()
    return {
        "root": ws.root,
        "path": config.CONFIG_REL,
        "config": cfg,
        "enforcement": config.enforcement_report(),
        "delegation": config.workspace_delegation(cfg),
    }


def config_init(ws, profile, context_dir=None, max_calls=None, worker_models=None, replace=False, dry_run=False):
    existing = None
    exists = os.path.exists(ws.config_path)
    if exists:
        try:
            existing = ws.read_config()
        except RivetError:
            existing = None
        if not replace:
            raise RivetError("config_exists", f"{config.CONFIG_REL} already exists; review it and pass --replace to change it")
    candidate = {
        "schema_version": config.SCHEMA_VERSION,
        "context_dir": context_dir or (existing or {}).get("context_dir", config.DEFAULT_CONTEXT_DIR),
        "execution_profile": profile,
        "orchestration": {"worker_models": worker_models or []},
        "gated_effects": (existing or {}).get("gated_effects", list(config.MANDATORY_GATES)),
    }
    if max_calls is not None:
        candidate["orchestration"]["max_calls"] = max_calls
    cfg = config.validate(candidate, root=ws.root, launcher=ws.command())
    if exists and ws.db_exists():
        conn = ws.connect()
        locked = [r["id"] for r in conn.execute(
            "SELECT w.id FROM work_items w JOIN contracts c ON c.work_id = w.id "
            "WHERE w.state NOT IN ('completed', 'abandoned') ORDER BY w.id")]
        if locked:
            raise RivetError(
                "config_locked",
                f"configuration cannot be replaced while admitted work is unfinished: {', '.join(locked)}; "
                "finish or abandon it first, or use a separate workspace",
                work_ids=locked,
            )
    result = {
        "action": "replace" if exists else "create",
        "dry_run": dry_run,
        "path": config.CONFIG_REL,
        "context_dir": cfg["context_dir"],
        "config": cfg,
        "rendered": config.render(cfg),
        "enforcement": config.enforcement_report(),
        "delegation": config.workspace_delegation(cfg),
    }
    if dry_run:
        result["next"] = "after explicit human confirmation, run the same command without --dry-run, then init"
        return result
    config.write(ws.config_path, cfg)
    os.makedirs(os.path.join(ws.root, cfg["context_dir"]), exist_ok=True)
    ws._config = cfg
    result["next"] = f"run `{ws.command('init')}` to create runtime state"
    return result
