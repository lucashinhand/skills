#!/usr/bin/env python3
"""Subscription-only Codex review transport; no plan lifecycle bookkeeping."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

# A denied or disabled tool means the reviewer could not do its job. An ordinary
# non-zero exit does not: exploring a repository legitimately cats a file that is
# not there or runs a glob that matches nothing. Tool output also carries whole
# files that mention denials, so it is searched only once the command itself
# failed, and successful output is never searched.
#
# Refusals and auth failures lead a code-mode script error or MCP error; other
# errors (a missing path, an unknown issue) are ordinary exploration.
REFUSAL = re.compile(r"rejected|not permitted|permission denied|sandbox denied|approval review|cancelled|unauthori[sz]ed|forbidden|bad credentials|\b40[13]\b", re.I)
# A denied command reports errno-style (`zsh:1: operation not permitted: <path>`,
# `[Errno 1] Operation not permitted`), unlike prose quoting the phrase.
ERRNO_DENIAL = re.compile(r"(?:: |\] )(?:operation not permitted|permission denied|read-only file system)\b", re.I)


# exec_command called outside code mode returns a text header, not a JSON chunk.
PLAIN_EXEC = re.compile(r"^Process exited with code (\d+)$.*?^Output:\n(.*)", re.M | re.S)
TRUNCATED_FAILURE = re.compile(r'"exit_code":\s*[1-9]')


def first_line(text):
    return text.strip().split("\n", 1)[0]


def parsed(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    plain = PLAIN_EXEC.search(text)
    if plain:
        return {"exit_code": int(plain.group(1)), "output": plain.group(2)}
    # Outer truncation elides the middle of the JSON, so it no longer parses.
    if text.startswith("Warning: truncated output") and TRUNCATED_FAILURE.search(text):
        return {"exit_code": 1, "output": text}
    return None


def results(value):
    # Code-mode scripts may wrap chunks, e.g. Promise.allSettled's {status, value}.
    if isinstance(value, dict):
        yield value
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            yield from results(item)


def tool_failure(blocks):
    texts = [str(block.get("text", "")) for block in blocks]
    script_error = next((text for text in texts if text.startswith("Script error:")), "")
    refused_script = texts and texts[0].startswith("Script failed") and REFUSAL.search(first_line(script_error.removeprefix("Script error:")))
    if refused_script:
        return "Reviewer tool denial; human attention required."
    for result in (result for text in texts for result in results(parsed(text))):
        rejected_call = result.get("status") == "rejected" and REFUSAL.search(first_line(str(result.get("reason", ""))))
        if rejected_call:
            return "Reviewer tool denial; human attention required."
        mcp_error = "".join(item.get("text", "") for item in result.get("content") or []) if result.get("isError") else ""
        if REFUSAL.search(first_line(mcp_error)):
            return "Reviewer tool failed; human attention required."
        denied_command = result.get("exit_code") not in (0, None) and ERRNO_DENIAL.search(str(result.get("output", "")))
        if denied_command:
            return "Reviewer tool denial; human attention required."
    return None


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
            "-m", "gpt-6-astra", "-c", 'model_reasoning_effort="medium"',
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
    degraded = False
    for event in events:
        item = event.get("item", {})
        if event.get("type") in ("error", "turn.failed") or item.get("type") == "error":
            raise RuntimeError("Codex reported an execution error; no approval recorded.")
        if item.get("status") == "failed" or item.get("error"):
            degraded = True
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            messages.append(item.get("text", ""))
    feedback = messages[-1].strip() if messages else ""
    verdicts = re.findall(r"^Verdict: (LGTM|CHANGES NEEDED)\s*$", feedback, re.MULTILINE)
    if len(verdicts) != 1:
        raise RuntimeError("Codex returned no unambiguous verdict.")
    if degraded and verdicts[0] == "LGTM":
        raise RuntimeError("Codex approved after a failed tool call; no approval recorded.")
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
        failure = tool_failure(output if isinstance(output, list) else [{"text": output}])
        if failure:
            raise RuntimeError(failure)


def review(repo, plan, rubric, context=None, session=None, scope=None):
    prompt = f"Read {rubric} in full and review the complete plan at {plan}. Read repository evidence only; do not implement or launch another reviewer. Return the rubric's exact verdict. If required context or a tool is unavailable, return CHANGES NEEDED and explain the blocker, never LGTM."
    if scope:
        prompt = f"Read the implementation-review rubric at {rubric} in full. Review the implementation scope and complete diff referenced by {scope} against the approved plan at {plan}. The plan and full diff are the brief; choose relevant repository evidence yourself. Read-only review: do not implement or launch another reviewer. Return the rubric's exact verdict. It is advisory, never implementation or shipping approval. Report missing required evidence instead of assuming it was checked."
        prompt += " This is source inspection, matching the Claude reviewer: use file-reading commands, not project-code execution or tests. Do not use shell heredocs; they require temporary writes in this runtime."
    if context:
        prompt += f" Read the author's response at {context} before reassessing."
    args = command(repo, prompt, session)
    auth = subprocess.run([args[0], "login", "status"], capture_output=True, text=True, timeout=20)
    if auth.returncode or "Logged in using ChatGPT" not in auth.stdout + auth.stderr:
        raise RuntimeError("Codex ChatGPT subscription login is required.")
    completed = subprocess.run(args, cwd=repo, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
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
