import json
from pathlib import Path
import tempfile
import unittest

import claude_plan


class ClaudePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve()
        self.skill = self.repo / "skill"
        self.plan = self.repo / "native-humble-blossom.md"
        self.plan.write_text("# Native title\n\nA scoped plan.\n")
        self.calls = []
        self.results = ["LGTM"]
        self.event = {"hook_event_name": "PreToolUse", "tool_name": "ExitPlanMode",
                      "permission_mode": "plan", "cwd": str(self.repo),
                      "session_id": "author", "tool_use_id": "tool-1",
                      "tool_input": {"planFilePath": str(self.plan), "plan": "Not the source of truth"}}

    def reviewer(self, repo, plan, context, session):
        self.calls.append((plan, context.read_text() if context else None, session))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result, "Verdict: " + result, "codex-reviewer"

    def run_hook(self, event=None):
        return claude_plan.process(event or self.event, self.skill, self.reviewer)

    def test_native_filename_and_exact_file_contents_no_marker(self):
        original = self.plan.read_bytes()
        result = self.run_hook()
        exported = self.repo / ".agents/plans" / self.plan.name
        self.assertEqual(exported.read_bytes(), original)
        self.assertEqual(self.plan.read_bytes(), original)
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertNotIn("plan-slug", exported.read_text())

    def test_revision_reuses_reviewer_and_filename(self):
        self.results = ["CHANGES NEEDED", "LGTM"]
        first = self.run_hook()
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertNotIn("plan-slug", first["hookSpecificOutput"]["permissionDecisionReason"])
        self.plan.write_text("# Changed title\nCorrected scope.\n")
        second = self.run_hook({**self.event, "session_id": "another", "tool_use_id": "tool-2"})
        self.assertEqual(second["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertNotEqual(self.calls[0][0], self.calls[1][0])
        self.assertEqual(self.calls[1][2], "codex-reviewer")

    def test_duplicate_rejected_plan_cannot_bypass_gate(self):
        self.results = ["CHANGES NEEDED"]
        self.run_hook()
        result = self.run_hook()
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(len(self.calls), 1)

    def test_duplicate_approved_plan_still_asks_human(self):
        self.run_hook()
        self.assertEqual(self.run_hook()["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertEqual(len(self.calls), 1)

    def test_missing_path_does_not_fall_back_to_inline_plan(self):
        with self.assertRaises(ValueError):
            self.run_hook({**self.event, "tool_input": {"plan": "# inline"}})
        self.assertEqual(self.calls, [])

    def test_native_bytes_are_not_normalised(self):
        for data in (b"# Native\r\nScope", b"# Native\r\nScope\r\n"):
            self.plan.write_bytes(data)
            self.results = ["LGTM"]
            self.run_hook()
            exported = self.repo / ".agents/plans" / self.plan.name
            self.assertEqual(exported.read_bytes(), data)
            self.assertEqual(self.calls[-1][0].read_bytes(), data)

    def test_native_source_changed_during_review_refuses_approval(self):
        def reviewer(repo, snapshot, context, session):
            self.plan.write_text("Changed during review")
            return "LGTM", "Verdict: LGTM", "codex-reviewer"
        result = claude_plan.process(self.event, self.skill, reviewer)
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("No current approval", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_legacy_native_crlf_approval_does_not_consume_a_pass(self):
        self.plan.write_bytes(b"# Native\r\nScope")
        self.run_hook()
        state_file = next(self.skill.glob(".state/*/review.json"))
        state = json.loads(state_file.read_text())
        self.assertEqual(state["plan_hash"], claude_plan.workflow.digest("# Native\nScope"))
        for key in ("schema_version", "submitted_plan_sha256", "approved_plan_sha256", "snapshot_path", "feedback_path"):
            state.pop(key)
        state_file.write_text(json.dumps(state))
        result = self.run_hook()
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads(state_file.read_text())["passes"], 1)

    def test_error_containing_lgtm_is_not_approval(self):
        self.results = [RuntimeError("invalid LGTM")]
        result = self.run_hook()
        self.assertFalse(result["continue"])
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_three_response_limit(self):
        self.results = ["CHANGES NEEDED"] * 3
        for number in range(3):
            self.plan.write_text(f"# Revision {number}\n")
            result = self.run_hook()
        self.assertFalse(result["continue"])
        self.plan.write_text("# Fourth revision\n")
        self.assertFalse(self.run_hook()["continue"])
        self.assertEqual(len(self.calls), 3)

    def test_context_uses_exact_author_transcript_and_preserves_plan(self):
        self.results = ["CHANGES NEEDED", "LGTM"]
        self.run_hook()
        transcript = self.repo / "author.jsonl"
        rows = [
            {"type": "assistant", "sessionId": "author", "uuid": "a", "message": {"content": [
                {"type": "tool_use", "name": "ExitPlanMode", "input": self.event["tool_input"]}]}},
            {"type": "assistant", "sessionId": "author", "uuid": "b", "message": {"content": [
                {"type": "text", "text": "<plan_review_context>Human scope decision.</plan_review_context>"}]}}
        ]
        transcript.write_text("\n".join(json.dumps(row) for row in rows))
        result = self.run_hook({**self.event, "hook_event_name": "Stop", "transcript_path": str(transcript)})
        self.assertIn("LGTM", result["systemMessage"])
        self.assertNotIn("review_approved", result)
        self.assertEqual(self.calls[1][1], "Human scope decision.\n")
        self.assertEqual(self.calls[1][2], "codex-reviewer")
        self.assertEqual(self.calls[1][0].read_text(), self.plan.read_text())


if __name__ == "__main__":
    unittest.main()
