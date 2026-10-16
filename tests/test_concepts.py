"""Concepts and start conformance: C13–C22."""

import json
import os
import unittest

from support import RivetTestCase, sha256

CONCEPT = ".rivet/context/design.md"


class ConceptTests(RivetTestCase):
    def setUp(self):
        super().setUp()
        self.ok("config-init", "--profile", "restricted")
        self.write(CONCEPT, "# Design\n\nOutcome: VALUE becomes 2.\n")

    def preview(self, *extra, paths=("src",), evidence=("unit",)):
        args = ["concept-proposal", "--concept-ref", CONCEPT, "--objective", "implement design",
                "--allowed-paths", *paths]
        if evidence:
            args += ["--evidence", *evidence]
        return self.ok(*(args + list(extra)))

    def confirm(self, fingerprint, *extra, paths=("src",), evidence=("unit",), code=None):
        args = ["concept-proposal", "--concept-ref", CONCEPT, "--objective", "implement design",
                "--allowed-paths", *paths]
        if evidence:
            args += ["--evidence", *evidence]
        args += list(extra) + ["--confirm", fingerprint]
        return self.err(*args, code=code) if code else self.ok(*args)

    def test_c13_fingerprint_is_sha256_of_exact_bytes_and_creates_no_state(self):
        self.write(CONCEPT, b"# Design\r\nno normalization \xe2\x80\x94 bytes\n")
        out = self.ok("concept-fingerprint", "--concept-ref", CONCEPT)
        self.assertEqual(out["concept_fingerprint"], sha256(self.read(CONCEPT)))
        self.assertEqual(out["concept_ref"], CONCEPT)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".rivet", "runtime")))

    def test_c14_rejects_unsafe_concept_references(self):
        os.symlink(os.path.join(self.repo, "README.md"), os.path.join(self.repo, ".rivet/context/escape.md"))
        for ref in ("../not-a-concept.md", os.path.join(self.repo, CONCEPT), ".rivet/context/missing.md",
                    "README.md", ".rivet/context/../../README.md", ".rivet/context/escape.md"):
            with self.subTest(ref=ref):
                self.err("concept-fingerprint", "--concept-ref", ref, code="concept_invalid")

    def test_c15_preview_is_read_only(self):
        out = self.preview()
        self.assertIs(out["preview"], True)
        self.assertEqual(len(out["proposal_fingerprint"]), 64)
        self.assertEqual(out["proposal"]["concept_fingerprint"], sha256(self.read(CONCEPT)))
        self.assertEqual(out["proposal"]["allowed_paths"], ["src"])
        self.assertIn(".rivet/config.json", out["proposal"]["preexisting_paths"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".rivet", "runtime")))

    def test_c16_any_bound_change_invalidates_confirmation(self):
        self.ok("init")
        dependency = self.ok("propose", "--objective", "dep")["id"]
        external = json.dumps({"name": "lib", "remote": "https://example.invalid/lib.git", "ref": "refs/heads/main"})
        base = self.preview()["proposal_fingerprint"]
        self.confirm(base, paths=("src", "tests"), code="confirmation_mismatch")
        self.confirm(base, evidence=("unit", "lint"), code="confirmation_mismatch")
        self.confirm(base, "--depends-on", dependency, code="confirmation_mismatch")
        self.confirm(base, "--external-result", external, code="confirmation_mismatch")
        self.confirm(base, "--risk", "high", code="confirmation_mismatch")
        self.write(CONCEPT, "# Design\n\nchanged\n")
        self.confirm(base, code="confirmation_mismatch")
        fresh = self.preview()["proposal_fingerprint"]
        self.write("untracked.txt", "appeared\n")
        self.confirm(fresh, code="confirmation_mismatch")
        again = self.preview()["proposal_fingerprint"]
        started = self.confirm(again)
        self.assertTrue(self.ok("check-scope", "--id", started["id"], "--epoch", started["epoch"])["valid"])

    def test_c16_initializing_after_preview_invalidates_confirmation(self):
        fingerprint = self.preview()["proposal_fingerprint"]
        self.ok("init")
        self.confirm(fingerprint, code="confirmation_mismatch")

    def test_c17_matching_confirmation_starts_exactly_one_running_item(self):
        self.ok("init")
        preview = self.preview()
        started = self.confirm(preview["proposal_fingerprint"], "--owner", "lead", "--ttl-seconds", 900,
                               "--approval-ref", "ticket-7")
        self.assertEqual((started["state"], started["version"], started["owner"]), ("running", 4, "lead"))
        self.assertEqual(started["ttl_seconds"], 900)
        self.assertEqual(started["concept_fingerprint"], preview["concept_fingerprint"])
        self.assertTrue(started["epoch"].startswith("epoch-"))
        self.assertEqual(started["proposal_fingerprint"], preview["proposal_fingerprint"])
        self.assertEqual(self.ok("status")["by_state"], {"running": 1})
        view = self.show(started["id"])
        self.assertEqual(view["approval_ref"], "ticket-7")
        self.assertEqual(view["concept_ref"], CONCEPT)
        self.verify_pass(started["id"], started["epoch"])
        self.assertTrue(self.ok("finish", "--id", started["id"], "--epoch", started["epoch"])["completed"])

    def test_c18_direct_concept_start_without_preview(self):
        self.ok("init")
        started = self.start(concept_ref=CONCEPT, approval_ref="ok-from-human")
        self.assertEqual(started["state"], "running")
        self.assertEqual(started["concept_fingerprint"], sha256(self.read(CONCEPT)))
        self.assertEqual(self.show(started["id"])["approval_ref"], "ok-from-human")

    def test_c19_concept_start_needs_paths_and_acceptance_before_creating_work(self):
        self.ok("init")
        self.err("start", "--objective", "x", "--concept-ref", CONCEPT, "--evidence", "unit", code="bounds_required")
        self.err("start", "--objective", "x", "--concept-ref", CONCEPT, "--allowed-paths", "src", code="bounds_required")
        self.assertEqual(self.ok("ready")["ready"], [])
        self.assertEqual(self.ok("status")["by_state"], {})

    def test_c20_incomplete_dependency_leaves_ready_unadmitted_confirmed_proposal(self):
        self.ok("init")
        prerequisite = self.start(objective="prerequisite", evidence=("unit",))
        fingerprint = self.preview("--id", "after", "--depends-on", prerequisite["id"])["proposal_fingerprint"]
        error = self.confirm(fingerprint, "--id", "after", "--depends-on", prerequisite["id"], code="dependency_incomplete")
        self.assertEqual(error["id"], "after")
        view = self.show("after")
        self.assertEqual((view["state"], view["admitted"], view["lease"]), ("ready", False, None))
        self.assertEqual(view["proposed_evidence"], ["unit"])
        self.verify_pass(prerequisite["id"], prerequisite["epoch"])
        self.assertTrue(self.ok("finish", "--id", prerequisite["id"], "--epoch", prerequisite["epoch"])["completed"])
        self.err("admit", "--id", "after", "--required-evidence", "other", code="criteria_mismatch")
        admitted = self.ok("admit", "--id", "after")
        self.assertEqual(admitted["required_evidence"], ["unit"])
        claimed = self.ok("claim", "--id", "after", "--owner", "lead")
        self.ok("transition", "--id", "after", "--to", "running", "--expected-version", claimed["version"],
                "--epoch", claimed["epoch"])

    def test_c21_propose_cannot_bypass_concept_pinning(self):
        self.ok("init")
        error = self.err("propose", "--objective", "x", "--concept-ref", CONCEPT, code="concept_requires_start")
        self.assertIn("concept-proposal", error["error"])

    def test_c22_every_risk_level_completes_without_a_final_approver(self):
        self.ok("init")
        for risk in ("low", "medium", "high", "critical"):
            with self.subTest(risk=risk):
                item = self.start(objective=f"{risk} work", evidence=("review",), risk=risk)
                self.ok("record-evidence", "--id", item["id"], "--epoch", item["epoch"], "--criterion", "review",
                        "--kind", "review", "--outcome", "pass")
                done = self.ok("finish", "--id", item["id"], "--epoch", item["epoch"])
                self.assertTrue(done["completed"])
                self.assertEqual(done["risk"], risk)
                self.assertNotIn("approved_by", done)

    def test_preview_requires_initialized_or_configured_workspace(self):
        other = self.make_repo("bare")
        self.err("concept-proposal", "--concept-ref", CONCEPT, "--objective", "x", "--allowed-paths", "src",
                 "--evidence", "unit", cwd=other, code="not_initialized")


if __name__ == "__main__":
    unittest.main()
