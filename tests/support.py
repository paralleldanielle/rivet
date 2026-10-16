"""Black-box test helpers: temporary Git repositories and real engine subprocesses."""

import hashlib
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest

# In-process imports of the engine (white-box tests) must not leave caches in the plugin.
sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugins", "rivet")
LAUNCHER = os.path.join(PLUGIN, "bin", "rivet")
LIB = os.path.join(PLUGIN, "lib")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class RivetTestCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="rivet-test-")
        self.addCleanup(tmp.cleanup)
        self.base = os.path.realpath(tmp.name)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        gitconfig = os.path.join(self.home, ".gitconfig")
        with open(gitconfig, "w") as handle:
            handle.write(
                "[user]\n\tname = Rivet Test\n\temail = test@example.invalid\n"
                "[init]\n\tdefaultBranch = main\n"
                "[protocol \"file\"]\n\tallow = always\n"
                "[advice]\n\tdetachedHead = false\n"
            )
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("RIVET_", "GIT_"))}
        self.env.update({
            "HOME": self.home,
            "GIT_CONFIG_GLOBAL": gitconfig,
            "GIT_CONFIG_NOSYSTEM": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.repo = self.make_repo("repo")

    # -- filesystem and git ---------------------------------------------------

    def make_repo(self, name, files=None):
        path = os.path.join(self.base, name)
        os.makedirs(path)
        self.git("init", "-q", cwd=path)
        files = files if files is not None else {
            "README.md": "hello\n",
            "src/app.py": "VALUE = 1\n",
            "tests/test_app.py": "def test():\n    pass\n",
            "docs/notes.md": "notes\n",
        }
        for rel, content in files.items():
            self.write(rel, content, cwd=path)
        self.git("add", "-A", cwd=path)
        self.git("commit", "-q", "-m", "initial", cwd=path)
        return path

    def git(self, *args, cwd=None, check=True):
        result = subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env, capture_output=True, text=True)
        if check and result.returncode != 0:
            self.fail(f"git {' '.join(args)} failed: {result.stderr}")
        return result.stdout.strip()

    def write(self, rel, content, cwd=None):
        path = os.path.join(cwd or self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(path, mode) as handle:
            handle.write(content)
        return path

    def read(self, rel, cwd=None):
        with open(os.path.join(cwd or self.repo, rel), "rb") as handle:
            return handle.read()

    def commit_all(self, message="change", cwd=None):
        self.git("add", "-A", cwd=cwd)
        self.git("commit", "-q", "-m", message, cwd=cwd)
        return self.git("rev-parse", "HEAD", cwd=cwd)

    # -- engine invocation ------------------------------------------------------

    def rivet(self, *args, cwd=None, offset=None, input=None, env=None):
        run_env = dict(self.env)
        if offset is not None:
            run_env["RIVET_TEST_CLOCK_OFFSET"] = str(offset)
        if env:
            run_env.update(env)
        return subprocess.run(
            [sys.executable, LAUNCHER, *[str(a) for a in args]],
            cwd=cwd or self.repo, env=run_env, capture_output=True, text=True, input=input,
        )

    def ok(self, *args, **kwargs):
        result = self.rivet(*args, **kwargs)
        if result.returncode != 0:
            self.fail(f"rivet {' '.join(map(str, args))} failed ({result.returncode}): {result.stderr}")
        self.assertEqual(result.stderr, "", "successful commands keep stderr empty")
        return json.loads(result.stdout)

    def err(self, *args, code=None, **kwargs):
        result = self.rivet(*args, **kwargs)
        self.assertNotEqual(result.returncode, 0, f"expected failure for {args}: {result.stdout}")
        self.assertEqual(result.stdout, "", "errors never print to stdout")
        if result.returncode == 2 and not result.stderr.lstrip().startswith("{"):
            return {"usage": result.stderr, "exit": 2}
        payload = json.loads(result.stderr)
        self.assertIn("error", payload)
        self.assertIn("code", payload)
        if code is not None:
            self.assertEqual(payload["code"], code, payload["error"])
        return payload

    def usage_error(self, *args, **kwargs):
        result = self.rivet(*args, **kwargs)
        self.assertEqual(result.returncode, 2, result.stderr)
        return result.stderr

    # -- workflows ------------------------------------------------------------------

    def init(self, profile="restricted", max_calls=None, models=(), context_dir=None, cwd=None):
        args = ["config-init", "--profile", profile]
        if max_calls is not None:
            args += ["--max-calls", max_calls]
        for model in models:
            args += ["--worker-model", model]
        if context_dir:
            args += ["--context-dir", context_dir]
        self.ok(*args, cwd=cwd)
        return self.ok("init", cwd=cwd)

    def start(self, objective="bounded work", paths=("src",), evidence=("unit",), **extra):
        args = ["start", "--objective", objective]
        if paths:
            args += ["--allowed-paths", *paths]
        if evidence:
            args += ["--evidence", *evidence]
        for key, value in extra.items():
            flag = "--" + key.replace("_", "-")
            if isinstance(value, (list, tuple)):
                if key == "external_result":
                    for item in value:
                        args += [flag, item]
                else:
                    args += [flag, *value]
            else:
                args += [flag, value]
        return self.ok(*args)

    def admitted(self, objective="admitted work", paths=("src",), evidence=("unit",), max_attempts=None):
        args = ["propose", "--objective", objective]
        if paths:
            args += ["--allowed-paths", *paths]
        if max_attempts is not None:
            args += ["--max-attempts", max_attempts]
        item = self.ok(*args)
        self.ok("transition", "--id", item["id"], "--to", "ready", "--expected-version", 1)
        admit = ["admit", "--id", item["id"]]
        if evidence:
            admit += ["--required-evidence", *evidence]
        self.ok(*admit)
        return item["id"]

    def show(self, work_id, **kwargs):
        return self.ok("show", "--id", work_id, **kwargs)

    def verify_pass(self, work_id, epoch, criterion="unit"):
        return self.ok("verify", "--id", work_id, "--epoch", epoch, "--criterion", criterion,
                       "--", sys.executable, "-c", "pass")

    def db(self, repo=None):
        conn = sqlite3.connect(os.path.join(repo or self.repo, ".rivet", "runtime", "state.db"))
        self.addCleanup(conn.close)
        return conn

    def run_printed(self, command, cwd=None):
        """Run a command string exactly as the engine printed it."""
        argv = shlex.split(command)
        if argv and argv[0].endswith("/rivet"):
            argv = [sys.executable] + argv
        return subprocess.run(argv, cwd=cwd or self.repo, env=self.env, capture_output=True, text=True)
