"""The target repository's Rivet workspace: paths, configuration, and database."""

import os
import shlex

from . import config, db, gitio, util
from .errors import RivetError


class Workspace:
    def __init__(self, cwd=None, launcher=None):
        self.cwd = os.path.realpath(cwd or os.getcwd())
        self.root = gitio.resolve_controller(self.cwd)
        self.rivet_dir = os.path.join(self.root, config.RIVET_DIR)
        self.runtime_dir = os.path.join(self.root, config.RUNTIME_REL)
        self.db_path = os.path.join(self.runtime_dir, "state.db")
        self.config_path = os.path.join(self.root, config.CONFIG_REL)
        self.ignore_path = os.path.join(self.root, config.IGNORE_REL)
        self.launcher = launcher
        self._config = None
        self._conn = None

    # -- invocation helpers -------------------------------------------------

    def command(self, *args):
        """An exact, directly runnable command line for this repository."""
        launcher = self.launcher or "rivet"
        return shlex.join([launcher, "--repo", self.root, *args])

    def now(self):
        return util.now()

    def rel(self, path):
        return os.path.relpath(path, self.root)

    # -- configuration -------------------------------------------------------

    def read_config(self):
        return config.read(self.config_path, root=self.root, launcher=self.launcher)

    def config(self):
        if self._config is None:
            cfg = self.read_config()
            if cfg is None:
                raise RivetError(
                    "not_initialized",
                    f"no Rivet configuration at {config.CONFIG_REL}; run `{self.command('init')}` first",
                )
            self._config = cfg
        return self._config

    # -- database ------------------------------------------------------------

    def db_exists(self):
        return os.path.exists(self.db_path)

    def connect(self, create=False):
        """Open the runtime database.

        Mutating commands pass create=True. Read-only commands never create
        state; they raise not_initialized when the database is absent.
        """
        if self._conn is not None:
            return self._conn
        if not self.db_exists():
            if not create:
                raise RivetError(
                    "not_initialized",
                    f"no Rivet runtime state in this repository; run `{self.command('init')}` first",
                )
            self.config()
            conn = db.create(self.db_path)
        else:
            conn = db.connect(self.db_path)
        db.check_schema(conn, self.db_path, launcher=self.command())
        self._conn = conn
        return conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
