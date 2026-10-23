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
DENIED_CHUNK = {"chunk_id": "x", "exit_code": 1, "output": "zsh:1: operation not permitted: /tmp/probe.txt\n"}


class CodexReviewTests(unittest.TestCase):
    def events(self, extra=(), verdict="LGTM"):
        return "\n".join(json.dumps(row) for row in [
            {"type": "thread.started", "thread_id": SESSION},
            *extra,
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Verdict: " + verdict}},
            {"type": "turn.completed"},
        ])

    def test_real_exec_event_shape(self):
        self.assertEqual(reviewer.parse_events(self.events()), ("LGTM", "Verdict: LGTM", SESSION))

    def test_error_invalidates_even_later_lgtm(self):
        for event in ({"type": "turn.failed"}, {"type": "item.completed", "item": {"type": "error", "message": "Code Mode unavailable"}}):
            with self.assertRaises(RuntimeError):
                reviewer.parse_events(self.events([event]))

    def test_failed_exploration_does_not_block_changes_needed(self):
        """A reviewer legitimately cats a missing file; that is not a broken reviewer."""
        failed = {"type": "item.completed", "item": {"type": "command_execution", "status": "failed"}}
        verdict, _, _ = reviewer.parse_events(self.events([failed], verdict="CHANGES NEEDED"))
        self.assertEqual(verdict, "CHANGES NEEDED")

    def test_failed_exploration_refuses_an_approval(self):
        failed = {"type": "item.completed", "item": {"type": "command_execution", "status": "failed"}}
        with self.assertRaisesRegex(RuntimeError, "failed tool call"):
            reviewer.parse_events(self.events([failed]))

    def verify(self, *texts):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sessions").mkdir()
            rows = [
                {"type": "session_meta", "payload": {"id": SESSION}},
                {"type": "turn_context", "payload": {"turn_id": "current", "cwd": str(root), "sandbox_policy": {"type": "read-only"}}},
                {"type": "response_item", "payload": {"type": "custom_tool_call_output", "internal_chat_message_metadata_passthrough": {"turn_id": "current"}, "output": [{"type": "input_text", "text": text} for text in texts]}},
            ]
            (root / "sessions" / ("rollout-" + SESSION + ".jsonl")).write_text("\n".join(json.dumps(row) for row in rows))
            reviewer.verify_transcript(SESSION, root, root)

    def test_transcript_tolerates_ordinary_exploration(self):
        """Shapes from real reviewer transcripts that must not void a review."""
        xcrun = "git: warning: confstr() failed with code 5\ngit: error: couldn't create cache file '/tmp/xcrun_db-A1' (errno=Operation not permitted)\ndiff --git a/a b/a"
        cases = {
            "missing file": json.dumps({"exit_code": 1, "output": "cat: x: No such file or directory"}),
            "xcrun noise on success": json.dumps({"chunk_id": "a", "exit_code": 0, "output": xcrun}),
            "xcrun noise on failure": json.dumps({"exit_code": 128, "output": xcrun + "\nfatal: bad revision"}),
            "file content quoting denials": json.dumps({"exit_code": 1, "output": "| Tool consolidation | costs |\nA failed deploy\nDENIAL = re.compile(r\"operation not permitted|permission denied\")"}),
            "code-mode prose": "| Tool consolidation | Reduce costs |\nThe sandbox denied nothing; a failed deploy creates no tag.",
            "running chunk": json.dumps({"chunk_id": "b", "session_id": 7, "output": "permission denied"}),
            "MCP not found": json.dumps({"isError": True, "content": [{"type": "text", "text": "GitHub API error 404: Not Found"}]}),
            "script syntax error": "Script error:\nSyntaxError: Unexpected token 'E', \"[Errno 1] O\"... is not valid JSON",
            "settled missing file": json.dumps([{"status": "fulfilled", "value": {"exit_code": 1, "output": "cat: x: No such file or directory"}}]),
            "settled ordinary rejection": json.dumps({"i": 0, "status": "rejected", "reason": "exec_command failed: No such file or directory"}),
            "plain exec missing file": "Chunk ID: a\nWall time: 0.0 seconds\nProcess exited with code 1\nOriginal token count: 5\nOutput:\ncat: x: No such file or directory\n",
            "plain exec success quoting denial": "Chunk ID: a\nWall time: 0.0 seconds\nProcess exited with code 0\nOriginal token count: 5\nOutput:\nzsh:1: operation not permitted: /tmp/x\n",
            "truncated success": 'Warning: truncated output (original token count: 9000)\nTotal output lines: 900\n\n{"chunk_id":"a","exit_code":0,"output":"zsh:1: operation not permitted…8000 tokens truncated…',
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.verify("Script completed\nWall time 0.1 seconds\nOutput:\n", text)
        self.verify("Script failed\nWall time 0.0 seconds\nOutput:\n", cases["script syntax error"])

    def test_transcript_refuses_real_denials(self):
        cases = {
            "shell write denied": ("Script completed\n", json.dumps({"exit_code": 1, "output": "zsh:1: operation not permitted: /tmp/probe.txt\n"})),
            "python write denied": ("Script completed\n", json.dumps({"exit_code": 1, "output": "PermissionError: [Errno 1] Operation not permitted: '.state/lock'\n"})),
            "git write denied": ("Script completed\n", json.dumps({"exit_code": 255, "output": "error: cannot open '.git/FETCH_HEAD': Operation not permitted\n"})),
            "harness rejection": ("Script failed\n", "Script error:\nexec_command failed: CreateProcess { message: \"Rejected(\\\"This action was rejected due to unacceptable risk.\\\")\" }"),
            "approval timeout": ("Script failed\n", "Script error:\nThe automatic permission approval review did not finish before its deadline."),
            "MCP refusal": ("Script completed\n", json.dumps({"isError": True, "content": [{"type": "text", "text": "This action was rejected due to unacceptable risk."}]})),
            "MCP auth": ("Script completed\n", json.dumps({"isError": True, "content": [{"type": "text", "text": "GitHub API error 401: Bad credentials"}]})),
            "settled denial": ("Script completed\n", json.dumps({"status": "fulfilled", "value": DENIED_CHUNK})),
            "indexed settled denial": ("Script completed\n", json.dumps({"i": 0, "status": "fulfilled", "value": DENIED_CHUNK})),
            "settled list denial": ("Script completed\n", json.dumps([{"status": "fulfilled", "value": DENIED_CHUNK}])),
            "settled MCP refusal": ("Script completed\n", json.dumps({"status": "fulfilled", "value": {"isError": True, "content": [{"type": "text", "text": "This action was rejected due to unacceptable risk."}]}})),
            "settled harness rejection": ("Script completed\n", json.dumps({"status": "rejected", "reason": "exec_command failed: CreateProcess { message: \"Rejected(\\\"This action was rejected due to unacceptable risk.\\\")\" }"})),
            "plain exec denial": ("Chunk ID: a\nWall time: 0.0 seconds\nProcess exited with code 1\nOriginal token count: 5\nOutput:\nzsh:1: operation not permitted: /tmp/probe.txt\n",),
            "truncated denial": ("Script completed\n", 'Warning: truncated output (original token count: 9000)\nTotal output lines: 900\n\n{"chunk_id":"a","exit_code":0,"output":"diff…8000 tokens truncated…"}\n{"chunk_id":"b","exit_code":128,"output":"fatal: Unable to create \'/repo/.git/index.lock\': Operation not permitted\\n"}'),
        }
        for name, texts in cases.items():
            with self.subTest(name), self.assertRaisesRegex(RuntimeError, "human attention"):
                self.verify(*texts)

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
                output = {"type": "response_item", "payload": {"type": "custom_tool_call_output", "internal_chat_message_metadata_passthrough": {"turn_id": turn}, "output": [{"type": "input_text", "text": json.dumps({"exit_code": 1, "output": "zsh:1: operation not permitted: /tmp/x"})}]}}
                path.write_text("\n".join(json.dumps(row) for row in base + [output]))
                if turn == "old":
                    reviewer.verify_transcript(SESSION, root, root)
                else:
                    with self.assertRaisesRegex(RuntimeError, "denial"):
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
