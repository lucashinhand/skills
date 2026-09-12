#!/usr/bin/env python3
"""Subscription-only Codex review transport; no plan lifecycle bookkeeping."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

READ_TOOLS = ["get_me", "get_file_contents", "issue_read", "pull_request_read", "search_issues", "search_code", "list_commits", "get_commit"]


def command(repo, prompt, session=None):
    binary = shutil.which("codex")
    if not binary:
        raise RuntimeError("Codex CLI unavailable; no fallback used.")
    if any(os.environ.get(key) for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")):
        raise RuntimeError("API/provider override set; subscription-only review paused.")
    servers = '{}'
    if os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
        servers = '{github = {url = "https://api.githubcopilot.com/mcp/", bearer_token_env_var = "GITHUB_PERSONAL_ACCESS_TOKEN", enabled_tools = ' + json.dumps(READ_TOOLS) + '}}'
    args = [binary, "-a", "never", "exec", "-C", str(repo), "-s", "read-only",
            "-m", "gpt-6-astra", "-c", 'model_reasoning_effort="low"',
            "-c", 'model_provider="openai"', "--ignore-user-config",
            "--disable", "hooks", "--disable", "apps", "--disable", "plugins",
            "-c", "mcp_servers=" + servers]
    if session:
        args.extend(["resume", "--json", session, prompt])
    else:
        args.extend(["--json", prompt])
    return args


def parse_events(stdout):
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    sessions = {event["thread_id"] for event in events if event.get("type") == "thread.started"}
    complete = any(event.get("type") == "turn.completed" for event in events)
    if len(sessions) != 1 or not complete:
        raise RuntimeError("Codex returned no completed review/session.")
    messages = []
    for event in events:
        item = event.get("item", {})
        if event.get("type") in ("error", "turn.failed") or item.get("type") == "error":
            raise RuntimeError("Codex reported an execution error; no approval recorded.")
        if item.get("status") == "failed" or item.get("error"):
            raise RuntimeError("Codex tool execution failed; no approval recorded.")
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            messages.append(item.get("text", ""))
    feedback = messages[-1].strip() if messages else ""
    verdicts = re.findall(r"^Verdict: (LGTM|CHANGES NEEDED)\s*$", feedback, re.MULTILINE)
    if len(verdicts) != 1:
        raise RuntimeError("Codex returned no unambiguous verdict.")
    return verdicts[0], feedback, sessions.pop()


def verify_transcript(session, repo, codex_home):
    if not re.fullmatch(r"[0-9a-f-]{36}", session):
        raise RuntimeError("Invalid Codex reviewer session ID.")
    paths = list((codex_home / "sessions").glob("**/*-" + session + ".jsonl"))
    if len(paths) != 1:
        raise RuntimeError("Cannot identify the exact Codex reviewer transcript.")
    rows = [json.loads(line) for line in paths[0].read_text().splitlines() if line.strip()]
    metadata = [row["payload"] for row in rows if row.get("type") == "session_meta"]
    contexts = [row["payload"] for row in rows if row.get("type") == "turn_context"]
    if len(metadata) != 1 or metadata[0].get("id") != session or not contexts:
        raise RuntimeError("Reviewer transcript identity unavailable.")
    context = contexts[-1]
    if Path(context.get("cwd", "")).resolve() != repo.resolve() or context.get("sandbox_policy", {}).get("type") != "read-only":
        raise RuntimeError("Reviewer cwd/read-only sandbox could not be verified.")
    turn = context["turn_id"]
    # Exec JSONL omits nested code-mode results in 0.154; inspect this turn only.
    for row in rows:
        payload = row.get("payload", {})
        if payload.get("type") not in ("custom_tool_call_output", "function_call_output"):
            continue
        if payload.get("internal_chat_message_metadata_passthrough", {}).get("turn_id") != turn:
            continue
        output = payload.get("output", [])
        blocks = output if isinstance(output, list) else [{"text": output}]
        for block in blocks:
            value = block.get("text", "")
            try:
                result = json.loads(value)
            except (ValueError, TypeError):
                if re.search(r"operation not permitted|permission denied|tool.*(?:failed|disabled)", str(value), re.I):
                    raise RuntimeError("Reviewer tool denial; human attention required.")
                continue
            if isinstance(result, dict) and (result.get("isError") or result.get("exit_code") not in (None, 0)):
                raise RuntimeError("Reviewer tool failed; human attention required.")


def review(repo, plan, rubric, context=None, session=None):
    prompt = f"Read {rubric} in full and review the complete plan at {plan}. Read repository evidence only; do not implement or launch another reviewer. Return the rubric's exact verdict. If required context or a tool is unavailable, return CHANGES NEEDED and explain the blocker, never LGTM."
    if context:
        prompt += f" Read the author's response at {context} before reassessing."
    args = command(repo, prompt, session)
    auth = subprocess.run([args[0], "login", "status"], capture_output=True, text=True, timeout=20)
    if auth.returncode or "Logged in using ChatGPT" not in auth.stdout + auth.stderr:
        raise RuntimeError("Codex ChatGPT subscription login is required.")
    completed = subprocess.run(args, cwd=repo, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    if completed.returncode:
        raise RuntimeError(f"Codex reviewer exited {completed.returncode}; no approval recorded.")
    result = parse_events(completed.stdout)
    if session and result[2] != session:
        raise RuntimeError("Codex resumed a different session; review paused.")
    verify_transcript(result[2], repo, Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, default=Path(__file__).resolve().parent.parent / "SKILL.md")
    parser.add_argument("--context", type=Path)
    parser.add_argument("--resume")
    args = parser.parse_args()
    try:
        verdict, feedback, session = review(args.repo.resolve(), args.plan.resolve(), args.rubric.resolve(), args.context, args.resume)
        print(json.dumps({"verdict": verdict, "feedback": feedback, "session_id": session}))
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)
