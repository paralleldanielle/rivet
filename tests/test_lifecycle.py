"""Ownership and lifecycle conformance: C11, C12, C23–C32 (and hardening H1)."""

import os
import subprocess
import sys
import unittest

from support import LAUNCHER, RivetTestCase


class LifecycleTests(RivetTestCase):
    def setUp(self):
        super().setUp()
        self.ok("init")

    def claim(self, work_id, owner="alpha", **kwargs):
        args = ["claim", "--id", work_id, "--owner", owner]
        for key, value in kwargs.items():
            args += ["--" + key.replace("_", "-"), value]
        return self.ok(*args)

    def test_version_arithmetic(self):
        work = self.admitted()
        self.assertEqual(self.show(work)["version"], 2)
        claimed = self.claim(work)
        self.assertEqual(claimed["version"], 3)
        self.assertEqual(self.start()["version"], 4)

    def test_c11_incomplete_dependencies_block_admission_and_ready_is_read_only(self):
        first = self.ok("propose", "--objective", "first", "--allowed-paths", "src")["id"]
        second = self.ok("propose", "--objective", "second", "--allowed-paths", "src", "--depends-on", first)["id"]
        self.ok("transition", "--id", second, "--to", "ready", "--expected-version", 1)
        before = self.ok("status", "--full")["records"]["work_items"]
        listed = [i["id"] for i in self.ok("ready")["ready"]]
        self.assertIn(first, listed)
        self.assertNotIn(second, listed)
        self.assertEqual(self.ok("status", "--full")["records"]["work_items"], before)
        error = self.err("admit", "--id", second, code="dependency_incomplete")
        self.assertEqual(error["dependencies"], [first])
        self.assertIn("dependency_incomplete", [b["code"] for b in self.show(second)["blockers"]])

    def test_c12_readmission_cannot_change_the_contract(self):
        work = self.admitted(evidence=("unit",))
        before = self.show(work)
        self.err("admit", "--id", work, "--required-evidence", "other", code="already_admitted")
        self.err("admit", "--id", work, code="already_admitted")
        after = self.show(work)
        for field in ("required_evidence", "allowed_paths", "effects", "objective"):
            self.assertEqual(after[field], before[field])

    def test_c23_concurrent_claims_have_exactly_one_winner(self):
        work = self.admitted()
        procs = [
            subprocess.Popen([sys.executable, LAUNCHER, "claim", "--id", work, "--owner", f"owner-{n}"],
                             cwd=self.repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for n in range(4)
        ]
        results = [p.communicate() + (p.returncode,) for p in procs]
        self.assertEqual(sum(1 for r in results if r[2] == 0), 1, results)
        self.assertTrue(all('"lease_held"' in r[1] for r in results if r[2] != 0))
        self.assertEqual(self.show(work)["attempts"], 1)

    def test_c24_claim_preconditions(self):
        proposed = self.ok("propose", "--objective", "x", "--allowed-paths", "src")["id"]
        self.err("claim", "--id", proposed, "--owner", "a", code="not_admitted")
        work = self.admitted(max_attempts=1)
        claimed = self.claim(work)
        self.ok("transition", "--id", work, "--to", "running", "--expected-version", claimed["version"],
                "--epoch", claimed["epoch"])
        self.err("claim", "--id", work, "--owner", "b", code="lease_held")
        paused = self.ok("pause", "--id", work, "--epoch", claimed["epoch"], "--expected-version", 4, "--reason", "r")
        self.ok("release", "--id", work, "--epoch", claimed["epoch"], "--expected-version", paused["version"])
        error = self.err("claim", "--id", work, "--owner", "b", code="attempts_exhausted")
        self.assertIn("abandon", error["error"])
        blockers = self.show(work)["blockers"]
        self.assertIn({"code": "attempts_exhausted", "detail": "1/1"}, blockers)

    def test_c25_wrong_or_expired_epoch_cannot_mutate(self):
        self.ok("config-init", "--profile", "orchestrated", "--max-calls", 2, "--replace")
        item = self.start()
        work, epoch = item["id"], item["epoch"]
        bad = "epoch-" + "0" * 32
        self.err("transition", "--id", work, "--to", "verification", "--expected-version", 4, "--epoch", bad, code="epoch_mismatch")
        self.err("heartbeat", "--id", work, "--epoch", bad, code="epoch_mismatch")
        self.err("verify", "--id", work, "--epoch", bad, "--criterion", "unit", "--", sys.executable, "-c", "pass",
                 code="epoch_mismatch")
        self.err("dispatch", "--id", work, "--epoch", bad, "--model", "m", "--adapter", "a", code="epoch_mismatch")
        self.ok("dispatch", "--id", work, "--epoch", epoch, "--model", "m", "--adapter", "a")
        self.err("settle-dispatch", "--id", work, "--epoch", bad, "--outcome", "completed", code="epoch_mismatch")
        self.ok("settle-dispatch", "--id", work, "--epoch", epoch, "--outcome", "completed")
        later = 4000
        for args in (
            ("transition", "--id", work, "--to", "verification", "--expected-version", 4, "--epoch", epoch),
            ("heartbeat", "--id", work, "--epoch", epoch),
            ("verify", "--id", work, "--epoch", epoch, "--criterion", "unit", "--", sys.executable, "-c", "pass"),
            ("dispatch", "--id", work, "--epoch", epoch, "--model", "m", "--adapter", "a"),
            ("check-scope", "--id", work, "--epoch", epoch),
        ):
            with self.subTest(args=args[0]):
                self.err(*args, offset=later, code="lease_expired")
        self.assertEqual(self.show(work)["state"], "running")

    def test_zero_ttl_lease_is_immediately_expired_and_unrenewable(self):
        work = self.admitted()
        claimed = self.claim(work, ttl_seconds=0)
        self.err("heartbeat", "--id", work, "--epoch", claimed["epoch"], code="lease_expired")
        self.assertTrue(self.show(work)["lease"]["expired"])

    def test_negative_ttl_rejected_without_using_an_attempt(self):
        work = self.admitted()
        self.err("claim", "--id", work, "--owner", "a", "--ttl-seconds", -5, code="invalid_argument")
        self.assertEqual(self.show(work)["attempts"], 0)

    def test_c26_authenticated_calls_renew_and_reads_do_not(self):
        work = self.admitted()
        claimed = self.claim(work, ttl_seconds=600)
        aged = self.show(work, offset=598)["lease"]
        self.assertLess(aged["ttl_seconds_remaining"], 5)
        self.assertLess(self.show(work, offset=598)["lease"]["ttl_seconds_remaining"], 5)
        self.ok("status", offset=598)
        self.assertLess(self.show(work, offset=598)["lease"]["ttl_seconds_remaining"], 5)
        self.ok("check-scope", "--id", work, "--epoch", claimed["epoch"], offset=598)
        renewed = self.show(work, offset=598)["lease"]
        self.assertGreater(renewed["ttl_seconds_remaining"], 500)
        self.assertFalse(renewed["expired"])
        self.ok("heartbeat", "--id", work, "--epoch", claimed["epoch"], "--ttl-seconds", 60, offset=598)
        self.assertLessEqual(self.show(work, offset=598)["lease"]["ttl_seconds_remaining"], 60)

    def test_c27_stale_expected_version_cannot_mutate(self):
        item = self.start()
        error = self.err("transition", "--id", item["id"], "--to", "verification", "--expected-version", 3,
                         "--epoch", item["epoch"], code="version_conflict")
        self.assertEqual(error["current_version"], 4)
        self.err("pause", "--id", item["id"], "--epoch", item["epoch"], "--expected-version", 99, "--reason", "r",
                 code="version_conflict")
        self.err("complete", "--id", item["id"], "--epoch", item["epoch"], "--expected-version", 2,
                 code="version_conflict")
        self.assertEqual(self.show(item["id"])["state"], "running")

    def test_c28_first_claim_requires_unchanged_worktree_and_reclaim_keeps_baseline(self):
        work = self.admitted()
        self.write("scratch.txt", "new\n")
        error = self.err("claim", "--id", work, "--owner", "a", code="worktree_changed")
        self.assertEqual(error["changed_paths"], ["scratch.txt"])
        os.remove(os.path.join(self.repo, "scratch.txt"))
        claimed = self.claim(work)
        self.write("src/app.py", "VALUE = 2\n")
        self.ok("release", "--id", work, "--epoch", claimed["epoch"], "--expected-version", claimed["version"])
        reclaimed = self.claim(work, owner="beta")
        scope = self.ok("check-scope", "--id", work, "--epoch", reclaimed["epoch"])
        self.assertEqual(scope["post_admission_paths"], ["src/app.py"])
        self.assertNotIn("src/app.py", scope["preexisting_paths"])

    def test_c29_pause_keeps_the_lease(self):
        item = self.start()
        work, epoch = item["id"], item["epoch"]
        paused = self.ok("pause", "--id", work, "--epoch", epoch, "--expected-version", 4, "--reason", "redirected")
        self.assertTrue(paused["lease_retained"])
        self.err("claim", "--id", work, "--owner", "other", code="lease_held")
        resumed = self.ok("transition", "--id", work, "--to", "running", "--expected-version", paused["version"],
                          "--epoch", epoch)
        self.assertEqual(resumed["state"], "running")
        paused = self.ok("pause", "--id", work, "--epoch", epoch, "--expected-version", resumed["version"], "--reason", "r")
        released = self.ok("release", "--id", work, "--epoch", epoch, "--expected-version", paused["version"])
        self.assertEqual(released["state"], "ready")
        self.assertIsNone(self.show(work)["lease"])
        self.claim(work, owner="other")

    def test_c30_pause_release_reclaim_preserves_contract_and_counters(self):
        self.ok("config-init", "--profile", "orchestrated", "--max-calls", 3, "--replace")
        item = self.start()
        work, epoch = item["id"], item["epoch"]
        self.ok("dispatch", "--id", work, "--epoch", epoch, "--model", "m", "--adapter", "codex")
        self.ok("settle-dispatch", "--id", work, "--epoch", epoch, "--outcome", "failed")
        before = self.show(work)
        paused = self.ok("pause", "--id", work, "--epoch", epoch, "--expected-version", 4, "--reason", "handoff")
        self.ok("release", "--id", work, "--epoch", epoch, "--expected-version", paused["version"])
        again = self.claim(work, owner="beta")
        self.assertNotEqual(again["epoch"], epoch)
        after = self.show(work)
        self.assertEqual(after["attempts"], before["attempts"] + 1)
        self.assertEqual(after["budgets"], before["budgets"])
        for field in ("required_evidence", "allowed_paths", "objective"):
            self.assertEqual(after[field], before[field])

    def test_c31_abandon_removes_lease_without_reverting_code(self):
        item = self.start()
        self.write("src/app.py", "VALUE = 99\n")
        self.err("abandon", "--id", item["id"], "--expected-version", 4, "--reason", "r", code="epoch_mismatch")
        out = self.ok("abandon", "--id", item["id"], "--expected-version", 4, "--epoch", item["epoch"], "--reason", "stop")
        self.assertEqual(out["state"], "abandoned")
        self.assertIsNone(self.show(item["id"])["lease"])
        self.assertEqual(self.read("src/app.py"), b"VALUE = 99\n")
        self.err("transition", "--id", item["id"], "--to", "ready", "--expected-version", out["version"],
                 code="invalid_transition")
        self.err("claim", "--id", item["id"], "--owner", "x", code="invalid_state")
        self.err("abandon", "--id", item["id"], "--expected-version", out["version"], "--reason", "again",
                 code="invalid_state")

    def test_c32_followups_are_quarantined(self):
        item = self.start()
        before = self.show(item["id"])
        followup = self.ok("propose-followup", "--id", item["id"], "--epoch", item["epoch"], "--objective", "later")
        self.assertEqual(followup["state"], "proposed")
        after = self.show(item["id"])
        self.assertEqual(after["followups"], [{"id": followup["id"], "objective": "later", "state": "proposed"}])
        for field in ("required_evidence", "allowed_paths", "version", "state"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(self.ok("status")["open_followups"], 1)
        self.assertNotIn(followup["id"], [i["id"] for i in self.ok("ready")["ready"]])
        self.err("show", "--id", followup["id"], code="not_found")

    def test_h1_generic_transition_cannot_create_ownership_or_completion(self):
        work = self.admitted(evidence=("tests",))
        self.err("transition", "--id", work, "--to", "leased", "--expected-version", 2, code="invalid_transition")
        self.err("transition", "--id", work, "--to", "running", "--expected-version", 2, code="invalid_transition")
        claimed = self.claim(work)
        epoch = claimed["epoch"]
        version = self.ok("transition", "--id", work, "--to", "running", "--expected-version", 3, "--epoch", epoch)["version"]
        version = self.ok("transition", "--id", work, "--to", "verification", "--expected-version", version, "--epoch", epoch)["version"]
        version = self.ok("transition", "--id", work, "--to", "human_review", "--expected-version", version, "--epoch", epoch)["version"]
        self.err("transition", "--id", work, "--to", "completed", "--expected-version", version, "--epoch", epoch,
                 code="invalid_transition")
        view = self.show(work)
        self.assertEqual(view["state"], "human_review")
        self.assertEqual(view["evidence"]["missing"], ["tests"])
        self.assertNotIn("completed", view["next_transitions"])

    def test_transition_to_ready_or_blocked_relinquishes_the_lease(self):
        work = self.admitted()
        claimed = self.claim(work)
        out = self.ok("transition", "--id", work, "--to", "ready", "--expected-version", 3, "--epoch", claimed["epoch"])
        self.assertTrue(out["lease_released"])
        second = self.claim(work, owner="beta")
        self.assertNotEqual(second["epoch"], claimed["epoch"])
        running = self.ok("transition", "--id", work, "--to", "running", "--expected-version", second["version"],
                          "--epoch", second["epoch"])
        blocked = self.ok("transition", "--id", work, "--to", "blocked", "--expected-version", running["version"],
                          "--epoch", second["epoch"], "--reason", "waiting")
        self.assertEqual(blocked["state"], "blocked")
        self.assertIsNone(self.show(work)["lease"])

    def test_expired_lease_needs_doctor_before_abandon(self):
        item = self.start()
        work = item["id"]
        self.err("abandon", "--id", work, "--expected-version", 4, "--reason", "x", offset=4000, code="epoch_mismatch")
        self.err("abandon", "--id", work, "--expected-version", 4, "--reason", "x", "--epoch", item["epoch"],
                 offset=4000, code="lease_expired")
        doctor = self.ok("doctor", offset=4000)
        self.ok("doctor", "--fix", "--confirm", doctor["suggested_repair"]["confirm_token"], offset=4000)
        view = self.show(work, offset=4000)
        self.ok("abandon", "--id", work, "--expected-version", view["version"], "--reason", "x", offset=4000)

    def test_lease_held_error_is_readable(self):
        item = self.start()
        error = self.err("claim", "--id", item["id"], "--owner", "x", code="lease_held")
        self.assertIn("coordinator", error["error"])
        work = self.admitted()
        self.claim(work)
        held = self.err("claim", "--id", work, "--owner", "x", code="lease_held")
        self.assertRegex(held["error"], r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")


if __name__ == "__main__":
    unittest.main()
