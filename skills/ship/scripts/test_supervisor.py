import argparse
import importlib.util
from pathlib import Path
import unittest
import shutil
import subprocess
import tempfile
import json

spec = importlib.util.spec_from_file_location("supervisor", Path(__file__).with_name("supervisor.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SupervisorTests(unittest.TestCase):
    def test_cli_serialises_duplicate_launches_without_overwriting_record(self):
        with tempfile.TemporaryDirectory(prefix="ship-supervisor-") as directory:
            root = Path(directory)
            scripts = root / "skill" / "scripts"
            scripts.mkdir(parents=True)
            script = scripts / "supervisor.py"
            shutil.copyfile(module.__file__, script)
            subprocess.run(["git", "init", "-q", str(root / "repo")], check=True)
            command = ["python3", str(script), "--pr", "442", "begin", "--repository",
                       "owner/repo", "--branch", "codex/work", "--head", "a" * 40]
            jobs = [subprocess.Popen(command, cwd=root / "repo", stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True) for _ in range(2)]
            results = [job.communicate() for job in jobs]
            self.assertEqual(sorted(job.returncode for job in jobs), [0, 1])
            winner = next(json.loads(output) for job, (output, _) in zip(jobs, results) if job.returncode == 0)
            read = subprocess.run(["python3", str(script), "--pr", "442", "read"],
                                  cwd=root / "repo", capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(read.stdout), winner)
            self.assertEqual(len(list((root / "skill" / ".state").glob("*/supervisor_442.json"))), 1)

    def begin(self):
        return module.change(None, argparse.Namespace(command="begin", repository="owner/repo",
            pr=442, branch="codex/work", head="a" * 40, plan="/repo/.agents/plans/exact.md"))

    def operation(self, state, command, **kwargs):
        return module.change(state, argparse.Namespace(command=command, handoff=state["handoff"], **kwargs))

    def test_ambiguous_launch_cannot_repeat(self):
        state = self.begin()
        with self.assertRaises(ValueError):
            module.change(state, argparse.Namespace(command="begin"))
        paused = self.operation(state, "pause", reason="launch result unknown")
        with self.assertRaises(ValueError):
            self.operation(paused, "bind", session="session_other")

    def test_session_and_handoff_must_match(self):
        state = self.operation(self.begin(), "bind", session="session_exact")
        with self.assertRaises(ValueError):
            self.operation(state, "ready", session="session_other")
        with self.assertRaises(ValueError):
            module.change(state, argparse.Namespace(command="ready", handoff="stale", session="session_exact"))
        with self.assertRaises(ValueError):
            self.operation(state, "bind", session="session_second")

    def test_completion_records_exact_head_and_plan(self):
        state = self.operation(self.begin(), "bind", session="session_exact")
        with self.assertRaises(ValueError):
            self.operation(state, "complete", head="b" * 40)
        state = self.operation(state, "ready", session="session_exact")
        state = self.operation(state, "complete", head="b" * 40)
        self.assertEqual(state["approved_head"], "b" * 40)
        self.assertEqual(state["plan"], "/repo/.agents/plans/exact.md")
        self.assertEqual(state["session_url"], "https://claude.ai/code/session_exact")

    def test_resume_keeps_owner_and_requires_fresh_readiness(self):
        state = self.operation(self.begin(), "bind", session="session_exact")
        paused = self.operation(state, "pause", reason="no acknowledgement")
        with self.assertRaises(ValueError):
            self.operation(paused, "resume", session="session_other", evidence="reply")
        resumed = self.operation(paused, "resume", session="session_exact", evidence="same-session acknowledgement")
        self.assertEqual(resumed["handoff"], state["handoff"])
        self.assertEqual(resumed["status"], "awaiting-ready")

    def test_replacement_preserves_history_and_rejects_stale_owner(self):
        state = self.begin()
        with self.assertRaises(ValueError):
            self.operation(state, "replace", head="b" * 40, previous_owner_stopped=True, evidence="archived")
        paused = self.operation(state, "pause", reason="ambiguous launch")
        with self.assertRaises(ValueError):
            self.operation(paused, "replace", head="b" * 40, previous_owner_stopped=False, evidence="timeout alone")
        replacement = self.operation(paused, "replace", head="b" * 40, previous_owner_stopped=True, evidence="human confirmed old session archived")
        self.assertNotEqual(replacement["handoff"], state["handoff"])
        self.assertEqual(replacement["history"][0]["handoff"], state["handoff"])
        self.assertIsNone(replacement["session_id"])
        with self.assertRaises(ValueError):
            module.change(replacement, argparse.Namespace(command="bind", handoff=state["handoff"], session="session_old"))


if __name__ == "__main__":
    unittest.main()
