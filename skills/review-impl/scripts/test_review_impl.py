import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import review_impl


class ImplementationReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Review fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "greet.py").write_text('def greet():\n    return "Hello world"\n')
        self.git("add", "greet.py")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        (self.repo / "greet.py").write_text('def greet(name="world"):\n    return f"Hello {name}"\n')
        self.git("add", "greet.py")
        self.git("commit", "-qm", "named greeting")
        self.head = self.git("rev-parse", "HEAD").strip()
        self.plan = self.root / "approved.md"
        self.plan.write_text("# Approved plan\nSupport an optional greeting name.\n")
        self.calls = []

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True)

    def reviewer(self, reviewer, repo, plan, scope, context, session):
        self.calls.append((reviewer, plan, json.loads(scope.read_text()), context, session))
        return "LGTM", "Verdict: LGTM", "review-session"

    def run_review(self, author="codex", reviewer=None, **kwargs):
        return review_impl.run(self.repo, self.plan, self.base, self.head, author,
                               state_root=self.root / "state", run_review=reviewer or self.reviewer, **kwargs)

    def test_exact_scope_snapshot_and_opposite_reviewers(self):
        for author, expected in (("codex", "claude"), ("claude", "codex")):
            result = self.run_review(author)
            self.assertEqual(result["reviewer"], expected)
            self.assertEqual(result["head"], self.head)
            self.assertEqual(result["merge_base"], self.base)
            self.assertEqual(self.calls[-1][1].read_bytes(), self.plan.read_bytes())
            self.assertIn('+def greet(name="world"):', Path(result["diff"]).read_text())

    def test_untracked_work_is_not_silently_omitted(self):
        (self.repo / "extra.py").write_text("extra = True\n")
        with self.assertRaisesRegex(RuntimeError, "clean checkout"):
            self.run_review()
        self.assertEqual(self.calls, [])

    def test_checkout_must_match_supplied_head(self):
        self.git("checkout", "--detach", "-q", self.base)
        with self.assertRaisesRegex(RuntimeError, "HEAD differs"):
            self.run_review()

    def test_plan_change_during_review_invalidates_lgtm(self):
        def changing(*args):
            self.plan.write_text("A different plan")
            return self.reviewer(*args)
        with self.assertRaisesRegex(RuntimeError, "plan changed"):
            self.run_review(reviewer=changing)
        record = next((self.root / "state").glob("*/result.json"))
        self.assertEqual(json.loads(record.read_text())["status"], "failed")

    def test_checkout_change_during_review_invalidates_lgtm(self):
        def changing(*args):
            (self.repo / "greet.py").write_text("changed = True")
            return self.reviewer(*args)
        with self.assertRaisesRegex(RuntimeError, "clean checkout"):
            self.run_review(reviewer=changing)

    def test_changes_needed_is_completed_advisory_review(self):
        result = self.run_review(reviewer=lambda *args: ("CHANGES NEEDED", "Verdict: CHANGES NEEDED", "session"))
        self.assertEqual(result["status"], "reviewed")
        self.assertEqual(result["verdict"], "CHANGES NEEDED")

    def test_failed_reviewer_is_not_skipped(self):
        def failure(*args):
            raise RuntimeError("reviewer unavailable")
        with self.assertRaisesRegex(RuntimeError, "reviewer unavailable"):
            self.run_review(reviewer=failure)
        record = next((self.root / "state").glob("*/result.json"))
        self.assertNotIn("verdict", json.loads(record.read_text()))

    def test_resume_must_return_same_session(self):
        with self.assertRaisesRegex(RuntimeError, "different session"):
            self.run_review(session="other-session")
        result = self.run_review(session="review-session")
        self.assertEqual(result["session_id"], "review-session")

    def test_live_claude_verdict_format_contract(self):
        for text, valid in (("**Verdict: LGTM**", False), ("Verdict: LGTM", True)):
            output = subprocess.CompletedProcess([], 0, json.dumps({"result": text, "session_id": "review-session"}), "")
            with patch.object(review_impl.plan_workflow, "reviewer_command", return_value=["claude"]), patch.object(review_impl.plan_workflow.subprocess, "run", return_value=output):
                if valid:
                    result = review_impl.plan_workflow.review(self.repo, self.plan, None, None)
                    self.assertEqual(result[0], "LGTM")
                else:
                    with self.assertRaisesRegex(RuntimeError, "unambiguous verdict"):
                        review_impl.plan_workflow.review(self.repo, self.plan, None, None)


if __name__ == "__main__":
    unittest.main()
