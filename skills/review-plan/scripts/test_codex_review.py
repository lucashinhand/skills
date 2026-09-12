import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("codex_review", Path(__file__).with_name("codex_review.py"))
reviewer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reviewer)
SESSION = "01a09413-501b-79a1-be90-c54317bc77f5"


class CodexReviewTests(unittest.TestCase):
    def events(self, extra=()):
        return "\n".join(json.dumps(row) for row in [
            {"type": "thread.started", "thread_id": SESSION},
            *extra,
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Verdict: LGTM"}},
            {"type": "turn.completed"},
        ])

    def test_real_exec_event_shape(self):
        self.assertEqual(reviewer.parse_events(self.events()), ("LGTM", "Verdict: LGTM", SESSION))

    def test_error_invalidates_even_later_lgtm(self):
        for event in ({"type": "turn.failed"}, {"type": "item.completed", "item": {"type": "error", "message": "Code Mode unavailable"}}):
            with self.assertRaises(RuntimeError):
                reviewer.parse_events(self.events([event]))

    def test_missing_completion_and_ambiguous_verdict_refused(self):
        for output in (self.events().replace('"turn.completed"', '"turn.started"'), self.events().replace('Verdict: LGTM', 'Verdict: LGTM\\nVerdict: CHANGES NEEDED')):
            with self.assertRaises(RuntimeError):
                reviewer.parse_events(output)

    def test_command_initial_and_resume_keep_safety_flags(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(reviewer.shutil, "which", return_value="/bin/codex"):
            for session in (None, SESSION):
                command = reviewer.command(Path("/repo"), "review", session)
                self.assertEqual(command[1:3], ["-a", "never"])
                self.assertEqual(command[command.index("-s") + 1], "read-only")
                self.assertIn("mcp_servers={}", command)
                self.assertNotIn("--ephemeral", command)
                if session:
                    self.assertEqual(command[-4:], ["resume", "--json", SESSION, "review"])

    def test_mcp_is_explicitly_read_only(self):
        with patch.dict(os.environ, {"GITHUB_PERSONAL_ACCESS_TOKEN": "secret-not-in-command"}, clear=True), patch.object(reviewer.shutil, "which", return_value="/bin/codex"):
            command = reviewer.command(Path("/repo"), "review")
        self.assertNotIn("secret-not-in-command", " ".join(command))
        config = next(arg for arg in command if arg.startswith("mcp_servers="))
        self.assertIn("enabled_tools", config)
        self.assertNotIn("issue_write", config)
        self.assertNotIn("create_pull_request", config)

    def test_api_override_refused_without_exposing_secret(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}, clear=True), patch.object(reviewer.shutil, "which", return_value="/bin/codex"):
            with self.assertRaises(RuntimeError) as caught:
                reviewer.command(Path("/repo"), "review")
        self.assertNotIn("secret", str(caught.exception))

    def test_transcript_denial_is_detected_but_old_turn_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sessions").mkdir()
            path = root / "sessions" / ("rollout-" + SESSION + ".jsonl")
            base = [
                {"type": "session_meta", "payload": {"id": SESSION}},
                {"type": "turn_context", "payload": {"turn_id": "current", "cwd": str(root), "sandbox_policy": {"type": "read-only"}}},
            ]
            for turn in ("old", "current"):
                output = {"type": "response_item", "payload": {"type": "custom_tool_call_output", "internal_chat_message_metadata_passthrough": {"turn_id": turn}, "output": [{"type": "input_text", "text": json.dumps({"exit_code": 1, "output": "operation not permitted"})}]}}
                path.write_text("\n".join(json.dumps(row) for row in base + [output]))
                if turn == "old":
                    reviewer.verify_transcript(SESSION, root, root)
                else:
                    with self.assertRaisesRegex(RuntimeError, "tool failed"):
                        reviewer.verify_transcript(SESSION, root, root)

    def test_transcript_wrong_sandbox_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sessions").mkdir()
            path = root / "sessions" / ("rollout-" + SESSION + ".jsonl")
            rows = [{"type": "session_meta", "payload": {"id": SESSION}}, {"type": "turn_context", "payload": {"cwd": str(root), "sandbox_policy": {"type": "workspace-write"}}}]
            path.write_text("\n".join(json.dumps(row) for row in rows))
            with self.assertRaisesRegex(RuntimeError, "sandbox"):
                reviewer.verify_transcript(SESSION, root, root)


if __name__ == "__main__":
    unittest.main()
