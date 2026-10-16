"""Recovery, worktree, portability and inspection conformance: C76–C89."""

import json
import os
import unittest

from support import RivetTestCase

CONCEPT = ".rivet/context/design.md"


class DoctorTests(RivetTestCase):
    def setUp(self):
        super().setUp()
        self.init("orchestrated", max_calls=2)

    def test_c76_c77_expired_and_ownerless_work_is_reported_and_repaired(self):
        running = self.start(objective="running")
        work = self.admitted()
        leased = self.ok("claim", "--id", work, "--owner", "a")
        orphan = self.start(objective="orphan")
        conn = self.db()
        conn.execute("DELETE FROM leases WHERE work_id = ?", (orphan["id"],))
        conn.commit()
        later = 4000
        doctor = self.ok("doctor", offset=later)
        self.assertIs(doctor["ok"], False)
        codes = {(f["code"], f["work_id"]) for f in doctor["findings"]}
        self.assertIn(("expired_lease", running["id"]), codes)
        self.assertIn(("expired_lease", work), codes)
        self.assertIn(("orphaned_item", orphan["id"]), codes)
        self.assertEqual({f["severity"] for f in doctor["findings"] if f["code"] != "enforcement_notice"}, {"error"})
        self.assertIn("lease_expired", [b["code"] for b in self.show(running["id"], offset=later)["blockers"]])
        self.assertIn("lease_missing", [b["code"] for b in self.show(orphan["id"])["blockers"]])
        repair = doctor["suggested_repair"]
        self.assertIn(repair["confirm_token"], repair["command"])

        self.err("doctor", "--fix", offset=later, code="confirmation_required")
        self.err("doctor", "--fix", "--confirm", "repair-expired-leases", offset=later, code="repair_token_invalid")
        fixed = self.ok("doctor", "--fix", "--confirm", repair["confirm_token"], offset=later)
        moves = {r["id"]: (r["from"], r["to"]) for r in fixed["repaired"]}
        self.assertEqual(moves, {running["id"]: ("running", "paused"), work: ("leased", "ready"),
                                 orphan["id"]: ("running", "paused")})
        self.assertIs(fixed["ok"], True)
        self.assertIsNone(self.show(running["id"])["lease"])
        reclaimed = self.ok("claim", "--id", running["id"], "--owner", "new-owner", offset=later)
        self.assertEqual(reclaimed["state"], "leased")
        self.assertNotEqual(reclaimed["epoch"], running["epoch"])
        self.assertNotEqual(leased["epoch"], reclaimed["epoch"])

    def test_c78_repair_touches_only_lease_and_lifecycle_metadata(self):
        item = self.start()
        work, epoch = item["id"], item["epoch"]
        self.ok("dispatch", "--id", work, "--epoch", epoch, "--model", "m", "--adapter", "codex")
        self.ok("settle-dispatch", "--id", work, "--epoch", epoch, "--outcome", "completed")
        self.ok("approve", "--id", work, "--effect", "push", "--approver", "human")
        self.write("src/app.py", "VALUE = 'kept'\n")
        before = self.show(work)
        doctor = self.ok("doctor", offset=4000)
        self.ok("doctor", "--fix", "--confirm", doctor["suggested_repair"]["confirm_token"], offset=4000)
        after = self.show(work)
        self.assertEqual(after["attempts"], before["attempts"])
        self.assertEqual(after["budgets"], before["budgets"])
        self.assertEqual(after["required_evidence"], before["required_evidence"])
        self.assertTrue(self.ok("check-effect", "--id", work, "--effect", "push")["authorized"])
        self.assertEqual(self.read("src/app.py"), b"VALUE = 'kept'\n")
        self.assertEqual(after["version"], before["version"] + 1)

    def test_suggested_repair_runs_exactly_as_printed_from_a_subdirectory(self):
        self.start()
        command = self.ok("doctor", offset=4000)["suggested_repair"]["command"]
        self.env["RIVET_TEST_CLOCK_OFFSET"] = "4000"
        result = self.run_printed(command, cwd=os.path.join(self.repo, "src"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["repaired"]), 1)

    def test_token_is_bound_to_the_findings(self):
        self.start(objective="first")
        token = self.ok("doctor", offset=4000)["suggested_repair"]["confirm_token"]
        self.start(objective="second")
        self.err("doctor", "--fix", "--confirm", token, offset=8000, code="repair_token_invalid")

    def test_doctor_reports_cycles_scope_and_enforcement(self):
        a = self.ok("propose", "--objective", "a", "--id", "a")["id"]
        b = self.ok("propose", "--objective", "b", "--id", "b", "--depends-on", a)["id"]
        conn = self.db()
        conn.execute("INSERT INTO dependencies (work_id, depends_on) VALUES ('a', 'b')")
        conn.commit()
        item = self.start()
        self.write("outside.txt", "x\n")
        doctor = self.ok("doctor")
        codes = [f["code"] for f in doctor["findings"]]
        self.assertEqual(codes.count("dependency_cycle"), 1)
        self.assertIn("scope_violation", codes)
        self.assertEqual(codes[-1], "enforcement_notice")
        self.assertIn("protocol-only", doctor["findings"][-1]["detail"])
        self.assertEqual(doctor["enforcement"]["gated_effects"]["level"], "protocol-only")
        self.assertEqual(doctor["delegation"]["max_calls"], 2)
        self.assertNotIn("suggested_repair", doctor)
        del b, item


class WorktreeTests(RivetTestCase):
    def setUp(self):
        super().setUp()
        self.ok("init")

    def test_c79_c81_isolated_writer_has_its_own_baseline_and_survives_reclaim(self):
        self.write("note.txt", "pre-existing local edit\n")
        item = self.start()
        work, epoch = item["id"], item["epoch"]
        writer = os.path.join(self.base, "writer")
        created = self.ok("create-worktree", "--id", work, "--epoch", epoch, "--path", writer)
        self.assertEqual(created["path"], writer)
        self.assertTrue(created["branch"].startswith(f"rivet/{work}/"))
        self.write("src/feature.py", "x = 1\n", cwd=writer)
        scope = self.ok("check-scope", "--id", work, "--epoch", epoch)
        self.assertEqual(scope["baseline_mode"], "worktree")
        self.assertEqual(scope["preexisting_paths"], [])
        self.assertIn("note.txt", scope["original_preexisting_paths"])
        self.assertEqual(scope["post_admission_paths"], ["src/feature.py"])

        inside = self.ok("status", cwd=writer)
        self.assertEqual([a["id"] for a in inside["active"]], [work])
        self.assertFalse(os.path.exists(os.path.join(writer, ".rivet", "runtime")))

        out = self.ok("verify", "--id", work, "--epoch", epoch, "--criterion", "unit", "--",
                      "python3", "-c", "import os; print(os.getcwd())")
        self.assertEqual(out["output_tail"].strip(), writer)
        paused = self.ok("pause", "--id", work, "--epoch", epoch, "--expected-version", 4, "--reason", "handoff")
        self.ok("release", "--id", work, "--epoch", epoch, "--expected-version", paused["version"])
        again = self.ok("claim", "--id", work, "--owner", "beta")
        self.assertEqual(again["writer_path"], writer)
        self.assertEqual(self.show(work)["lease"]["writer_path"], writer)
        self.assertEqual(self.ok("check-scope", "--id", work, "--epoch", again["epoch"])["post_admission_paths"],
                         ["src/feature.py"])
        self.err("claim", "--id", work, "--owner", "x", "--worktree", self.repo, code="lease_held")

    def test_c80_worktree_requires_untouched_item_and_committed_concept(self):
        item = self.start()
        self.write("src/app.py", "VALUE = 2\n")
        destination = os.path.join(self.base, "w1")
        self.err("create-worktree", "--id", item["id"], "--epoch", item["epoch"], "--path", destination,
                 code="worktree_invalid")
        self.assertFalse(os.path.exists(destination))

        other = self.make_repo("concepts")
        self.ok("init", cwd=other)
        self.write(CONCEPT, "# Design\n", cwd=other)
        concept_item = self.ok("start", "--objective", "x", "--concept-ref", CONCEPT, "--allowed-paths", "src",
                               "--evidence", "unit", cwd=other)
        destination = os.path.join(self.base, "w2")
        error = self.err("create-worktree", "--id", concept_item["id"], "--epoch", concept_item["epoch"],
                         "--path", destination, cwd=other, code="worktree_invalid")
        self.assertIn("committed", error["error"])
        self.assertFalse(os.path.exists(destination))


class PortabilityTests(RivetTestCase):
    def setUp(self):
        super().setUp()
        self.ok("init")
        item = self.start()
        self.work, self.epoch = item["id"], item["epoch"]
        self.verify_pass(self.work, self.epoch)

    def export(self, *args, cwd=None):
        return self.ok("export", *args, cwd=cwd)

    def lines(self, path):
        with open(path) as handle:
            return [json.loads(line) for line in handle]

    def test_default_export_is_ignored_and_keeps_scope_and_evidence(self):
        out = self.export()
        self.assertEqual(out["path"], os.path.join(self.repo, ".rivet", "runtime", "work.jsonl"))
        self.assertNotIn("warning", out)
        self.assertTrue(self.ok("check-scope", "--id", self.work, "--epoch", self.epoch)["valid"])
        self.assertEqual(self.show(self.work)["evidence"]["missing"], [])
        warned = self.export("--out", "handoff.jsonl")
        self.assertIn("warning", warned)

    def test_c82_checkpoint_round_trip_and_identical_rows_skipped(self):
        out = self.export("--out", "../handoff.jsonl")
        records = self.lines(out["path"])
        self.assertEqual(records[0]["record_type"], "rivet-export")
        tables = [r["table"] for r in records[1:]]
        self.assertIn("baselines", tables)
        self.assertEqual(tables[-1], "events")
        other = self.make_repo("recipient")
        self.ok("init", cwd=other)
        imported = self.ok("import", "--in", out["path"], cwd=other)
        self.assertEqual(imported["imported"], out["exported"])
        self.assertIn("doctor", imported["next"])
        view = self.show(self.work, cwd=other)
        self.assertEqual((view["state"], view["version"]), ("running", 4))
        again = self.ok("import", "--in", out["path"], cwd=other)
        self.assertEqual((again["imported"], again["skipped"]), (0, out["exported"]))
        events = [r for r in records if r.get("table") == "events"]
        reexport = self.lines(self.ok("export", "--out", "../again.jsonl", cwd=other)["path"])
        self.assertEqual([r["row"]["id"] for r in reexport if r.get("table") == "events"],
                         [r["row"]["id"] for r in events], "event order survives handoff")

    def test_worktree_registration_is_exported(self):
        writer = os.path.join(self.base, "writer")
        other = self.start(objective="isolated")
        self.ok("create-worktree", "--id", other["id"], "--epoch", other["epoch"], "--path", writer)
        records = self.lines(self.export()["path"])
        self.assertEqual([r["row"]["path"] for r in records if r.get("table") == "writer_worktrees"], [writer])

    def test_c83_conflicts_and_deletions_abort_the_whole_import(self):
        path = self.export("--out", "../conflict.jsonl")["path"]
        records = self.lines(path)
        for record in records:
            if record.get("table") == "work_items":
                record["row"]["objective"] = "tampered"
        new_event = {"record_type": "row", "table": "events",
                     "row": {"id": "evt-new", "work_id": None, "kind": "note", "detail": "{}", "created_at": "2026-01-01T00:00:00Z"}}
        with open(path, "w") as handle:
            for record in [records[0], new_event] + records[1:]:
                handle.write(json.dumps(record) + "\n")
        before = len(self.ok("status", "--full")["records"]["events"])
        self.err("import", "--in", path, code="import_conflict")
        self.assertEqual(len(self.ok("status", "--full")["records"]["events"]), before)
        with open(path, "w") as handle:
            handle.write(json.dumps(records[0]) + "\n")
            handle.write(json.dumps({"record_type": "delete", "table": "work_items", "row": {"id": self.work}}) + "\n")
        self.err("import", "--in", path, code="import_invalid")
        self.assertEqual(self.show(self.work)["objective"], "bounded work")

    def test_c84_event_only_import_cannot_change_canonical_state(self):
        out = self.export("--since", "2000-01-01T00:00:00Z", "--out", "../events.jsonl")
        self.assertEqual(out["mode"], "events")
        records = self.lines(out["path"])
        self.assertTrue(all(r.get("table") == "events" for r in records[1:]))
        other = self.make_repo("audit")
        self.ok("init", cwd=other)
        self.ok("import", "--in", out["path"], cwd=other)
        self.assertEqual(self.ok("status", cwd=other)["by_state"], {})
        item_row = {"record_type": "row", "table": "work_items",
                    "row": self.ok("status", "--full")["records"]["work_items"][0]}
        with open(out["path"], "a") as handle:
            handle.write(json.dumps(item_row) + "\n")
        third = self.make_repo("third")
        self.ok("init", cwd=third)
        self.err("import", "--in", out["path"], cwd=third, code="import_invalid")
        self.assertEqual(self.ok("status", cwd=third)["by_state"], {})

    def test_c86_corrupt_imported_baseline_is_never_clean(self):
        path = self.export("--out", "../corrupt.jsonl")["path"]
        records = self.lines(path)
        for record in records:
            if record.get("table") == "baselines":
                record["row"]["digest"] = "0" * 64
        with open(path, "w") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        other = self.make_repo("recipient")
        self.ok("init", cwd=other)
        self.ok("import", "--in", path, cwd=other)
        scope = self.show(self.work, cwd=other)["scope"]
        self.assertEqual(scope["status"], "unavailable")
        self.assertIn("corrupt", scope["detail"])
        self.err("check-scope", "--id", self.work, "--epoch", self.epoch, cwd=other, code="scope_unavailable")

    def test_import_paths_resolve_like_export(self):
        self.export("--out", "../shared.jsonl")
        other = self.make_repo("recipient")
        self.ok("init", cwd=other)
        os.rename(os.path.join(self.base, "shared.jsonl"), os.path.join(other, "shared.jsonl"))
        self.ok("import", "--in", "shared.jsonl", cwd=os.path.join(other, "src"))

    def test_c88_errors_are_json_never_tracebacks(self):
        self.ok("propose", "--objective", "x", "--id", "dup")
        error = self.err("propose", "--objective", "y", "--id", "dup", code="duplicate_id")
        self.assertNotIn("Traceback", error["error"])
        self.err("propose", "--objective", "z", "--depends-on", "work-missing", code="not_found")
        self.assertNotIn("z", [i["objective"] for i in self.ok("status")["active"]])
        bad = self.write("../bad.jsonl", "[1]\n")
        self.err("import", "--in", bad, code="import_invalid")
        self.err("import", "--in", "../missing.jsonl", code="import_invalid")


class InspectionTests(RivetTestCase):
    def setUp(self):
        super().setUp()
        self.ok("init")

    def test_c87_compact_status_and_actionable_show(self):
        item = self.start()
        self.ok("transition", "--id", item["id"], "--to", "verification", "--expected-version", 4,
                "--epoch", item["epoch"])
        status = self.ok("status")
        for key in ("evidence", "config", "enforcement", "records"):
            self.assertNotIn(key, status)
        self.assertIn("status --full", status["detail"])
        self.assertEqual(status["blockers"], [{"id": item["id"], "code": "evidence_missing", "detail": "unit"}])
        full = self.ok("status", "--full")
        self.assertIn("evidence", full["records"])
        self.assertIn("enforcement", full)
        view = self.show(item["id"])
        self.assertEqual(view["lease"]["owner"], "coordinator")
        self.assertGreater(view["lease"]["ttl_seconds_remaining"], 3500)
        self.assertEqual(view["scope"]["status"], "clean")
        self.assertIn("running", view["next_transitions"])
        self.assertEqual(view["evidence"]["missing"], ["unit"])
        unclaimed = self.admitted()
        quiet = self.show(unclaimed)
        self.assertEqual(quiet["scope"]["status"], "unknown")
        self.assertIsNone(quiet["lease"])

    def test_c89_redirection_pauses_live_work_and_leaves_it_alone(self):
        old = self.start(objective="old work")
        paused = self.ok("pause", "--id", old["id"], "--epoch", old["epoch"], "--expected-version", 4,
                         "--reason", "human asked for something else")
        new = self.start(objective="new request")
        self.verify_pass(new["id"], new["epoch"])
        self.assertTrue(self.ok("finish", "--id", new["id"], "--epoch", new["epoch"])["completed"])
        view = self.show(old["id"])
        self.assertEqual((view["state"], view["version"]), ("paused", paused["version"]))
        self.assertEqual(view["lease"]["owner"], "coordinator")
        events = [e for e in self.ok("status", "--full")["records"]["events"] if e["work_id"] == old["id"]]
        self.assertEqual(events[-1]["kind"], "paused")


if __name__ == "__main__":
    unittest.main()
