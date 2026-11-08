import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("launch_cloud.py")
SUCCESS = """
import os, sys
if not os.isatty(1):
    sys.exit("To start a new cloud session, run from a TTY.")
assert sys.argv[1:] == ["--cloud", "do the thing"], sys.argv
print("Created cloud session: Ship PR")
print("View: https://claude.ai/code/session_Abc123?from=cli")
"""


class LaunchCloudTests(unittest.TestCase):
    def launch(self, fake=None, *extra):
        with tempfile.TemporaryDirectory(prefix="ship-launch-") as directory:
            binary = Path(directory) / "claude"
            if fake is not None:
                binary.write_text(f"#!/usr/bin/env python3\n{fake}")
                binary.chmod(0o755)
            result = subprocess.run(["python3", str(SCRIPT), "do the thing", *extra],
                                    env={**os.environ, "CLAUDE_BIN": str(binary)},
                                    capture_output=True, text=True, timeout=20)
        return result.returncode, json.loads(result.stdout)

    def test_success_returns_session_and_clean_url(self):
        self.assertEqual(self.launch(SUCCESS), (0, dict(
            ok=True, session_id="session_Abc123", url="https://claude.ai/code/session_Abc123")))

    def test_child_reading_stdin_gets_end_of_file(self):
        code, report = self.launch("import sys\nsys.stdin.read()\n" + SUCCESS)
        self.assertEqual((code, report["session_id"]), (0, "session_Abc123"))

    def test_pre_launch_refusal_is_a_non_launch(self):
        code, report = self.launch('import sys\nsys.exit("Error: Starting a new cloud session with --cloud is interactive only")')
        self.assertEqual((code, report["launched"]), (1, False))
        self.assertIn("interactive only", report["error"])

    def test_workspace_trust_prompt_is_a_non_launch(self):
        code, report = self.launch('import sys\nprint("Quick safety check:\\x1b[1CIs\\x1b[1Cthis\\x1b[1Ca\\x1b[1Cproject\\x1b[1Cyou\\x1b[1Ctrust?")\nprint("\\u276f No, exit\\nYes,\\x1b[1CI\\x1b[1Ctrust\\x1b[1Cthis\\x1b[1Cfolder")\nsys.exit(1)')
        self.assertEqual((code, report["launched"]), (1, False))
        self.assertIn("workspace not trusted", report["error"])

    def test_unparsable_output_is_ambiguous(self):
        code, report = self.launch('print("\\x1b[31mSomething went sideways\\x1b[0m")')
        self.assertEqual((code, report["launched"]), (2, "unknown"))
        self.assertIn("Something went sideways", report["output_tail"])
        self.assertNotIn("\x1b", report["output_tail"])

    def test_missing_binary_is_a_non_launch(self):
        code, report = self.launch()
        self.assertEqual((code, report["ok"], report["launched"]), (1, False, False))

    def test_timeout_is_ambiguous(self):
        code, report = self.launch("import time\ntime.sleep(30)", "--timeout", "1")
        self.assertEqual((code, report["launched"], report["error"]), (2, "unknown", "timed out"))


if __name__ == "__main__":
    unittest.main()
