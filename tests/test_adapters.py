"""Host adapter packaging: manifests, skills, generated role files, launcher (C67 static checks)."""

import json
import os
import re
import subprocess
import sys
import unittest

from support import LAUNCHER, PLUGIN, ROOT, RivetTestCase

SKILLS = ("rivet-config", "rivet-concept", "rivet-work", "rivet-status", "rivet-doctor")
ADAPTERS = ("claude-code", "codex", "copilot", "cursor")


def load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return json.load(handle)


def frontmatter(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert match, f"{path} has no frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(": ")
        fields[key] = json.loads(value) if value.startswith('"') else value
    return fields, match.group(2)


class ManifestTests(unittest.TestCase):
    def test_manifests_and_marketplaces_agree(self):
        sys.path.insert(0, os.path.join(PLUGIN, "lib"))
        from rivet import VERSION
        sys.path.remove(os.path.join(PLUGIN, "lib"))
        portable = load("plugins/rivet/plugin.json")
        self.assertEqual(portable["$schema"], "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json")
        claude = load("plugins/rivet/.claude-plugin/plugin.json")
        cursor = load("plugins/rivet/.cursor-plugin/plugin.json")
        copilot = load("plugins/rivet/.plugin/plugin.json")
        self.assertEqual(copilot["agents"], "./com.github.copilot/agents/", "Copilot without Agent Plugins support")
        self.assertTrue(os.path.isdir(os.path.join(PLUGIN, copilot["agents"])))
        for manifest in (portable, claude, cursor, copilot):
            self.assertEqual(manifest["name"], "rivet")
            self.assertEqual(manifest["version"], VERSION)
            self.assertEqual(manifest["license"], "Apache-2.0")
            self.assertTrue(manifest["description"])
        self.assertTrue(os.path.isdir(os.path.join(PLUGIN, cursor["agents"])))
        self.assertTrue(os.path.isdir(os.path.join(PLUGIN, cursor["skills"])))
        for component in ("agents", "skills", "commands", "hooks", "mcpServers"):
            self.assertNotIn(component, portable, "Agent Plugins 1.0 manifests carry no component paths")

        claude_market = load(".claude-plugin/marketplace.json")
        codex_market = load(".agents/plugins/marketplace.json")
        cursor_market = load(".cursor-plugin/marketplace.json")
        sources = [claude_market["plugins"][0]["source"], codex_market["plugins"][0]["source"]["path"],
                   cursor_market["plugins"][0]["source"]]
        for source in sources:
            self.assertEqual(os.path.realpath(os.path.join(ROOT, source)), os.path.realpath(PLUGIN))
        self.assertEqual(claude_market["plugins"][0]["version"], VERSION)
        self.assertEqual(cursor_market["plugins"][0]["version"], VERSION)
        entry = codex_market["plugins"][0]
        self.assertIn("installation", entry["policy"])
        self.assertTrue(entry["category"])


class SkillTests(unittest.TestCase):
    def test_skills_are_portable_and_lean(self):
        found = sorted(os.listdir(os.path.join(PLUGIN, "skills")))
        self.assertEqual(found, sorted(SKILLS))
        for name in SKILLS:
            with self.subTest(skill=name):
                fields, body = frontmatter(os.path.join(PLUGIN, "skills", name, "SKILL.md"))
                self.assertEqual(set(fields), {"name", "description"})
                self.assertEqual(fields["name"], name)
                self.assertLessEqual(len(fields["description"]), 400)
                self.assertIn("Use ", fields["description"])
                self.assertLess(len(body.split()), 1300)
                self.assertIn("python3 <plugin>/bin/rivet", body, "every workflow says how to find the engine")
                self.assertNotRegex(body, r"(?i)read (the )?readme|engine source")
        for name in ("rivet-concept", "rivet-status"):
            fields, _ = frontmatter(os.path.join(PLUGIN, "skills", name, "SKILL.md"))
            self.assertIn("Not for", fields["description"])

    def test_safety_rules_repeated_at_each_entry_point(self):
        def text(*parts):
            with open(os.path.join(PLUGIN, *parts), encoding="utf-8") as handle:
                return handle.read()
        for parts in (("skills", "rivet-work", "SKILL.md"), ("skills", "rivet-concept", "SKILL.md"),
                      ("agents", "rivet-coordinator.md")):
            self.assertIn("latest human instruction", text(*parts).lower(), parts)
        for parts in (("skills", "rivet-config", "SKILL.md"), ("agents", "rivet-coordinator.md")):
            body = text(*parts)
            self.assertRegex(body, r"(?i)empty.*(means|list).*any model")
            self.assertRegex(body.lower(), r"spending limits belong to the host")

    def test_work_skill_references_exist_and_cover_every_host(self):
        refs = os.path.join(PLUGIN, "skills", "rivet-work", "references")
        self.assertEqual(sorted(os.listdir(refs)), ["coordinator.md", "hosts.md", "worker.md"])
        with open(os.path.join(refs, "hosts.md"), encoding="utf-8") as handle:
            hosts = handle.read()
        for adapter in ADAPTERS:
            self.assertIn(f"`--adapter {adapter}`", hosts)


class RoleTests(unittest.TestCase):
    def test_generated_files_are_in_sync(self):
        result = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_adapters.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_workers_cannot_delegate_or_be_invoked_directly(self):
        claude, body = frontmatter(os.path.join(PLUGIN, "agents", "rivet-worker.md"))
        tools = [t.strip() for t in claude["tools"].split(",")]
        self.assertFalse(any(t.startswith(("Agent", "Task")) for t in tools))
        copilot, _ = frontmatter(os.path.join(PLUGIN, "com.github.copilot", "agents", "rivet-worker.agent.md"))
        self.assertEqual(copilot["user-invocable"], "false")
        self.assertNotIn("agent", copilot["tools"])
        coordinator, _ = frontmatter(os.path.join(PLUGIN, "com.github.copilot", "agents", "rivet-coordinator.agent.md"))
        self.assertEqual(coordinator["agents"], "['rivet-worker']")
        self.assertEqual(coordinator["disable-model-invocation"], "true")
        for prohibition in ("Run the Rivet engine", "Launch other agents", "Commit, push"):
            self.assertIn(prohibition, body)
        self.assertNotIn("bin/rivet", body, "workers get no launcher path")

    def test_coordinator_protocol_is_sequential(self):
        _, body = frontmatter(os.path.join(PLUGIN, "agents", "rivet-coordinator.md"))
        self.assertIn("strictly one at a time", body)
        self.assertIn("If it fails, do not launch", body)
        self.assertIn("Always settle", body)
        self.assertIn('subagent_type: "rivet:rivet-worker"', body)
        _, cursor = frontmatter(os.path.join(PLUGIN, "cursor", "agents", "rivet-coordinator.md"))
        self.assertIn('subagent_type: "rivet-worker"', cursor)

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11")
    def test_codex_agent_is_valid_toml(self):
        import tomllib
        with open(os.path.join(PLUGIN, "codex", "agents", "rivet_worker.toml"), "rb") as handle:
            data = tomllib.load(handle)
        self.assertEqual(set(data), {"name", "description", "developer_instructions"})
        self.assertEqual(data["name"], "rivet_worker")


class LauncherTests(RivetTestCase):
    def test_launcher_works_through_a_symlink_from_another_repository(self):
        link_dir = os.path.join(self.base, "bin")
        os.makedirs(link_dir)
        os.symlink(LAUNCHER, os.path.join(link_dir, "rivet"))
        result = subprocess.run([os.path.join(link_dir, "rivet"), "init"], cwd=self.repo, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["root"], self.repo)
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".rivet", "runtime", "state.db")))
        self.assertFalse(os.path.exists(os.path.join(PLUGIN, ".rivet")), "state belongs to the target repository")

    def test_launcher_never_writes_bytecode_into_the_plugin(self):
        env = {k: v for k, v in self.env.items() if k != "PYTHONDONTWRITEBYTECODE"}
        subprocess.run([sys.executable, LAUNCHER, "version"], cwd=self.repo, env=env, capture_output=True, check=True)
        caches = [d for d, _, _ in os.walk(PLUGIN) if os.path.basename(d) == "__pycache__"]
        self.assertEqual(caches, [])

    def test_doctor_and_errors_point_at_the_absolute_launcher(self):
        self.ok("init")
        error = self.err("show", "--id", "nope")
        self.assertEqual(error["code"], "not_found")
        other = self.make_repo("uninitialized")
        message = self.err("status", cwd=other, code="not_initialized")["error"]
        self.assertIn(os.path.realpath(LAUNCHER), message)


if __name__ == "__main__":
    unittest.main()
