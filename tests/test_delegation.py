"""Delegation and effect conformance: C59–C67."""

import json
import unittest

from support import RivetTestCase


class DelegationTests(RivetTestCase):
    def setUp(self):
        super().setUp()
        self.init("orchestrated", max_calls=2)
        item = self.start()
        self.work, self.epoch = item["id"], item["epoch"]

    def dispatch(self, code=None, model="host-model", adapter="claude-code"):
        args = ("dispatch", "--id", self.work, "--epoch", self.epoch, "--model", model, "--adapter", adapter)
        return self.err(*args, code=code) if code else self.ok(*args)

    def settle(self, outcome="completed", code=None):
        args = ("settle-dispatch", "--id", self.work, "--epoch", self.epoch, "--outcome", outcome)
        return self.err(*args, code=code) if code else self.ok(*args)

    def test_c59_restricted_rejects_dispatch_orchestrated_running_permits(self):
        self.assertEqual(self.dispatch()["reserved"], 1)
        other = self.make_repo("restricted")
        self.ok("init", cwd=other)
        item = self.ok("start", "--objective", "x", "--allowed-paths", "src", "--evidence", "unit", cwd=other)
        error = self.err("dispatch", "--id", item["id"], "--epoch", item["epoch"], "--model", "m", "--adapter", "a",
                         cwd=other, code="delegation_unavailable")
        self.assertIn("orchestrat", error["error"])

    def test_dispatch_requires_running_state_and_names(self):
        self.ok("pause", "--id", self.work, "--epoch", self.epoch, "--expected-version", 4, "--reason", "r")
        self.dispatch(code="invalid_state")
        self.ok("transition", "--id", self.work, "--to", "running", "--expected-version", 5, "--epoch", self.epoch)
        self.dispatch(code="invalid_argument", model="  ")
        self.dispatch(code="invalid_argument", adapter="")

    def test_c60_limit_two_allows_exactly_two_reservations(self):
        self.dispatch()
        self.settle()
        second = self.dispatch()
        self.assertEqual(second["remaining_calls"], 0)
        self.settle()
        error = self.dispatch(code="budget_exhausted")
        self.assertIn("2/2", error["error"])

    def test_c61_failed_settlement_consumes_a_call_and_counters_never_refund(self):
        self.dispatch()
        settled = self.settle("failed")
        self.assertEqual((settled["used"], settled["reserved"], settled["remaining_calls"]), (1, 1, 1))
        self.assertEqual(self.show(self.work)["budgets"]["delegated_calls"]["remaining"], 1)

    def test_c62_settlement_needs_an_outstanding_reservation(self):
        error = self.settle(code="settlement_invalid")
        self.assertIn("dispatch first", error["error"])
        self.dispatch()
        self.settle()
        self.settle(code="settlement_invalid")
        budget = self.show(self.work)["budgets"]["delegated_calls"]
        self.assertEqual((budget["used"], budget["reserved"]), (1, 1))

    def test_c63_item_delegation_unavailable_at_zero_remaining(self):
        self.assertIs(self.show(self.work)["delegation"]["available"], True)
        for _ in range(2):
            self.dispatch()
            self.settle()
        delegation = self.show(self.work)["delegation"]
        self.assertIs(delegation["available"], False)
        self.assertEqual(delegation["remaining_calls"], 0)
        self.assertIn("exhausted", delegation["reason"])

    def test_dispatch_uses_values_frozen_at_admission(self):
        cfg = json.loads(self.read(".rivet/config.json"))
        cfg.update(execution_profile="restricted", orchestration={"max_calls": 0, "worker_models": []})
        self.write(".rivet/config.json", json.dumps(cfg))
        self.assertEqual(self.dispatch()["limit"], 2)

    def test_h9_outstanding_reservation_blocks_completion(self):
        self.dispatch()
        self.verify_pass(self.work, self.epoch)
        finish = self.ok("finish", "--id", self.work, "--epoch", self.epoch)
        self.assertFalse(finish["completed"])
        self.assertEqual([b["code"] for b in finish["blockers"]], ["outstanding_dispatch"])
        self.assertIn("outstanding_dispatch", [b["code"] for b in self.show(self.work)["blockers"]])
        self.settle("failed")
        self.assertTrue(self.ok("finish", "--id", self.work, "--epoch", self.epoch)["completed"])

    def test_c64_gates_need_a_matching_approval(self):
        check = self.ok("check-effect", "--id", self.work, "--effect", "push")
        self.assertEqual((check["gated"], check["authorized"]), (True, False))
        other = self.start(objective="other")
        self.ok("approve", "--id", other["id"], "--effect", "push", "--approver", "human")
        self.ok("approve", "--id", self.work, "--effect", "merge", "--approver", "human")
        self.assertFalse(self.ok("check-effect", "--id", self.work, "--effect", "push")["authorized"])

    def test_c65_consumption_is_single_use(self):
        approval = self.ok("approve", "--id", self.work, "--effect", "push", "--approver", "human")
        preview = self.ok("check-effect", "--id", self.work, "--effect", "push")
        self.assertTrue(preview["authorized"])
        self.assertFalse(preview["consumed"])
        used = self.ok("check-effect", "--id", self.work, "--effect", "push", "--consume")
        self.assertEqual((used["authorized"], used["consumed"], used["approval_id"]),
                         (True, True, approval["approval_id"]))
        again = self.ok("check-effect", "--id", self.work, "--effect", "push", "--consume")
        self.assertEqual((again["authorized"], again["consumed"]), (False, False))

    def test_non_gated_effects_and_unknown_items(self):
        approval = self.ok("approve", "--id", self.work, "--effect", "lint-fix", "--approver", "human")
        check = self.ok("check-effect", "--id", self.work, "--effect", "lint-fix", "--consume")
        self.assertEqual((check["gated"], check["authorized"], check["consumed"]), (False, True, False))
        rows = self.ok("status", "--full")["records"]["approvals"]
        self.assertEqual([r["consumed"] for r in rows if r["id"] == approval["approval_id"]], [0])
        self.err("check-effect", "--id", "work-missing", "--effect", "push", code="not_found")
        self.err("approve", "--id", "work-missing", "--effect", "push", "--approver", "h", code="not_found")

    def test_c66_completion_is_not_publication_approval(self):
        self.verify_pass(self.work, self.epoch)
        self.assertTrue(self.ok("finish", "--id", self.work, "--epoch", self.epoch)["completed"])
        for effect in ("push", "pull_request", "merge"):
            self.assertFalse(self.ok("check-effect", "--id", self.work, "--effect", effect)["authorized"])

    def test_c67_engine_side_rules(self):
        """No launch after a failed reservation: dispatch fails closed and reserves nothing."""
        for _ in range(2):
            self.dispatch()
            self.settle()
        before = self.show(self.work)["budgets"]
        self.dispatch(code="budget_exhausted")
        self.assertEqual(self.show(self.work)["budgets"], before)
        events = [e["kind"] for e in self.ok("status", "--full")["records"]["events"]]
        self.assertEqual(events.count("dispatch"), 2)
        self.assertEqual(events.count("settle"), 2)


if __name__ == "__main__":
    unittest.main()
