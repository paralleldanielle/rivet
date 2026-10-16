"""Configuration and contract conformance: C01–C10, C85."""

import json
import os
import unittest

from support import RivetTestCase

MANDATORY = ["destructive_external", "external_compute", "merge", "pull_request", "push"]


class ConfigurationTests(RivetTestCase):
    def test_c01_init_without_configuration_uses_restricted_defaults(self):
        out = self.ok("init")
        cfg = out["config"]
        self.assertEqual(cfg["schema_version"], 1)
        self.assertEqual(cfg["execution_profile"], "restricted")
        self.assertEqual(cfg["orchestration"], {"max_calls": 0, "worker_models": []})
        self.assertEqual(cfg["gated_effects"], MANDATORY)
        self.assertEqual(cfg["context_dir"], ".rivet/context")
        self.assertIs(out["delegation"]["available"], False)
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".rivet", "runtime", "state.db")))
        self.assertIn(".rivet/runtime", self.git("status", "--porcelain", "--ignored", "--untracked-files=all"))
        self.assertNotIn("state.db", self.git("status", "--porcelain", "--untracked-files=all"))

    def test_c02_init_again_preserves_configuration_and_ignore_file(self):
        self.init("orchestrated", max_calls=3)
        before = self.read(".rivet/config.json")
        ignore = self.read(".rivet/.gitignore")
        out = self.ok("init")
        self.assertEqual(self.read(".rivet/config.json"), before)
        self.assertEqual(self.read(".rivet/.gitignore"), ignore)
        self.assertEqual(out["config"]["execution_profile"], "orchestrated")
        self.assertEqual(out["warnings"], [])

    def test_c02_existing_ignore_file_without_runtime_rule_is_kept_and_reported(self):
        self.write(".rivet/.gitignore", "*.tmp\n")
        out = self.ok("init")
        self.assertEqual(self.read(".rivet/.gitignore"), b"*.tmp\n")
        self.assertEqual([w["code"] for w in out["warnings"]], ["runtime_not_ignored"])
        self.assertIn("runtime/", out["warnings"][0]["detail"])
        doctor = self.ok("doctor")
        self.assertIn("runtime_not_ignored", [f["code"] for f in doctor["findings"]])

    def test_c03_preview_creates_no_files_or_runtime_state(self):
        out = self.ok("config-init", "--profile", "orchestrated", "--max-calls", 4, "--dry-run")
        self.assertEqual(out["action"], "create")
        self.assertIs(out["dry_run"], True)
        self.assertEqual(out["config"]["orchestration"]["max_calls"], 4)
        self.assertIs(out["delegation"]["available"], True)
        self.assertIn("enforcement", out)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".rivet")))

    def test_c04_write_orchestrated_configuration(self):
        out = self.ok("config-init", "--profile", "orchestrated", "--max-calls", 4)
        text = self.read(".rivet/config.json").decode()
        self.assertTrue(text.startswith('{\n  "schema_version": 1'))
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(json.loads(text), out["config"])
        self.assertEqual(text, out["rendered"])
        self.assertEqual(out["context_dir"], ".rivet/context")
        self.assertTrue(os.path.isdir(os.path.join(self.repo, ".rivet", "context")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".rivet", "runtime")))
        self.assertIs(out["delegation"]["available"], True)
        self.assertEqual(out["delegation"]["max_calls"], 4)

    def _config_file(self, cfg):
        self.write(".rivet/config.json", json.dumps(cfg))

    def test_c05_rejects_invalid_configuration(self):
        base = {
            "schema_version": 1, "context_dir": ".rivet/context", "execution_profile": "orchestrated",
            "orchestration": {"max_calls": 2, "worker_models": []}, "gated_effects": MANDATORY,
        }
        cases = [
            dict(base, extra=True),
            dict(base, orchestration={"max_calls": True, "worker_models": []}),
            dict(base, orchestration={"max_calls": 2, "worker_models": [], "credits": 5}),
            dict(base, context_dir="../outside"),
            dict(base, context_dir="/abs"),
            dict(base, context_dir="."),
            dict(base, orchestration={"max_calls": 2, "worker_models": ["a", " a "]}),
            dict(base, orchestration={"max_calls": 2, "worker_models": ["  "]}),
            dict(base, gated_effects=[g for g in MANDATORY if g != "push"]),
        ]
        for cfg in cases:
            with self.subTest(cfg=cfg):
                self._config_file(cfg)
                error = self.err("config", code="config_invalid")
                self.assertTrue(error["error"])
        self.err("config-init", "--profile", "orchestrated", "--max-calls", 2, "--worker-model", "x",
                 "--worker-model", " x", "--dry-run", "--replace", code="config_invalid")

    def test_c05_context_dir_symlink_escape_rejected(self):
        os.symlink(self.base, os.path.join(self.repo, "escape"))
        self.err("config-init", "--profile", "restricted", "--context-dir", "escape/ctx", code="config_invalid")

    def test_model_names_are_trimmed_sorted_and_extra_gates_kept(self):
        out = self.ok("config-init", "--profile", "orchestrated", "--max-calls", 2,
                      "--worker-model", " model-b ", "--worker-model", "model-a")
        self.assertEqual(out["config"]["orchestration"]["worker_models"], ["model-a", "model-b"])
        cfg = out["config"]
        cfg["gated_effects"] = cfg["gated_effects"] + ["deploy", "deploy"]
        self._config_file(cfg)
        self.assertEqual(self.ok("config")["config"]["gated_effects"], sorted(MANDATORY + ["deploy"]))
        replaced = self.ok("config-init", "--profile", "restricted", "--replace")
        self.assertIn("deploy", replaced["config"]["gated_effects"])

    def test_c06_profile_limits(self):
        self.err("config-init", "--profile", "orchestrated", "--dry-run", code="config_invalid")
        self.err("config-init", "--profile", "orchestrated", "--max-calls", 0, "--dry-run", code="config_invalid")
        self.err("config-init", "--profile", "restricted", "--max-calls", 2, "--dry-run", code="config_invalid")

    def test_c07_replace_requires_flag_and_is_locked_by_admitted_work(self):
        self.init("orchestrated", max_calls=2)
        before = self.read(".rivet/config.json")
        self.err("config-init", "--profile", "restricted", code="config_exists")
        preview = self.ok("config-init", "--profile", "restricted", "--replace", "--dry-run")
        self.assertEqual(preview["action"], "replace")
        self.assertEqual(self.read(".rivet/config.json"), before)
        self.admitted()
        error = self.err("config-init", "--profile", "restricted", "--replace", code="config_locked")
        self.assertEqual(len(error["work_ids"]), 1)
        self.assertEqual(self.read(".rivet/config.json"), before)

    def test_c08_unadmitted_proposal_does_not_lock_configuration(self):
        self.init("orchestrated", max_calls=2)
        self.ok("propose", "--objective", "not admitted", "--allowed-paths", "src")
        out = self.ok("config-init", "--profile", "restricted", "--replace")
        self.assertEqual(out["config"]["execution_profile"], "restricted")

    def test_c09_model_allow_list_semantics(self):
        self.init("orchestrated", max_calls=3)
        started = self.start()
        out = self.ok("dispatch", "--id", started["id"], "--epoch", started["epoch"],
                      "--model", "any-made-up-model", "--adapter", "claude-code")
        self.assertEqual(out["model"], "any-made-up-model")
        self.assertEqual((out["reserved"], out["remaining_calls"]), (1, 2))
        self.ok("settle-dispatch", "--id", started["id"], "--epoch", started["epoch"], "--outcome", "completed")

        other = self.make_repo("other")
        self.init("orchestrated", max_calls=3, models=["exact-model"], cwd=other)
        item = self.ok("start", "--objective", "x", "--allowed-paths", "src", "--evidence", "unit", cwd=other)
        self.err("dispatch", "--id", item["id"], "--epoch", item["epoch"], "--model", "exact", "--adapter", "codex",
                 cwd=other, code="model_not_allowed")
        self.err("dispatch", "--id", item["id"], "--epoch", item["epoch"], "--model", "exact-model-2", "--adapter",
                 "codex", cwd=other, code="model_not_allowed")
        self.ok("dispatch", "--id", item["id"], "--epoch", item["epoch"], "--model", "exact-model", "--adapter",
                "codex", cwd=other)

    def test_c10_admission_allocates_whole_configured_limit(self):
        self.init("orchestrated", max_calls=20)
        item = self.ok("propose", "--objective", "x", "--allowed-paths", "src")
        self.ok("transition", "--id", item["id"], "--to", "ready", "--expected-version", 1)
        self.usage_error("admit", "--id", item["id"], "--max-calls", 1)
        out = self.ok("admit", "--id", item["id"], "--required-evidence", "unit")
        self.assertEqual(out["budgets"]["delegated_calls"]["limit"], 20)
        self.assertEqual(out["delegation"]["remaining_calls"], 20)
        self.assertIs(out["delegation"]["available"], True)
        self.assertEqual(out["delegation"]["worker_models"], [])

    def test_restricted_admission_has_one_zero_budget_and_attempts_on_item(self):
        self.ok("init")
        work = self.admitted()
        view = self.show(work)
        self.assertEqual(list(view["budgets"]), ["delegated_calls"])
        self.assertEqual(view["budgets"]["delegated_calls"]["limit"], 0)
        self.assertEqual(view["budgets"]["delegated_calls"]["accounting"], "engine_settled")
        self.assertEqual((view["attempts"], view["max_attempts"]), (0, 3))
        self.assertIs(view["delegation"]["available"], False)

    def test_removed_interfaces_stay_removed(self):
        self.ok("init")
        self.usage_error("reserve-budget", "--id", "x")
        self.usage_error("settle-budget", "--id", "x")
        self.usage_error("config-init", "--profile", "restricted", "--credit-limit", 5, "--dry-run")
        self.usage_error("finish", "--id", "x", "--epoch", "y", "--approved-by", "me")
        self.usage_error("record-evidence", "--id", "x", "--epoch", "y", "--diff-fingerprint", "z")
        cfg = self.ok("config")
        self.assertNotIn("cost_tier", cfg["enforcement"])
        self.assertNotIn("attested_path", cfg["delegation"])

    def test_c85_stale_configuration_schema_fails_closed(self):
        self.ok("init")
        cfg = json.loads(self.read(".rivet/config.json"))
        cfg["schema_version"] = 2
        self._config_file(cfg)
        error = self.err("config", code="config_schema_unsupported")
        self.assertIn("config-init", error["error"])
        self.assertIn("--replace", error["error"])
        doctor = self.ok("doctor")
        self.assertIs(doctor["ok"], False)
        self.assertEqual(doctor["findings"][0]["code"], "configuration_unusable")
        self.assertIn("config-init", doctor["findings"][0]["fix"])
        replaced = self.ok("config-init", "--profile", "restricted", "--replace")
        self.assertEqual(replaced["config"]["schema_version"], 1)

    def test_c85_stale_database_schema_fails_closed_and_names_runtime(self):
        self.ok("init")
        conn = self.db()
        conn.execute("UPDATE schema_meta SET value = '0' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        error = self.err("status", code="db_schema_unsupported")
        self.assertIn("doctor", error["error"])
        doctor = self.ok("doctor")
        self.assertIs(doctor["ok"], False)
        self.assertEqual(doctor["findings"][0]["code"], "schema_mismatch")
        repair = doctor["suggested_repair"]
        self.assertIsNone(repair["confirm_token"])
        self.assertIn(os.path.join(self.repo, ".rivet", "runtime"), repair["command"])
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".rivet", "runtime", "state.db")), "never auto-reset")
        run = self.run_printed(repair["command"].replace(" && ", " ; ").split(" ; ")[0])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.run_printed(repair["command"].split(" && ")[1]).returncode, 0)
        self.assertIs(self.ok("doctor")["ok"], True)
        self.assertNotIn("runtime.bak", self.git("status", "--porcelain", "--untracked-files=all"))

    def test_read_commands_never_create_runtime_state(self):
        self.ok("config-init", "--profile", "restricted")
        status = self.ok("status")
        self.assertIs(status["initialized"], False)
        self.assertEqual(self.ok("ready"), {"initialized": False, "ready": []})
        self.err("show", "--id", "work-x", code="not_initialized")
        doctor = self.ok("doctor")
        self.assertIn("not_initialized", [f["code"] for f in doctor["findings"]])
        self.ok("concept-fingerprint", "--concept-ref", self.write_concept())
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".rivet", "runtime")))

    def write_concept(self):
        self.write(".rivet/context/c.md", "# Concept\n")
        return ".rivet/context/c.md"

    def test_engine_resolves_root_from_subdirectory_and_other_repo(self):
        self.ok("init")
        sub = os.path.join(self.repo, "src")
        self.assertEqual(self.ok("config", cwd=sub)["root"], self.repo)
        other = self.make_repo("elsewhere")
        out = self.ok("--repo", self.repo, "config", cwd=other)
        self.assertEqual(out["root"], self.repo)


if __name__ == "__main__":
    unittest.main()
