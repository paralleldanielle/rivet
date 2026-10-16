"""External result conformance: C68–C75."""

import json
import os
import unittest

from support import RivetTestCase


class ExternalTests(RivetTestCase):
    def setUp(self):
        super().setUp()
        self.ok("init")
        self.remote = self.bare("lib.git")
        self.lib = self.make_repo("lib", {"lib.py": "v = 1\n"})
        self.git("remote", "add", "origin", self.remote, cwd=self.lib)
        self.git("push", "-q", "origin", "main", cwd=self.lib)

    def bare(self, name):
        path = os.path.join(self.base, name)
        self.git("init", "-q", "--bare", path, cwd=self.base)
        return path

    def publish(self, content, cwd=None, ref="main"):
        cwd = cwd or self.lib
        self.write("lib.py", content, cwd=cwd)
        sha = self.commit_all("publish", cwd=cwd)
        self.git("push", "-q", "origin", f"HEAD:refs/heads/{ref}", cwd=cwd)
        return sha

    def declare(self, name="lib", remote=None, gitlink=None, ref="refs/heads/main"):
        desc = {"name": name, "remote": remote or self.remote, "ref": ref}
        if gitlink:
            desc["gitlink"] = gitlink
        return json.dumps(desc)

    def begin(self, *declarations, paths=("src",)):
        item = self.ok("start", "--objective", "publish library", "--allowed-paths", *paths,
                       *[arg for d in declarations for arg in ("--external-result", d)])
        return item["id"], item["epoch"]

    def verify(self, work, epoch, sha, name="lib"):
        return self.ok("verify-external", "--id", work, "--epoch", epoch, "--name", name, "--commit-sha", sha)

    def test_c68_declaration_validation(self):
        good = json.loads(self.declare())
        cases = [
            [good, good],
            [dict(good, extra=1)],
            [dict(good, ref="main")],
            [dict(good, ref="refs/heads/a..b")],
            [dict(good, remote="https://user:secret@example.invalid/lib.git")],
            [dict(good, remote="https://token@example.invalid/lib.git")],
            [dict(good, remote="ext::sh -c touch% /tmp/pwned")],
            [dict(good, remote="fd::3")],
            [dict(good, remote="-oProxyCommand=evil")],
            [dict(good, remote="https://example.invalid/lib.git?x=1")],
            [dict(good, remote="ftp://example.invalid/lib.git")],
            [dict(good, name="bad name")],
            [dict(good, gitlink="../outside")],
            [{"remote": self.remote, "ref": "refs/heads/main"}],
        ]
        for declarations in cases:
            with self.subTest(declarations=declarations):
                args = ["propose", "--objective", "x"]
                for d in declarations:
                    args += ["--external-result", json.dumps(d)]
                error = self.err(*args)
                self.assertIn(error["code"], ("external_invalid", "invalid_path"))
        ok = self.ok("propose", "--objective", "x", "--external-result",
                     json.dumps(dict(good, remote="ssh://git@example.invalid/lib.git")),
                     "--external-result", json.dumps(dict(good, name="scp", remote="git@example.invalid:org/lib.git")))
        self.assertEqual([d["name"] for d in self.show(ok["id"])["external_results"]], ["lib", "scp"])

    def test_c69_unknown_name_and_malformed_sha(self):
        work, epoch = self.begin(self.declare())
        sha = self.publish("v = 2\n")
        self.err("verify-external", "--id", work, "--epoch", epoch, "--name", "nope", "--commit-sha", sha,
                 code="external_invalid")
        for bad in (sha[:12], "not-a-sha", sha + "0"):
            self.err("verify-external", "--id", work, "--epoch", epoch, "--name", "lib", "--commit-sha", bad,
                     code="invalid_argument")
        upper = self.verify(work, epoch, sha.upper())
        self.assertEqual(upper["commit_sha"], sha)

    def test_c70_published_ancestor_passes_after_the_ref_advances(self):
        work, epoch = self.begin(self.declare())
        self.assertEqual(self.show(work)["required_evidence"], ["external:lib"])
        sha = self.publish("v = 2\n")
        out = self.verify(work, epoch, sha)
        self.assertEqual((out["outcome"], out["source"]), ("pass", "engine_measured"))
        self.publish("v = 3\n")
        finish = self.ok("finish", "--id", work, "--epoch", epoch)
        self.assertTrue(finish["completed"], finish)
        self.assertEqual(finish["evidence"]["sources"], {"external:lib": "engine_measured"})

    def test_c71_unrelated_commit_fails_and_every_declaration_is_required(self):
        mirror = self.bare("mirror.git")
        self.git("push", "-q", mirror, "main", cwd=self.lib)
        work, epoch = self.begin(self.declare(), self.declare("mirror", mirror))
        stranger = self.make_repo("stranger", {"x": "y\n"})
        foreign = self.git("rev-parse", "HEAD", cwd=stranger)
        failed = self.verify(work, epoch, foreign)
        self.assertEqual(failed["outcome"], "fail")
        self.assertIn("not found", failed["detail"])
        sha = self.publish("v = 2\n")
        self.verify(work, epoch, sha)
        finish = self.ok("finish", "--id", work, "--epoch", epoch)
        self.assertFalse(finish["completed"])
        self.assertEqual(finish["evidence"]["missing"], ["external:mirror"])
        self.git("push", "-q", mirror, "main", cwd=self.lib)
        self.verify(work, epoch, sha, name="mirror")
        self.assertTrue(self.ok("finish", "--id", work, "--epoch", epoch)["completed"])

    def test_c72_c73_parent_gitlink_must_match_and_submodule_production_is_accepted(self):
        self.git("submodule", "add", "-q", self.remote, "vendor/lib")
        self.commit_all("add submodule")
        work, epoch = self.begin(self.declare(gitlink="vendor/lib"), paths=("vendor/lib",))
        sub = os.path.join(self.repo, "vendor", "lib")
        self.git("checkout", "-q", "-b", "work", cwd=sub)
        sha = self.publish("v = 9\n", cwd=sub)
        failed = self.verify(work, epoch, sha)
        self.assertEqual(failed["outcome"], "fail")
        self.assertIn("gitlink", failed["detail"])
        self.git("add", "vendor/lib")
        passed = self.verify(work, epoch, sha)
        self.assertEqual(passed["outcome"], "pass", passed["detail"])
        self.assertTrue(self.ok("finish", "--id", work, "--epoch", epoch)["completed"])

    def test_c74_remote_rewrite_after_a_pass_blocks_completion(self):
        work, epoch = self.begin(self.declare())
        sha = self.publish("v = 2\n")
        self.verify(work, epoch, sha)
        rogue = self.make_repo("rogue", {"other.txt": "unrelated\n"})
        self.git("push", "-q", "--force", self.remote, "main", cwd=rogue)
        self.ok("transition", "--id", work, "--to", "verification", "--expected-version", 4, "--epoch", epoch)
        self.err("complete", "--id", work, "--epoch", epoch, "--expected-version", 2, code="version_conflict")
        self.assertEqual(self.show(work)["evidence"]["missing"], [], "a stale complete does no remote recheck")
        finish = self.ok("finish", "--id", work, "--epoch", epoch)
        self.assertFalse(finish["completed"])
        codes = [b["code"] for b in finish["blockers"]]
        self.assertEqual(codes[0], "external_result_failed")
        self.assertRegex(finish["blockers"][0]["detail"], "not found|not reachable")
        view = self.show(work)
        self.assertEqual(view["evidence"]["missing"], ["external:lib"])
        self.assertIn("external_result_failed", [b["code"] for b in view["blockers"]])
        self.git("fetch", "-q", "origin", cwd=self.lib)
        self.git("reset", "-q", "--hard", "origin/main", cwd=self.lib)
        new_sha = self.publish("v = 5\n")
        self.verify(work, epoch, new_sha)
        self.assertTrue(self.ok("finish", "--id", work, "--epoch", epoch)["completed"])

    def test_c75_external_checks_do_not_touch_checkouts_or_push(self):
        work, epoch = self.begin(self.declare())
        sha = self.publish("v = 2\n")
        refs_before = self.git("for-each-ref", cwd=self.repo)
        status_before = self.git("status", "--porcelain", "--untracked-files=all", cwd=self.repo)
        remote_before = self.git("for-each-ref", cwd=self.remote)
        self.verify(work, epoch, sha)
        self.assertEqual(self.git("for-each-ref", cwd=self.repo), refs_before)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), status_before)
        self.assertEqual(self.git("for-each-ref", cwd=self.remote), remote_before)

    def test_relative_local_remote_resolves_against_controller(self):
        relative = os.path.relpath(self.remote, self.repo)
        work, epoch = self.begin(self.declare(remote=relative))
        sha = self.publish("v = 2\n")
        out = self.ok("verify-external", "--id", work, "--epoch", epoch, "--name", "lib", "--commit-sha", sha,
                      cwd=os.path.join(self.repo, "src"))
        self.assertEqual(out["outcome"], "pass", out["detail"])

    def test_unreachable_remote_is_a_measured_failure(self):
        work, epoch = self.begin(self.declare(remote=os.path.join(self.base, "missing.git")))
        out = self.verify(work, epoch, "a" * 40)
        self.assertEqual(out["outcome"], "fail")
        self.assertIn("could not fetch", out["detail"])


if __name__ == "__main__":
    unittest.main()
