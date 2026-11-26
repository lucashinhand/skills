#!/usr/bin/env python3
"""Launch one Claude cloud session from a terminal Anthropic requires; print JSON."""
import argparse
import json
import os
import pty
import re
import select
import shutil
import signal
import sys
import termios
import time

SESSION = re.compile(r"https://claude\.ai/code/((?:session|cse)_[A-Za-z0-9]+)|Session ID: ((?:session|cse)_[A-Za-z0-9]+)")
REFUSALS = ("run from a TTY", "interactive only", "Unable to get organization UUID",
            "disabled by your organization")
# The one-time workspace trust prompt appears before launch; the CLI exits when it goes unanswered.
# The terminal UI positions words with cursor moves, so compare without whitespace.
UNTRUSTED = "YesItrustthisfolder"
CONTROL = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b.|[\x00-\x08\x0b-\x1f\x7f]")


def run(binary, prompt, timeout):
    pid, master = pty.fork()
    if pid == 0:
        try:
            attributes = termios.tcgetattr(0)
            attributes[3] &= ~termios.ECHO
            termios.tcsetattr(0, termios.TCSANOW, attributes)
            os.execv(binary, [binary, "--cloud", prompt])
        finally:
            os._exit(127)
    output, deadline = b"", time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        readable, writable, _ = select.select([master], [master], [], min(remaining, 0.05))
        try:
            if readable:
                if not (chunk := os.read(master, 4096)):
                    break
                output += chunk
            elif writable:
                # pty.fork never closes stdin, so end-of-file keeps a stdin reader from hanging.
                os.write(master, b"\x04")
                time.sleep(0.05)
        except OSError:
            break
    else:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        if os.waitpid(pid, os.WNOHANG) == (0, 0):
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        return output, None
    return output, os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])


def result(binary, prompt, timeout):
    if not binary:
        return 1, dict(ok=False, launched=False, error="claude executable not found")
    output, code = run(binary, prompt, timeout)
    text = CONTROL.sub("", output.decode(errors="replace").replace("\r\n", "\n"))
    if match := SESSION.search(text):
        session = match.group(1) or match.group(2)
        return 0, dict(ok=True, session_id=session, url=f"https://claude.ai/code/{session}")
    if UNTRUSTED in re.sub(r"[\s,]", "", text):
        return 1, dict(ok=False, launched=False,
                       error="workspace not trusted: open Claude Code here once and trust the folder")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if refusal := next((line for line in reversed(lines) if any(r in line for r in REFUSALS)), None):
        return 1, dict(ok=False, launched=False, error=refusal)
    error = "timed out" if code is None else f"no session in output (exit {code})"
    return 2, dict(ok=False, launched="unknown", error=error, output_tail=text[-500:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    code, report = result(shutil.which(os.environ.get("CLAUDE_BIN", "claude")), args.prompt, args.timeout)
    print(json.dumps(report))
    sys.exit(code)


if __name__ == "__main__":
    main()
