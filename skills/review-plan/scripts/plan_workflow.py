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
from datetime import datetime, timezone
from uuid import uuid4

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
    temporary.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    temporary.replace(path)


def extract(pattern, text):
    matches = pattern.findall(text or "")
    return matches[0].strip() if len(matches) == 1 and matches[0].strip() else None


def reviewer_command(plan, context, session, rubric=None, scope=None):
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
        "You are the independent reviewer subprocess in the automatic plan-review workflow. "
        "The parent owns evidence persistence and user-facing status reporting. "
        f"Read the review rubric at {SKILL / 'SKILL.md'} in full. "
        f"Review the implementation plan at {plan}. "
        "The plan file is the current complete proposal. Inspect relevant repository files "
        "read-only. Do not launch another reviewer, execute commands, edit files or implement. "
        "Return the rubric's exact verdict contract. Approval means plan quality only, "
        "never authorisation to implement. If essential context is unavailable, request it."
    )
    if context:
        prompt += f" Before reassessing, read the author's response at {context}."
    if scope:
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
        binary, "-p", prompt, "--model", "claude-opus-5", "--effort", "low",
        "--safe-mode", "--restricted", "--disable-slash-commands",
        "--tools", "Read,Glob,Grep", "--allowedTools", "Read,Glob,Grep",
        "--permission-mode", "plan", "--permission-prompts", "none",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--output-format", "json", "--add-dir", str(SKILL),
    ]
    if session:
        command.extend(["--resume", session])
    if scope:
        command.extend(["--add-dir", str(Path(scope).parent), "--add-dir", str(Path(rubric).parent), "--add-dir", str(Path(plan).parent)])
    return command


def review(repo, plan, context, session, rubric=None, scope=None):
    result = subprocess.run(
        reviewer_command(plan, context, session, rubric, scope), cwd=repo,
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


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evidence_matches(state, path, approved=False, native_path=None):
    expected = state.get("approved_plan_sha256" if approved else "submitted_plan_sha256")
    if state.get("schema_version") != 2 or not expected:
        return False
    try:
        paths = [path, state["snapshot_path"]]
        if native_path:
            paths.append(native_path)
        return all(file_hash(item) == expected for item in paths) and Path(state["feedback_path"]).is_file()
    except (OSError, KeyError):
        return False


def evidence_message(state, path, state_file):
    lines = [f"Plan: [{path.name}]({path})", f"State: [review.json]({state_file})"]
    for key, label in (("snapshot_path", "Reviewed snapshot"), ("feedback_path", "Review output")):
        if state.get(key) and Path(state[key]).is_file():
            lines.append(f"{label}: [{Path(state[key]).name}]({state[key]})")
    if state.get("submitted_plan_sha256"):
        lines.append(f"SHA-256: {state['submitted_plan_sha256']}")
    if state.get("reviewer_session"):
        lines.append(f"Reviewer session: {state['reviewer_session']}")
    return "\n".join(lines)


def approved_response(message, native):
    if native:
        return {"review_approved": True, "systemMessage": message}
    return {"decision": "block", "reason": (
        message + "\nReport this completed review to the human with its verdict and evidence links. "
        "Do not emit another proposal or launch another review. Await explicit human implementation approval."
    )}


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


def process_plan(repo, slug, text, plan, context, turn, skill, run_review, reviewer_name="Claude", native=False, native_path=None):
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
        if state.get("status") in ("failed", "paused", "stale"):
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
            exported = (plan if native else plan + "\n").encode("utf-8")
            atomic_write(path, exported)
            if path.read_bytes() != exported or (native_path and Path(native_path).read_bytes() != exported):
                return warning(f"Plan export read-back mismatch: {path}. No review started.")
            # Legacy Claude read_text() normalised newlines; retain that hash's
            # meaning while the submitted/approved hashes cover exact bytes.
            legacy_text = plan.replace("\r\n", "\n").replace("\r", "\n") if native else plan
            plan_hash = digest(legacy_text)
            if state.get("plan_hash") == plan_hash and state.get("status") == "reviewed":
                if not evidence_matches(state, path, approved=True, native_path=native_path):
                    return warning(f"Historical/unverified approval; no pass consumed. Submit a revised proposal for review.\n{evidence_message(state, path, state_file)}")
                return approved_response(f"Unchanged reviewed plan. Current file matches reviewed snapshot. Await explicit human approval.\n{evidence_message(state, path, state_file)}", native)
            if state.get("plan_hash") == plan_hash and state.get("status") == "changes_needed":
                return warning("Plan unchanged: revise it or provide <plan_review_context>pushback/context</plan_review_context>.")
            state["plan_hash"] = plan_hash
        path = repo / ".agents" / "plans" / state["plan_name"]
        if context and not evidence_matches(state, path, native_path=native_path):
            return warning(f"Context review refused: proposal evidence is missing or stale. Submit the revised plan.\n{evidence_message(state, path, state_file)}")
        context_path = None
        state["last_turn"] = turn
        state["last_output_hash"] = output_hash
        if state["passes"] >= MAX_PASSES:
            state["status"] = "paused"
            atomic_write(state_file, json.dumps(state, indent=2) + "\n")
            return warning(f"Three review passes used. Plan: {path}. Ask the human; do not implement.")
        state["passes"] += 1
        attempt_dir = state_dir / f"pass-{state['passes']}-{uuid4().hex}"
        attempt_dir.mkdir()
        snapshot = attempt_dir / "plan.md"
        submitted = path.read_bytes()
        with snapshot.open("xb") as stream:
            stream.write(submitted)
        if snapshot.read_bytes() != submitted:
            return warning(f"Snapshot read-back mismatch: {snapshot}. No review started.")
        if context:
            context_path = attempt_dir / "author-context.md"
            atomic_write(context_path, context + "\n")
        state.pop("approved_plan_sha256", None)
        state.update(schema_version=2, submitted_plan_sha256=hashlib.sha256(submitted).hexdigest(),
                     snapshot_path=str(snapshot), feedback_path=str(attempt_dir / "review.md"))
        attempt = {"plan_path": str(path), "snapshot_path": str(snapshot),
                   "submitted_plan_sha256": state["submitted_plan_sha256"],
                   "pass": state["passes"], "budget": len(state.get("budget_resets", [])) + 1,
                   "reviewer": reviewer_name, "reviewer_session": state.get("reviewer_session"),
                   "context_path": str(context_path) if context_path else None,
                   "feedback_path": state["feedback_path"],
                   "started_at": datetime.now(timezone.utc).isoformat(), "outcome": "reviewing"}
        state["status"] = "reviewing"
        atomic_write(attempt_dir / "review.json", json.dumps(attempt, indent=2) + "\n")
        atomic_write(state_file, json.dumps(state, indent=2) + "\n")
        try:
            result, feedback, reviewer_session = run_review(
                repo, snapshot, context_path, state.get("reviewer_session")
            )
            if state.get("reviewer_session") and reviewer_session != state["reviewer_session"]:
                raise RuntimeError("Reviewer resumed a different session.")
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
            state["status"] = "failed"
            attempt.update(outcome="failed", error=str(error), completed_at=datetime.now(timezone.utc).isoformat())
            atomic_write(attempt_dir / "review.json", json.dumps(attempt, indent=2) + "\n")
            atomic_write(state_file, json.dumps(state, indent=2) + "\n")
            return warning(f"{reviewer_name} review pass {state['passes']}/{MAX_PASSES} failed: {error}. Do not implement.\n{evidence_message(state, path, state_file)}")
        state["reviewer_session"] = reviewer_session
        atomic_write(Path(state["feedback_path"]), feedback + "\n")
        attempt.update(verdict=result, reviewer_session=reviewer_session, completed_at=datetime.now(timezone.utc).isoformat())
        if not evidence_matches(state, path, native_path=native_path):
            state["status"] = "stale"
            attempt["outcome"] = "stale"
            atomic_write(attempt_dir / "review.json", json.dumps(attempt, indent=2) + "\n")
            atomic_write(state_file, json.dumps(state, indent=2) + "\n")
            return warning(f"{reviewer_name} returned {result}, but review input changed or disappeared. No current approval.\n{evidence_message(state, path, state_file)}")
        state["status"] = "reviewed" if result == "LGTM" else "changes_needed"
        attempt["outcome"] = state["status"]
        if result == "LGTM":
            state["approved_plan_sha256"] = state["submitted_plan_sha256"]
        atomic_write(attempt_dir / "review.json", json.dumps(attempt, indent=2) + "\n")
        atomic_write(state_dir / "latest-review.md", feedback + "\n")
        if result != "LGTM" and state["passes"] >= MAX_PASSES:
            state["status"] = "paused"
        atomic_write(state_file, json.dumps(state, indent=2) + "\n")
        feedback = f"{feedback}\n\nCurrent file matches reviewed snapshot.\n{evidence_message(state, path, state_file)}"
        if result == "LGTM":
            return approved_response(f"{reviewer_name} review pass {state['passes']}/{MAX_PASSES}: LGTM. Plan: {path}. Await explicit human implementation approval.\n{feedback}", native)
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
