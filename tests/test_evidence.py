"""Evidence and completion conformance: C45–C58."""

import json
import os
import sys
import unittest

from support import LIB, RivetTestCase


class EvidenceTests(RivetTestCase):
    def setUp(self):
        super().setUp()
        self.ok("init")
        item = self.start(evidence=("unit",))
        self.work, self.epoch = item["id"], item["epoch"]

    def verify(self, *argv, criterion="unit", extra=()):
        return self.ok("verify", "--id", self.work, "--epoch", self.epoch, "--criterion", criterion, *extra, "--", *argv)

    def evidence_rows(self):
        return self.ok("status", "--full")["records"]["evidence"]

    def test_c45_nonzero_exit_is_a_recorded_measured_failure(self):
        result = self.rivet("verify", "--id", self.work, "--epoch", self.epoch, "--criterion", "unit", "--",
                            sys.executable, "-c", "import sys; print('boom'); sys.exit(3)")
        self.assertEqual(result.returncode, 0, "recording a failure is a successful measurement")
        out = json.loads(result.stdout)
        self.assertEqual((out["outcome"], out["exit_code"], out["timed_out"]), ("fail", 3, False))
        self.assertEqual(out["source"], "engine_measured")
        self.assertIn("boom", out["output_tail"])
        self.assertEqual(self.show(self.work)["evidence"]["missing"], ["unit"])

    def test_c46_pass_and_timeout(self):
        out = self.verify(sys.executable, "-c", "print('fine')")
        self.assertEqual((out["outcome"], out["exit_code"]), ("pass", 0))
        slow = self.verify(sys.executable, "-c", "import time; time.sleep(30)", criterion="slow",
                           extra=("--timeout-seconds", "1"))
        self.assertEqual((slow["outcome"], slow["exit_code"], slow["timed_out"]), ("fail", None, True))
        self.assertLess(slow["duration_seconds"], 20)

    def test_c47_launch_failure_records_nothing(self):
        self.err("verify", "--id", self.work, "--epoch", self.epoch, "--criterion", "unit", "--",
                 "definitely-not-a-program", code="launch_failed")
        self.err("verify", "--id", self.work, "--epoch", self.epoch, "--criterion", "unit", "--", "exit 0",
                 code="launch_failed")
        self.assertEqual(self.evidence_rows(), [])

    def test_output_tail_is_bounded(self):
        out = self.verify(sys.executable, "-c", "print('x' * 10000); import sys; print('END', file=sys.stderr)")
        self.assertEqual(len(out["output_tail"]), 4096)
        self.assertTrue(out["output_tail"].rstrip().endswith("END"))

    def test_c48_verification_runs_in_the_writer_and_renews_the_lease(self):
        result = self.rivet("verify", "--id", self.work, "--epoch", self.epoch, "--criterion", "unit", "--",
                            sys.executable, "-c", "import os; print(os.getcwd())", offset=3590)
        out = json.loads(result.stdout)
        self.assertEqual(out["output_tail"].strip(), self.repo)
        self.assertGreater(self.show(self.work, offset=3590)["lease"]["ttl_seconds_remaining"], 3000)
        nested = self.ok("verify", "--id", self.work, "--epoch", self.epoch, "--criterion", "unit", "--cwd", "src",
                         "--", sys.executable, "-c", "import os; print(os.getcwd())")
        self.assertEqual(nested["output_tail"].strip(), os.path.join(self.repo, "src"))

    def test_verify_cwd_must_stay_inside_the_writer(self):
        for cwd in ("..", "/tmp", "src/../.."):
            with self.subTest(cwd=cwd):
                self.err("verify", "--id", self.work, "--epoch", self.epoch, "--criterion", "unit", "--cwd", cwd,
                         "--", sys.executable, "-c", "pass", code="cwd_invalid")
        self.assertEqual(self.evidence_rows(), [])

    def test_c49_any_repository_change_invalidates_evidence(self):
        changes = [
            lambda: self.write("src/app.py", "VALUE = 2\n"),
            lambda: self.git("add", "src/app.py"),
            lambda: self.git("commit", "-q", "-m", "commit in scope"),
            lambda: self.write("src/new_untracked.py", "a\n"),
            lambda: self.write("src/new_untracked.py", "b\n"),
        ]
        for change in changes:
            self.verify_pass(self.work, self.epoch)
            self.assertEqual(self.show(self.work)["evidence"]["missing"], [])
            change()
            finish = self.ok("finish", "--id", self.work, "--epoch", self.epoch)
            self.assertFalse(finish["completed"])
            self.assertIn({"code": "evidence_missing", "detail": "unit"}, finish["blockers"])

    def test_c50_scope_exempt_draft_still_invalidates_evidence(self):
        self.verify_pass(self.work, self.epoch)
        self.write(".rivet/context/idea.md", "later\n")
        finish = self.ok("finish", "--id", self.work, "--epoch", self.epoch)
        self.assertFalse(finish["completed"])
        self.assertTrue(finish["scope"]["valid"])
        self.assertEqual(finish["scope"]["draft_paths"], [".rivet/context/idea.md"])
        self.assertEqual([b["code"] for b in finish["blockers"]], ["evidence_missing"])

    def test_c51_measured_attribution_survives_later_assertions(self):
        self.ok("record-evidence", "--id", self.work, "--epoch", self.epoch, "--criterion", "unit",
                "--kind", "review", "--outcome", "pass")
        self.assertEqual(self.show(self.work)["evidence"]["sources"], {"unit": "caller_reported"})
        self.verify_pass(self.work, self.epoch)
        self.assertEqual(self.show(self.work)["evidence"]["sources"], {"unit": "engine_measured"})
        self.ok("record-evidence", "--id", self.work, "--epoch", self.epoch, "--criterion", "unit",
                "--kind", "review", "--outcome", "pass")
        self.assertEqual(self.show(self.work)["evidence"]["sources"], {"unit": "engine_measured"})

    def test_c52_batches_are_atomic(self):
        batch = [
            {"criterion": "design-review", "kind": "review", "outcome": "pass"},
            {"criterion": "report", "kind": "artifact", "outcome": "pass", "artifact": "artifacts/report.html"},
            {"criterion": "unit", "kind": "review", "outcome": "pass", "commit_sha": "A" * 40},
        ]
        path = self.write("../batch.json", json.dumps(batch))
        out = self.ok("record-evidence", "--id", self.work, "--epoch", self.epoch, "--batch", path)
        self.assertEqual((out["recorded"], out["source"]), (3, "caller_reported"))
        self.assertEqual(len(self.evidence_rows()), 3)
        for bad in (
            batch[:1] + [{"criterion": "x", "kind": "review", "outcome": "maybe"}],
            batch[:1] + [{"criterion": "x", "kind": "review", "outcome": "pass", "source": "engine_measured"}],
            batch[:1] + [{"criterion": "external:lib", "kind": "review", "outcome": "pass"}],
            [],
        ):
            with self.subTest(bad=bad):
                self.err("record-evidence", "--id", self.work, "--epoch", self.epoch, "--batch", "-",
                         input=json.dumps(bad))
        self.assertEqual(len(self.evidence_rows()), 3)
        self.err("record-evidence", "--id", self.work, "--epoch", self.epoch, "--batch", path,
                 "--criterion", "x", code="invalid_argument")

    def test_c53_ordinary_evidence_cannot_forge_external_or_measured(self):
        self.err("record-evidence", "--id", self.work, "--epoch", self.epoch, "--criterion", "external:lib",
                 "--kind", "external_result", "--outcome", "pass", code="reserved_criterion")
        self.err("verify", "--id", self.work, "--epoch", self.epoch, "--criterion", "external:lib", "--",
                 sys.executable, "-c", "pass", code="reserved_criterion")
        self.err("verify", "--id", self.work, "--epoch", self.epoch, "--criterion", "unit", "--kind",
                 "external_result", "--", sys.executable, "-c", "pass", code="reserved_criterion")
        self.err("record-evidence", "--id", self.work, "--epoch", self.epoch, "--criterion", "unit",
                 "--kind", "external_result", "--outcome", "pass", code="reserved_criterion")
        self.assertEqual(self.evidence_rows(), [])

    def test_c54_finish_with_missing_criteria_reports_blockers_without_advancing(self):
        before = self.show(self.work)
        result = self.rivet("finish", "--id", self.work, "--epoch", self.epoch)
        self.assertEqual(result.returncode, 0)
        finish = json.loads(result.stdout)
        self.assertIs(finish["completed"], False)
        self.assertEqual(finish["blockers"], [{"code": "evidence_missing", "detail": "unit"}])
        self.assertEqual(finish["evidence"]["missing"], ["unit"])
        after = self.show(self.work)
        self.assertEqual((after["state"], after["version"]), (before["state"], before["version"]))

    def test_c55_current_evidence_and_scope_complete_and_remove_the_lease(self):
        self.write("src/app.py", "VALUE = 2\n")
        self.verify_pass(self.work, self.epoch)
        finish = self.ok("finish", "--id", self.work, "--epoch", self.epoch)
        self.assertIs(finish["completed"], True)
        self.assertEqual((finish["state"], finish["version"]), ("completed", 6))
        self.assertEqual(finish["evidence"]["sources"], {"unit": "engine_measured"})
        view = self.show(self.work)
        self.assertIsNone(view["lease"])
        self.assertEqual(view["state"], "completed")
        self.assertEqual(self.read("src/app.py"), b"VALUE = 2\n", "completion never commits or reverts")

    def test_complete_requires_verification_state_and_reports_missing_evidence(self):
        self.err("complete", "--id", self.work, "--epoch", self.epoch, "--expected-version", 4, code="invalid_state")
        self.ok("transition", "--id", self.work, "--to", "verification", "--expected-version", 4, "--epoch", self.epoch)
        error = self.err("complete", "--id", self.work, "--epoch", self.epoch, "--expected-version", 5,
                         code="evidence_missing")
        self.assertEqual(error["missing"], ["unit"])
        self.write("stray.txt", "x\n")
        self.verify_pass(self.work, self.epoch)
        self.err("complete", "--id", self.work, "--epoch", self.epoch, "--expected-version", 5, code="scope_violation")

    def test_c56_changes_during_completion_prevent_settlement(self):
        self.verify_pass(self.work, self.epoch)
        self.ok("transition", "--id", self.work, "--to", "verification", "--expected-version", 4, "--epoch", self.epoch)
        sys.path.insert(0, LIB)
        self.addCleanup(sys.path.remove, LIB)
        from rivet import completion, external
        from rivet.errors import RivetError
        from rivet.workspace import Workspace

        original = external.recheck

        def racing_recheck(*args, **kwargs):
            self.write("src/app.py", "VALUE = 'changed mid-completion'\n")
            return []

        external.recheck = racing_recheck
        self.addCleanup(setattr, external, "recheck", original)
        ws = Workspace(cwd=self.repo)
        try:
            with self.assertRaises(RivetError) as raised:
                completion.complete(ws, self.work, self.epoch, 5)
        finally:
            ws.close()
        self.assertEqual(raised.exception.code, "worktree_changed")
        self.assertEqual(self.show(self.work)["state"], "verification")

    def test_c58_any_valid_pass_satisfies_a_criterion(self):
        """A later failure at the same fingerprints does not supersede an earlier pass (see docs/backlog.md)."""
        self.verify_pass(self.work, self.epoch)
        self.verify(sys.executable, "-c", "raise SystemExit(1)")
        view = self.show(self.work)
        self.assertEqual(view["evidence"]["satisfied"], ["unit"])
        self.assertEqual(view["evidence"]["basis"], "recorded")
        self.assertTrue(self.ok("finish", "--id", self.work, "--epoch", self.epoch)["completed"])


if __name__ == "__main__":
    unittest.main()
