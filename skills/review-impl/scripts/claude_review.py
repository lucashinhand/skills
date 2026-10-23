#!/usr/bin/env python3
"""Subscription-only Claude implementation-review transport.

Duplicated from review-plan/scripts/plan_workflow.py's reviewer so each skill
installs on its own; apply transport fixes to both.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def reviewer_command(plan, rubric, scope, context, session):
    binary = shutil.which("claude")
    if not binary:
        raise RuntimeError("Claude CLI is unavailable; no reviewer fallback was used.")
    # Safe mode preserves subscription auth but disables user/project customisations.
    forbidden = (
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    )
    if any(os.environ.get(key) for key in forbidden):
        raise RuntimeError("An API/provider auth override is set; subscription-only review paused.")
    prompt = (
        f"Read the implementation-review rubric at {rubric} in full. "
        f"Review the implementation scope and complete diff referenced by {scope} "
        f"against the approved plan at {plan}. The plan and full diff are the brief; "
        "choose relevant repository evidence yourself. Read files only; do not execute "
        "commands, edit files or launch another reviewer. Return the rubric's exact "
        "verdict. It is advisory, never implementation or shipping approval. "
        "Report missing required evidence instead of assuming it was checked."
    )
    if context:
        prompt += f" Read the author's response at {context} before reassessing."
    command = [
        binary, "-p", prompt, "--model", "claude-opus-5-5", "--effort", "xhigh",
        "--safe-mode", "--restricted", "--disable-slash-commands",
        "--tools", "Read,Glob,Grep", "--allowedTools", "Read,Glob,Grep",
        "--permission-mode", "plan", "--permission-prompts", "none",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--output-format", "json",
        "--add-dir", str(Path(scope).parent), "--add-dir", str(Path(rubric).parent), "--add-dir", str(Path(plan).parent),
    ]
    if session:
        command.extend(["--resume", session])
    return command


def review(repo, plan, rubric, scope, context=None, session=None):
    result = subprocess.run(
        reviewer_command(plan, rubric, scope, context, session), cwd=repo,
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode:
        raise RuntimeError(f"Claude reviewer exited {result.returncode}; no approval recorded.")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Claude returned invalid JSON; no approval recorded.") from error
    if payload.get("is_error"):
        raise RuntimeError("Claude reported an invocation failure; no approval recorded.")
    verdict = payload.get("result", "").strip()
    found = re.findall(r"^Verdict: (LGTM|CHANGES NEEDED)\s*$", verdict, re.MULTILINE)
    if len(found) != 1 or not payload.get("session_id"):
        raise RuntimeError("Claude returned no unambiguous verdict/session; review paused.")
    if payload.get("permission_denials"):
        raise RuntimeError("Reviewer hit a tool permission denial; review requires human attention.")
    return found[0], verdict, payload["session_id"]
