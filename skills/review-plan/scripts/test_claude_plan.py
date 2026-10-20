import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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

    def write_transcript(self, path, session="author"):
        row = {"type": "assistant", "sessionId": session, "uuid": "a", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": str(path)}}]}}
        transcript = self.repo / "author.jsonl"
        transcript.write_text(json.dumps(row))
        return {**self.event, "tool_input": {"plan": "# inline"}, "transcript_path": str(transcript)}

    def use_plans_dir(self):
        plans = self.repo / "plans"
        plans.mkdir(exist_ok=True)
        patcher = mock.patch.object(claude_plan, "PLANS_DIR", plans)
        patcher.start()
        self.addCleanup(patcher.stop)
        return plans

    def test_transcript_write_to_plans_dir_supplies_missing_path(self):
        plan = self.use_plans_dir() / "wise-otter.md"
        plan.write_text("# Written plan\n")
        result = self.run_hook(self.write_transcript(plan))
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertEqual(self.calls[0][0].read_text(), "# Written plan\n")

    def test_transcript_write_outside_plans_dir_is_ignored(self):
        self.use_plans_dir()
        with self.assertRaises(ValueError):
            self.run_hook(self.write_transcript("/tmp/notes.md"))
        self.assertEqual(self.calls, [])

    def test_write_to_other_plans_directory_is_ignored(self):
        self.use_plans_dir()
        other = self.repo / "elsewhere" / "plans"
        other.mkdir(parents=True)
        (other / "x.md").write_text("# Other\n")
        with self.assertRaises(ValueError):
            self.run_hook(self.write_transcript(other / "x.md"))
        self.assertEqual(self.calls, [])

    def test_failed_write_is_ignored(self):
        plan = self.use_plans_dir() / "wise-otter.md"
        plan.write_text("# Blocked\n")
        event = self.write_transcript(plan)
        path = Path(event["transcript_path"])
        row = json.loads(path.read_text())
        row["message"]["content"][0]["id"] = "w1"
        result_row = {"type": "user", "sessionId": "author", "uuid": "r", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "w1", "is_error": True, "content": "blocked"}]}}
        path.write_text("\n".join(json.dumps(r) for r in (row, result_row)))
        with self.assertRaises(ValueError):
            self.run_hook(event)
        self.assertEqual(self.calls, [])

    def test_later_write_supersedes_a_historical_exit_plan_path(self):
        # A session that reviewed one plan, then wrote another, proposes the newer
        # one: resolving the stale path would review it behind the older approval.
        later = self.use_plans_dir() / "later.md"
        later.write_text("# Later\n")
        rows = [
            {"type": "assistant", "sessionId": "author", "uuid": "a", "message": {"content": [
                {"type": "tool_use", "name": "ExitPlanMode", "input": {"planFilePath": str(self.plan)}}]}},
            {"type": "assistant", "sessionId": "author", "uuid": "b", "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": str(later)}}]}}]
        transcript = self.repo / "author.jsonl"
        transcript.write_text("\n".join(json.dumps(row) for row in rows))
        self.run_hook({**self.event, "tool_input": {}, "transcript_path": str(transcript)})
        self.assertEqual(self.calls[0][0].read_text(), "# Later\n")

    def test_live_event_path_beats_transcript_evidence(self):
        later = self.use_plans_dir() / "later.md"
        later.write_text("# Later\n")
        rows = [{"type": "assistant", "sessionId": "author", "uuid": "a", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": str(later)}}]}}]
        transcript = self.repo / "author.jsonl"
        transcript.write_text("\n".join(json.dumps(row) for row in rows))
        self.run_hook({**self.event, "transcript_path": str(transcript)})
        self.assertEqual(self.calls[0][0].read_text(), self.plan.read_text())

    def test_stop_hook_uses_transcript_fallback(self):
        plan = self.use_plans_dir() / "wise-otter.md"
        plan.write_text("# Written plan\n")
        self.results = ["CHANGES NEEDED", "LGTM"]
        self.run_hook({**self.event, "tool_input": {"planFilePath": str(plan)}})
        rows = [
            {"type": "assistant", "sessionId": "author", "uuid": "a", "message": {"content": [
                {"type": "tool_use", "name": "ExitPlanMode", "input": {"plan": "# inline"}},
                {"type": "tool_use", "name": "Write", "input": {"file_path": str(plan)}}]}},
            {"type": "assistant", "sessionId": "author", "uuid": "b", "message": {"content": [
                {"type": "text", "text": "<plan_review_context>Human scope decision.</plan_review_context>"}]}}]
        transcript = self.repo / "author.jsonl"
        transcript.write_text("\n".join(json.dumps(row) for row in rows))
        result = self.run_hook({**self.event, "hook_event_name": "Stop", "transcript_path": str(transcript)})
        self.assertIn("LGTM", result["systemMessage"])
        self.assertEqual(self.calls[1][0].read_text(), "# Written plan\n")
        self.assertEqual(self.calls[1][1], "Human scope decision.\n")

    def test_transcript_from_another_session_is_rejected(self):
        event = self.write_transcript(self.repo / "plans" / "wise-otter.md", session="other")
        with self.assertRaisesRegex(ValueError, "session mismatch"):
            self.run_hook(event)


if __name__ == "__main__":
    unittest.main()
