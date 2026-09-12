import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "workflow", Path(__file__).resolve().parent / "plan_workflow.py"
)
workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow)


MARKER = "<!-- plan-slug: native-plan -->\n"


class PlanWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        self.skill = self.root / "installed-skill"
        self.skill.mkdir()
        self.calls = []
        self.results = ["LGTM"]

    def reviewer(self, repo, plan, context, session):
        self.calls.append((repo, plan, context, session))
        verdict = self.results.pop(0)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict, "Verdict: " + verdict, "claude-session"

    def event(self, body="# First plan\nDo the scoped work.", turn="1", session="one", **extra):
        marker = f"<!-- plan-slug: plan-{session} -->"
        if "<!-- plan-slug:" not in body:
            body = marker + "\n" + body
        response = extra.get("last_assistant_message")
        if isinstance(response, str) and "<plan_review_context>" in response and "<!-- plan-slug:" not in response:
            extra["last_assistant_message"] = marker + "\n" + response
        return {
            "hook_event_name": "Stop", "permission_mode": "plan",
            "cwd": str(self.repo), "session_id": session, "turn_id": turn,
            "last_assistant_message": "<proposed_plan>" + body + "</proposed_plan>",
            **extra,
        }

    def run_hook(self, event):
        return workflow.process(event, self.skill, self.reviewer)

    def native_event(self, mode="plan", session="one", turn="1"):
        transcript = self.root / "rollout.jsonl"
        rows = [
            {"type": "session_meta", "payload": {"id": session, "cwd": str(self.repo)}},
            {"type": "turn_context", "payload": {"turn_id": turn, "cwd": str(self.repo), "collaboration_mode": {"mode": mode}}},
            {"type": "event_msg", "payload": {"type": "item_completed", "thread_id": session, "turn_id": turn, "item": {"type": "Plan", "text": MARKER + "# Native plan\nScoped work."}}},
        ]
        transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return self.event(permission_mode="bypassPermissions", last_assistant_message=None, transcript_path=str(transcript))

    def test_native_plan_uses_collaboration_mode_and_completed_item(self):
        self.run_hook(self.native_event())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1].read_text(), MARKER + "# Native plan\nScoped work.\n")

    def test_native_default_mode_ignores_plan_item(self):
        self.assertEqual(self.run_hook(self.native_event(mode="default")), {})
        self.assertEqual(self.calls, [])

    def test_native_revision_within_same_turn_reaches_same_reviewer(self):
        self.results = ["CHANGES NEEDED", "LGTM"]
        event = self.native_event()
        self.run_hook(event)
        with Path(event["transcript_path"]).open("a") as stream:
            stream.write(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "thread_id": "one", "turn_id": "1", "item": {"type": "Plan", "text": MARKER + "# Native plan\nCorrected work."}}}) + "\n")
        self.run_hook(event)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1][3], "claude-session")
        self.assertEqual(self.calls[1][1].read_text(), MARKER + "# Native plan\nCorrected work.\n")
        self.assertEqual(self.run_hook(event), {})

    def test_native_context_after_plan_is_current_output(self):
        self.results = ["CHANGES NEEDED", "LGTM"]
        event = self.native_event()
        self.run_hook(event)
        with Path(event["transcript_path"]).open("a") as stream:
            stream.write(json.dumps({"type": "response_item", "payload": {"role": "assistant", "phase": "final_answer", "internal_chat_message_metadata_passthrough": {"turn_id": "1"}, "content": [{"type": "output_text", "text": MARKER + "<plan_review_context>Author context</plan_review_context>"}]}}) + "\n")
        self.run_hook(event)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1][2].read_text(), "Author context\n")

    def test_native_wrong_session_or_turn_fails_closed(self):
        for overrides in ({"session": "other"}, {"turn": "old"}):
            self.assertFalse(self.run_hook(self.native_event(**overrides))["continue"])
        self.assertEqual(self.calls, [])

    def test_native_clarification_does_not_reuse_old_plan(self):
        event = self.native_event()
        transcript = Path(event["transcript_path"])
        with transcript.open("a") as stream:
            stream.write(json.dumps({"type": "turn_context", "payload": {"turn_id": "2", "cwd": str(self.repo), "collaboration_mode": {"mode": "plan"}}}) + "\n")
        self.assertEqual(self.run_hook({**event, "turn_id": "2"}), {})
        self.assertEqual(self.calls, [])

    def state(self):
        return json.loads(next(self.skill.glob(".state/*/review.json")).read_text())

    def test_export_absolute_path_and_lgtm(self):
        response = self.run_hook(self.event())
        self.assertNotIn("decision", response)
        self.assertIn("Await explicit human", response["systemMessage"])
        self.assertEqual(self.calls[0][1].read_text(), "<!-- plan-slug: plan-one -->\n# First plan\nDo the scoped work.\n")
        self.assertTrue(self.calls[0][1].is_absolute())
        self.assertEqual(self.state()["status"], "reviewed")

    def test_default_mode_does_not_write_or_review(self):
        self.assertEqual(self.run_hook(self.event(permission_mode="default")), {})
        self.assertEqual(self.calls, [])
        self.assertFalse((self.skill / ".state").exists())

    def test_clarification_preserves_existing_plan(self):
        self.run_hook(self.event())
        path = self.calls[0][1]
        before = path.stat().st_mtime_ns
        result = self.run_hook(self.event(turn="2", last_assistant_message="Which option?"))
        self.assertEqual(result, {})
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertEqual(len(self.calls), 1)

    def test_revision_keeps_path_and_reviewer_session(self):
        self.results = ["CHANGES NEEDED", "LGTM"]
        first = self.run_hook(self.event())
        self.assertEqual(first["decision"], "block")
        self.run_hook(self.event("# New title\nRevised work.", turn="2", stop_hook_active=True))
        self.assertEqual(self.calls[0][1], self.calls[1][1])
        self.assertEqual(self.calls[1][3], "claude-session")
        self.assertIn("Revised work.", self.calls[1][1].read_text())
        self.assertEqual(self.state()["passes"], 2)

    def test_replayed_turn_and_unchanged_approval_do_not_review(self):
        self.run_hook(self.event())
        self.assertEqual(self.run_hook(self.event()), {})
        self.run_hook(self.event(turn="2"))
        self.assertEqual(len(self.calls), 1)

    def test_unchanged_rejected_plan_stops(self):
        self.results = ["CHANGES NEEDED"]
        self.run_hook(self.event())
        result = self.run_hook(self.event(turn="2"))
        self.assertFalse(result["continue"])
        self.assertEqual(len(self.calls), 1)

    def test_session_isolation(self):
        self.results = ["LGTM", "LGTM"]
        self.run_hook(self.event())
        self.run_hook(self.event(session="two"))
        self.assertNotEqual(self.calls[0][1], self.calls[1][1])
        self.assertEqual(len(list(self.skill.glob(".state/*/review.json"))), 2)


    def test_same_slug_across_author_sessions_resumes_reviewer(self):
        self.results = ["CHANGES NEEDED", "LGTM"]
        self.run_hook(self.event("<!-- plan-slug: shared-plan -->\n# Plan\nFirst"))
        self.run_hook(self.event("<!-- plan-slug: shared-plan -->\n# Renamed\nRevised", session="two", turn="2"))
        self.assertEqual(self.calls[0][1], self.calls[1][1])
        self.assertEqual(self.calls[1][3], "claude-session")
        self.assertEqual(self.state()["passes"], 2)

    def test_different_slugs_in_one_author_session_have_separate_budgets(self):
        self.results = ["LGTM", "LGTM"]
        self.run_hook(self.event("<!-- plan-slug: first-plan -->\n# First"))
        self.run_hook(self.event("<!-- plan-slug: second-plan -->\n# Second", turn="2"))
        self.assertNotEqual(self.calls[0][1], self.calls[1][1])
        self.assertIsNone(self.calls[1][3])
        self.assertEqual(len(list(self.skill.glob(".state/*/review.json"))), 2)

    def test_missing_unsafe_or_duplicate_slug_pauses(self):
        for marker in ("", "<!-- plan-slug: ../outside -->", "<!-- plan-slug: valid -->\n<!-- plan-slug: duplicate -->"):
            event = self.event(last_assistant_message="<proposed_plan>" + marker + "\n# Plan</proposed_plan>")
            self.assertFalse(self.run_hook(event)["continue"])
        self.assertEqual(self.calls, [])

    def test_repository_isolation(self):
        self.results = ["LGTM", "LGTM"]
        self.run_hook(self.event())
        other = self.root / "other-repo"
        other.mkdir()
        self.run_hook(self.event(cwd=str(other)))
        self.assertEqual(len(list(self.skill.glob(".state/*/review.json"))), 2)

    def test_three_passes_then_pause_and_retain_plan(self):
        self.results = ["CHANGES NEEDED"] * 3
        for turn in range(1, 4):
            result = self.run_hook(self.event(f"# Plan\nRevision {turn}", turn=str(turn)))
        self.assertFalse(result["continue"])
        self.assertEqual(self.state()["status"], "paused")
        self.assertTrue(self.calls[0][1].exists())
        self.run_hook(self.event("# Plan\nFourth revision", turn="4"))
        self.assertEqual(len(self.calls), 3)

    def test_context_request_reuses_plan_and_session(self):
        self.results = ["CHANGES NEEDED", "LGTM"]
        self.run_hook(self.event())
        original = self.calls[0][1].read_text()
        self.run_hook(self.event(
            turn="2", last_assistant_message="<plan_review_context>User chose A.</plan_review_context>"
        ))
        self.assertEqual(self.calls[1][1].read_text(), original)
        self.assertEqual(self.calls[1][2].read_text(), "User chose A.\n")
        self.assertEqual(self.calls[1][3], "claude-session")

    def test_unrequested_context_is_ignored(self):
        result = self.run_hook(self.event(last_assistant_message="<plan_review_context>foo</plan_review_context>"))
        self.assertEqual(result, {})
        self.assertEqual(self.calls, [])

    def test_failure_stops_without_retry(self):
        self.results = [RuntimeError("test failure")]
        result = self.run_hook(self.event())
        self.assertFalse(result["continue"])
        self.assertEqual(self.state()["status"], "failed")
        self.run_hook(self.event("# Plan\nNew", turn="2"))
        self.assertEqual(len(self.calls), 1)

    def test_timeout_is_failure_not_approval(self):
        self.results = [subprocess.TimeoutExpired("claude", 300)]
        result = self.run_hook(self.event())
        self.assertFalse(result["continue"])
        self.assertEqual(self.state()["status"], "failed")

    def test_partial_or_multiple_plans_do_not_write(self):
        for text in ["<proposed_plan>partial", "<proposed_plan>a</proposed_plan><proposed_plan>b</proposed_plan>"]:
            result = self.run_hook(self.event(last_assistant_message=text))
            self.assertFalse(result["continue"])
        self.assertEqual(self.calls, [])

    def test_missing_assistant_text_fails_visibly(self):
        self.assertFalse(self.run_hook(self.event(last_assistant_message=None))["continue"])

    def test_symlinked_plan_destination_refused(self):
        (self.repo / ".agents").mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo / ".agents" / "plans").symlink_to(outside)
        self.assertFalse(self.run_hook(self.event())["continue"])
        self.assertEqual(list(outside.iterdir()), [])

    def test_skill_symlink_uses_physical_state(self):
        alias = self.root / "alias"
        alias.symlink_to(self.skill, target_is_directory=True)
        workflow.process(self.event(), alias, self.reviewer)
        self.assertTrue(next(self.skill.glob(".state/*/review.json")).exists())

    def test_reviewer_arguments_are_read_only_and_file_backed(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(workflow.shutil, "which", return_value="/bin/claude"):
            command = workflow.reviewer_command(self.repo / "plan.md", None, None)
        self.assertIn("--safe-mode", command)
        self.assertIn("--restricted", command)
        self.assertEqual(command[command.index("--tools") + 1], "Read,Glob,Grep")
        self.assertEqual(command[command.index("--model") + 1], "claude-opus-5")
        self.assertEqual(command[command.index("--effort") + 1], "low")
        self.assertNotIn("--bare", command)
        self.assertIn(str(self.repo / "plan.md"), command[2])

    def test_api_key_override_refused_without_printing_it(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "do-not-expose"}, clear=True), patch.object(workflow.shutil, "which", return_value="/bin/claude"):
            with self.assertRaisesRegex(RuntimeError, "subscription-only") as error:
                workflow.reviewer_command(self.repo / "plan.md", None, None)
        self.assertNotIn("do-not-expose", str(error.exception))

    def test_permission_denial_invalidates_reviewer_lgtm(self):
        payload = {"result": "Verdict: LGTM", "session_id": "x", "permission_denials": [{"tool_name": "Edit"}]}
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        with patch.object(workflow, "reviewer_command", return_value=["claude"]), patch.object(workflow.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "permission denial"):
                workflow.review(self.repo, self.repo / "plan.md", None, None)


if __name__ == "__main__":
    unittest.main()
