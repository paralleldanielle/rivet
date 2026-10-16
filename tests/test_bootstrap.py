"""`rivet bootstrap` behavior in a temporary HOME."""

import os
import unittest

from support import LAUNCHER, PLUGIN, RivetTestCase


class BootstrapTests(RivetTestCase):
    def paths(self):
        return {
            "bin": os.path.join(self.home, ".local", "bin", "rivet"),
            "codex": os.path.join(self.home, ".codex", "agents", "rivet_worker.toml"),
            "cursor": os.path.join(self.home, ".cursor", "plugins", "local", "rivet"),
        }

    def test_dry_run_changes_nothing(self):
        out = self.ok("bootstrap", "--host", "all", "--link-bin", "--instructions", "--dry-run")
        self.assertTrue(all(a["status"].startswith("would_") for a in out["actions"]), out["actions"])
        for path in self.paths().values():
            self.assertFalse(os.path.lexists(path))
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".rivet")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "AGENTS.md")))
        self.assertEqual(set(out["next_steps"]), {"claude-code", "codex", "copilot", "cursor"})

    def test_full_bootstrap_is_idempotent(self):
        self.write("AGENTS.md", "# Existing guidance\n\nKeep this.\n")
        out = self.ok("bootstrap", "--host", "all", "--link-bin", "--instructions")
        paths = self.paths()
        self.assertEqual(os.path.realpath(paths["bin"]), os.path.realpath(LAUNCHER))
        self.assertEqual(os.path.realpath(paths["cursor"]), os.path.realpath(PLUGIN))
        with open(paths["codex"]) as handle:
            with open(os.path.join(PLUGIN, "codex", "agents", "rivet_worker.toml")) as source:
                self.assertEqual(handle.read(), source.read())
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".rivet", "runtime", "state.db")))
        agents = self.read("AGENTS.md").decode()
        self.assertTrue(agents.startswith("# Existing guidance\n\nKeep this.\n"))
        self.assertEqual(agents.count("<!-- rivet:begin -->"), 1)
        self.assertIn("rivet:begin", self.read("CLAUDE.md").decode())
        self.assertEqual(out["conflicts"], [])

        again = self.ok("bootstrap", "--host", "all", "--link-bin", "--instructions")
        statuses = {a["action"]: a["status"] for a in again["actions"] if a["action"] != "init"}
        self.assertEqual(set(statuses.values()), {"exists"})
        self.assertEqual(self.read("AGENTS.md").decode(), agents)

    def test_never_overwrites_unrelated_files(self):
        codex = self.paths()["codex"]
        os.makedirs(os.path.dirname(codex))
        with open(codex, "w") as handle:
            handle.write('name = "mine"\n')
        out = self.ok("bootstrap", "--host", "codex", "--no-init")
        self.assertEqual(out["conflicts"], [codex])
        with open(codex) as handle:
            self.assertEqual(handle.read(), 'name = "mine"\n')

    def test_project_scope_codex_agent(self):
        self.ok("bootstrap", "--host", "codex", "--scope", "project", "--no-init")
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".codex", "agents", "rivet_worker.toml")))

    def test_repository_writes_refused_while_admitted_work_is_unfinished(self):
        self.ok("init")
        self.start()
        self.err("bootstrap", "--instructions", code="repo_writes_blocked")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "AGENTS.md")))
        self.ok("bootstrap", "--host", "cursor", "--no-init")

    def test_runs_outside_a_repository_when_nothing_repo_scoped_is_requested(self):
        outside = os.path.join(self.base, "plain")
        os.makedirs(outside)
        out = self.ok("bootstrap", "--host", "cursor", "--no-init", cwd=outside)
        self.assertEqual(out["actions"][0]["status"], "created")
        self.err("bootstrap", cwd=outside, code="not_a_git_repo")


if __name__ == "__main__":
    unittest.main()
