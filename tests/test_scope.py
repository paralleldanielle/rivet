"""Scope conformance: C33–C44."""

import os
import unittest

from support import RivetTestCase


class ScopeTests(RivetTestCase):
    def setUp(self):
        super().setUp()
        self.ok("init")

    def begin(self, paths=("src",), evidence=("unit",)):
        item = self.start(paths=paths, evidence=evidence)
        return item["id"], item["epoch"]

    def scope(self, work, epoch):
        return self.ok("check-scope", "--id", work, "--epoch", epoch)

    def test_c33_c34_c35_preexisting_edits_are_visible_but_not_attributed(self):
        self.write("docs/notes.md", "staged edit\n")
        self.git("add", "docs/notes.md")
        self.write("docs/notes.md", "unstaged on top\n")
        self.write("README.md", "unstaged only\n")
        self.write("notes.txt", "untracked note\n")
        work, epoch = self.begin()
        scope = self.scope(work, epoch)
        self.assertTrue(scope["valid"])
        self.assertEqual(scope["post_admission_paths"], [])
        for path in ("docs/notes.md", "README.md", "notes.txt"):
            self.assertIn(path, scope["preexisting_paths"])

        self.git("add", "docs/notes.md")
        scope = self.scope(work, epoch)
        self.assertFalse(scope["valid"])
        self.assertEqual(scope["violations"], ["docs/notes.md"])

        self.git("reset", "-q", "docs/notes.md")
        self.git("add", "docs/notes.md")
        self.write("docs/notes.md", "staged edit\n")
        self.git("add", "docs/notes.md")
        self.write("docs/notes.md", "unstaged on top\n")
        self.assertTrue(self.scope(work, epoch)["valid"], "restoring index and worktree removes the net violation")

        self.write("notes.txt", "edited untracked note\n")
        self.assertEqual(self.scope(work, epoch)["violations"], ["notes.txt"])
        self.write("notes.txt", "untracked note\n")
        self.assertTrue(self.scope(work, epoch)["valid"])

    def test_c36_prefixes_are_literal_and_renames_report_both_sides(self):
        self.write("src2/b.py", "x\n")
        self.write("old.txt", "move me\n")
        self.commit_all("more files")
        work, epoch = self.begin(paths=("src/",))
        self.write("src/a.py", "a\n")
        self.write("src2/b.py", "changed\n")
        self.git("mv", "old.txt", "src/new.txt")
        scope = self.scope(work, epoch)
        self.assertEqual(scope["violations"], ["old.txt", "src2/b.py"])
        self.assertEqual(scope["post_admission_paths"], ["old.txt", "src/a.py", "src/new.txt", "src2/b.py"])

    def test_c37_committing_an_out_of_scope_edit_does_not_hide_it(self):
        work, epoch = self.begin()
        self.write("docs/notes.md", "committed change\n")
        self.git("commit", "-q", "-am", "sneak")
        self.assertEqual(self.scope(work, epoch)["violations"], ["docs/notes.md"])

    def test_c38_deletions_mode_and_symlink_target_changes(self):
        os.symlink("README.md", os.path.join(self.repo, "link"))
        self.commit_all("link")
        work, epoch = self.begin()
        os.remove(os.path.join(self.repo, "docs/notes.md"))
        os.chmod(os.path.join(self.repo, "README.md"), 0o755)
        os.remove(os.path.join(self.repo, "link"))
        os.symlink("docs/notes.md", os.path.join(self.repo, "link"))
        self.assertEqual(self.scope(work, epoch)["violations"], ["README.md", "docs/notes.md", "link"])

    def test_c39_new_context_draft_is_exempt_but_existing_untracked_note_is_not(self):
        self.write(".rivet/context/existing.md", "present at admission\n")
        work, epoch = self.begin()
        self.write(".rivet/context/new-idea.md", "a captured idea\n")
        scope = self.scope(work, epoch)
        self.assertTrue(scope["valid"])
        self.assertEqual(scope["draft_paths"], [".rivet/context/new-idea.md"])
        self.assertEqual(scope["draft_allowance"], ".rivet/context")
        self.write(".rivet/context/existing.md", "edited\n")
        self.assertEqual(self.scope(work, epoch)["violations"], [".rivet/context/existing.md"])

    def test_c39_custom_context_dir_and_committed_doc(self):
        self.ok("config-init", "--profile", "restricted", "--context-dir", "design", "--replace")
        self.write("design/committed.md", "doc\n")
        self.commit_all("doc")
        work, epoch = self.begin()
        self.write("design/draft.md", "draft\n")
        self.assertTrue(self.scope(work, epoch)["valid"])
        self.write("design/committed.md", "changed\n")
        self.assertEqual(self.scope(work, epoch)["violations"], ["design/committed.md"])

    def test_c40_editing_admitted_concept_blocks_completion_inside_allowed_paths(self):
        self.write(".rivet/context/design.md", "# Design\n")
        item = self.start(paths=("src", ".rivet/context"), concept_ref=".rivet/context/design.md")
        work, epoch = item["id"], item["epoch"]
        self.verify_pass(work, epoch)
        self.write(".rivet/context/design.md", "# Design, edited to match\n")
        scope = self.scope(work, epoch)
        self.assertFalse(scope["valid"])
        self.assertIn(".rivet/context/design.md", scope["concept_drift"])
        finish = self.ok("finish", "--id", work, "--epoch", epoch)
        self.assertFalse(finish["completed"])
        self.assertEqual(finish["blockers"][0]["code"], "concept_drift")
        self.ok("transition", "--id", work, "--to", "verification", "--expected-version", 4, "--epoch", epoch)
        error = self.err("complete", "--id", work, "--epoch", epoch, "--expected-version", 5, code="concept_drift")
        self.assertEqual(error["concept_ref"], ".rivet/context/design.md")

    def test_c41_corrupt_baseline_and_broken_ancestry_are_unavailable_never_clean(self):
        work, epoch = self.begin()
        conn = self.db()
        conn.execute("UPDATE baselines SET digest = 'bogus' WHERE work_id = ?", (work,))
        conn.commit()
        error = self.err("check-scope", "--id", work, "--epoch", epoch, code="scope_unavailable")
        self.assertIn("corrupt", error["error"])
        view = self.show(work)["scope"]
        self.assertEqual(view["status"], "unavailable")
        self.assertIsNone(view["valid"])
        self.assertIn("corrupt", view["detail"])
        finish = self.err("finish", "--id", work, "--epoch", epoch, code="scope_unavailable")
        self.assertTrue(finish["error"])

    def test_c41_history_rewrite_makes_scope_unavailable(self):
        work, epoch = self.begin()
        self.git("checkout", "-q", "--orphan", "rewritten")
        self.git("commit", "-q", "-m", "unrelated history")
        error = self.err("check-scope", "--id", work, "--epoch", epoch, code="scope_unavailable")
        self.assertIn("ancestor", error["error"])
        self.assertEqual(self.show(work)["scope"]["status"], "unavailable")

    def test_c41_changed_writer_path_is_unavailable(self):
        work, epoch = self.begin()
        conn = self.db()
        conn.execute("UPDATE leases SET writer_path = '/nonexistent/elsewhere' WHERE work_id = ?", (work,))
        conn.commit()
        self.err("check-scope", "--id", work, "--epoch", epoch, code="scope_unavailable")

    def test_c42_runtime_and_ignored_files_stay_out_of_measurement(self):
        self.write(".gitignore", "build/\n")
        self.commit_all("ignore build")
        work, epoch = self.begin()
        self.write("build/output.bin", "artifact\n")
        self.ok("status")
        self.ok("export")
        scope = self.scope(work, epoch)
        self.assertTrue(scope["valid"])
        self.assertEqual(scope["post_admission_paths"], [])
        self.assertFalse(any(p.startswith(".rivet/runtime") for p in scope["preexisting_paths"]))

    def test_c43_control_file_changes_outside_scope_are_violations(self):
        work, epoch = self.begin()
        self.git("add", ".rivet/config.json", ".rivet/.gitignore")
        self.git("commit", "-q", "-m", "track rivet config")
        self.assertTrue(self.scope(work, epoch)["valid"], "identical control bytes only changed Git status")
        self.write(".rivet/config.json", self.read(".rivet/config.json").decode().replace('"push"', '"push",\n    "deploy"'))
        scope = self.scope(work, epoch)
        self.assertEqual(scope["violations"], [".rivet/config.json"])
        self.verify_pass(work, epoch)
        self.ok("transition", "--id", work, "--to", "verification", "--expected-version", 4, "--epoch", epoch)
        self.err("complete", "--id", work, "--epoch", epoch, "--expected-version", 5, code="scope_violation")

    def test_c44_parent_scope_does_not_look_inside_submodules(self):
        library = self.make_repo("library", {"lib.py": "x = 1\n"})
        self.git("submodule", "add", "-q", library, "vendor/library")
        self.commit_all("add submodule")
        work, epoch = self.begin()
        self.write("vendor/library/lib.py", "x = 2\n")
        self.write("vendor/library/new.py", "y\n")
        scope = self.scope(work, epoch)
        self.assertTrue(scope["valid"])
        self.assertEqual(scope["post_admission_paths"], [])

    def test_unrestricted_scope_is_labelled(self):
        item = self.ok("start", "--objective", "open ended")
        self.assertEqual(item["contract_mode"], {"scope": "unrestricted", "evidence": "none_required"})
        self.write("anything.txt", "x\n")
        scope = self.scope(item["id"], item["epoch"])
        self.assertTrue(scope["valid"])
        self.assertEqual(scope["scope_mode"], "unrestricted")
        self.assertEqual(self.show(item["id"])["contract_mode"]["scope"], "unrestricted")

    def test_unsafe_allowed_paths_rejected_on_every_route(self):
        for bad in ("../x", "/abs", "a/../b", "."):
            with self.subTest(path=bad):
                self.err("propose", "--objective", "x", "--allowed-paths", bad, code="invalid_path")
                self.err("start", "--objective", "x", "--allowed-paths", bad, code="invalid_path")
        self.err("propose", "--objective", "   ", code="invalid_argument")
        self.err("propose", "--objective", "x", "--max-attempts", 0, code="invalid_argument")
        self.err("propose", "--objective", "x", "--id", "self", "--depends-on", "self", code="invalid_argument")


if __name__ == "__main__":
    unittest.main()
