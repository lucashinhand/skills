#!/usr/bin/env python3
"""Persist native Codex plans and run a bounded, read-only Claude review."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

SKILL = Path(__file__).resolve().parent.parent
MAX_PASSES = 3
PLAN = re.compile(r"<proposed_plan>\s*([\s\S]*?)\s*</proposed_plan>")
CONTEXT = re.compile(r"<plan_review_context>\s*([\s\S]*?)\s*</plan_review_context>")
SLUG = re.compile(r"<!-- plan-slug: ([a-z0-9]+(?:-[a-z0-9]+)*) -->")


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def extract(pattern, text):
    matches = pattern.findall(text or "")
    return matches[0].strip() if len(matches) == 1 and matches[0].strip() else None


def reviewer_command(plan, context, session):
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
        f"Read the review rubric at {SKILL / 'SKILL.md'} in full. "
        f"Review the implementation plan at {plan}. "
        "The plan file is the current complete proposal. Inspect relevant repository files "
        "read-only. Do not launch another reviewer, execute commands, edit files or implement. "
        "Return the rubric's exact verdict contract. Approval means plan quality only, "
        "never authorisation to implement. If essential context is unavailable, request it."
    )
    if context:
        prompt += f" Before reassessing, read the author's response at {context}."
    command = [
        binary, "-p", prompt, "--model", "claude-opus-5", "--effort", "low",
        "--safe-mode", "--restricted", "--disable-slash-commands",
        "--tools", "Read,Glob,Grep", "--allowedTools", "Read,Glob,Grep",
        "--permission-mode", "plan", "--permission-prompts", "none",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--output-format", "json", "--add-dir", str(SKILL),
    ]
    if session:
        command.extend(["--resume", session])
    return command


def review(repo, plan, context, session):
    result = subprocess.run(
        reviewer_command(plan, context, session), cwd=repo,
        capture_output=True, text=True, timeout=300,
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


def warning(message):
    return {"continue": False, "stopReason": message, "systemMessage": message}


def native_output(event):
    # Codex 0.154 stores collaboration mode and Plan items in the exact rollout.
    # permission_mode describes permissions, not the collaboration mode.
    with Path(event["transcript_path"]).open() as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    session, turn = event["session_id"], event["turn_id"]
    repo = Path(event["cwd"]).resolve()
    metadata = [row["payload"] for row in rows if row.get("type") == "session_meta"]
    if len(metadata) != 1 or metadata[0].get("id") != session:
        raise ValueError("Transcript session does not match hook session.")
    contexts = [row["payload"] for row in rows if row.get("type") == "turn_context" and row.get("payload", {}).get("turn_id") == turn]
    if not contexts or Path(contexts[-1].get("cwd", "")).resolve() != repo:
        raise ValueError("Transcript turn or working directory does not match hook.")
    mode = contexts[-1].get("collaboration_mode", {}).get("mode")
    if mode not in ("plan", "default"):
        raise ValueError("Transcript collaboration mode is unavailable.")
    if mode != "plan":
        return None
    messages = []
    for row in rows:
        payload = row.get("payload", {})
        if row.get("type") == "event_msg" and payload.get("type") == "item_completed" and payload.get("turn_id") == turn and payload.get("thread_id") == session:
            item = payload.get("item", {})
            if item.get("type") == "Plan":
                messages.append("<proposed_plan>" + item["text"] + "</proposed_plan>")
        if row.get("type") == "response_item" and payload.get("role") == "assistant" and payload.get("phase") == "final_answer" and payload.get("internal_chat_message_metadata_passthrough", {}).get("turn_id") == turn:
            messages.append("".join(part.get("text", "") for part in payload.get("content", []) if part.get("type") == "output_text"))
    # Hook continuations append revised snapshots within the same turn.
    return messages[-1] if messages else ""


def process(event, skill=SKILL, run_review=review):
    if event.get("hook_event_name") != "Stop":
        return {}
    if event.get("agent_id"):
        return {}
    if not event.get("transcript_path") and event.get("permission_mode") != "plan":
        return {}
    session = event.get("session_id")
    turn = event.get("turn_id")
    cwd = event.get("cwd")
    if not all(isinstance(value, str) and value for value in (session, turn, cwd)):
        return warning("Plan export paused: missing session, turn or working directory.")
    repo = Path(cwd).resolve()
    text = event.get("last_assistant_message")
    if event.get("transcript_path"):
        try:
            text = native_output(event)
        except (OSError, ValueError, KeyError, TypeError):
            return warning("Plan export paused: cannot verify this turn's transcript output.")
        if text is None:
            return {}
    if not isinstance(text, str):
        return warning("Plan export paused: this runtime did not supply assistant text.")
    plan = extract(PLAN, text)
    context = extract(CONTEXT, text)
    if plan is None and context is None:
        if "<proposed_plan>" in text or "<plan_review_context>" in text:
            return warning("Plan export paused: incomplete or ambiguous output block.")
        return {}
    slug = extract(SLUG, text)
    if not slug or len(slug) > 80:
        return warning("Plan export paused: include one stable <!-- plan-slug: descriptive-unique-slug --> marker in the proposal or context reply.")
    return process_plan(repo, slug, text, plan, context, turn, skill, run_review)


def process_plan(repo, slug, text, plan, context, turn, skill, run_review, reviewer_name="Claude", native=False):
    key = digest(str(repo) + "\0" + slug)[:24]
    state_dir = skill.resolve() / ".state" / key
    state_dir.mkdir(parents=True, exist_ok=True)
    # Serialise a plan across author sessions, including duplicate hook delivery.
    with (state_dir / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_file = state_dir / "review.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        output_hash = digest(text)
        if not native and state.get("last_turn") == turn and state.get("last_output_hash") == output_hash:
            return {}
        if state.get("status") in ("failed", "paused"):
            return warning(f"Plan review is {state['status']}; human intervention required. State: {state_file}")
        if context and state.get("status") != "changes_needed":
            return {}
        if plan:
            if not state:
                state = {"plan_name": f"{slug}.md", "passes": 0}
            plan_dir = repo / ".agents" / "plans"
            plan_dir.mkdir(parents=True, exist_ok=True)
            if plan_dir.resolve() != plan_dir:
                return warning("Plan export refused a redirected plans directory.")
            path = plan_dir / state["plan_name"]
            if path.is_symlink():
                return warning("Plan export refused a symlinked plan file.")
            atomic_write(path, plan if native else plan + "\n")
            plan_hash = digest(plan)
            if state.get("plan_hash") == plan_hash and state.get("status") == "reviewed":
                return {**({"review_approved": True} if native else {}), "systemMessage": f"Unchanged reviewed plan: {path}. Await explicit human approval."}
            if state.get("plan_hash") == plan_hash and state.get("status") == "changes_needed":
                return warning("Plan unchanged: revise it or provide <plan_review_context>pushback/context</plan_review_context>.")
            state["plan_hash"] = plan_hash
        path = repo / ".agents" / "plans" / state["plan_name"]
        context_path = None
        if context:
            context_path = state_dir / "author-context.md"
            atomic_write(context_path, context + "\n")
        state["last_turn"] = turn
        state["last_output_hash"] = output_hash
        if state["passes"] >= MAX_PASSES:
            state["status"] = "paused"
            atomic_write(state_file, json.dumps(state, indent=2) + "\n")
            return warning(f"Three review passes used. Plan: {path}. Ask the human; do not implement.")
        state["passes"] += 1
        state["status"] = "reviewing"
        atomic_write(state_file, json.dumps(state, indent=2) + "\n")
        try:
            result, feedback, reviewer_session = run_review(
                repo, path, context_path, state.get("reviewer_session")
            )
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
            state["status"] = "failed"
            atomic_write(state_file, json.dumps(state, indent=2) + "\n")
            return warning(f"Plan saved to {path}, but review failed: {error}. Do not implement.")
        state["reviewer_session"] = reviewer_session
        state["status"] = "reviewed" if result == "LGTM" else "changes_needed"
        atomic_write(state_dir / "latest-review.md", feedback + "\n")
        if result != "LGTM" and state["passes"] >= MAX_PASSES:
            state["status"] = "paused"
        atomic_write(state_file, json.dumps(state, indent=2) + "\n")
        if result == "LGTM":
            return {**({"review_approved": True} if native else {}), "systemMessage": f"{reviewer_name} review pass {state['passes']}: LGTM. Plan: {path}. Await explicit human implementation approval.\n{feedback}"}
        if state["status"] == "paused":
            return warning(f"Three review passes used. Plan: {path}. Ask the human; do not implement.\n{feedback}")
        if native:
            return {"decision": "block", "reason": (
                f"Plan saved to {path}. Independent {reviewer_name} review pass {state['passes']}/{MAX_PASSES}. "
                "Stay in Plan Mode. This feedback is not human direction or implementation approval. "
                "Incorporate or explain why you disagree. Keep using your native plan file. "
                "If context is requested, do not revise yet: respond with a complete "
                "<plan_review_context>...</plan_review_context> block containing relevant human "
                "decisions, constraints and author reasoning. Use this block for pushback too. "
                "Otherwise revise your native plan normally. Ask the human if a new decision "
                "is required; never invent their answer.\n\n" + feedback)}
        return {
            "decision": "block",
            "reason": (
                f"Plan saved to {path}. Independent Claude review pass {state['passes']}/{MAX_PASSES} follows. "
                "This is reviewer feedback, not human direction or implementation approval. "
                "Stay in Plan Mode. Incorporate or explain why you disagree. "
                f"Preserve <!-- plan-slug: {slug} --> in every revised plan or context reply. "
                "If the reviewer requests missing context, do not revise yet. "
                "Respond with a complete <plan_review_context>...</plan_review_context> block "
                "containing relevant user decisions, constraints and author reasoning. "
                "Use that block for reasoned pushback too. Otherwise return the complete revised "
                "<proposed_plan>...</proposed_plan>. If a new human decision is required, ask the "
                "user instead and stop; never invent their answer.\n\n" + feedback
            ),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["hook"])
    parser.parse_args()
    try:
        result = process(json.load(sys.stdin))
    except Exception as error:
        result = warning(f"Plan workflow failed ({type(error).__name__}); inspect the local hook. No approval recorded.")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
