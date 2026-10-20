#!/usr/bin/env python3
"""Adapt Claude's native plan-ready event to the shared bounded review loop."""
import json
from pathlib import Path
import sys

import codex_review
import plan_workflow as workflow


def review(repo, plan, context, session):
    return codex_review.review(repo, plan, workflow.SKILL / "SKILL.md", context, session)


def transcript_messages(event):
    # Resolve only this author's exact transcript, never list the native plans directory.
    rows = [json.loads(line) for line in Path(event["transcript_path"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    messages = [row for row in rows if row.get("type") in ("assistant", "user")]
    if not messages or any(row.get("sessionId") != event["session_id"] for row in messages):
        raise ValueError("Claude transcript session mismatch.")
    return messages


PLANS_DIR = Path.home() / ".claude" / "plans"


def is_plan_file(path):
    candidate = Path(path).resolve()
    # gemini-resolved(B, threads=PRRT_kwDOPfMmP86s9rCL): native paths are absolute; a ~ path fails loudly at process()'s guard
    return candidate.suffix == ".md" and candidate.parent == PLANS_DIR.resolve()


def tool_uses(messages):
    for row in reversed(messages):
        for part in reversed(row.get("message", {}).get("content", []) or []):
            if isinstance(part, dict) and part.get("type") == "tool_use":
                yield part


def plan_path(messages):
    # Newest evidence wins. The live event's own path already takes precedence in
    # process(), so an older ExitPlanMode here is history: a plan written after it
    # supersedes it, or that superseding plan would go unreviewed behind its approval.
    # A blocked write still appears as a tool use, so only successful ones count.
    failed = {part.get("tool_use_id") for row in messages
              for part in row.get("message", {}).get("content", []) or []
              if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("is_error")}
    for part in tool_uses(messages):
        if part.get("id") in failed:
            continue
        # gemini-resolved(B, threads=PRRT_kwDOPfMmP86s9rCX): a tool_use input is always a dict; a null one must fail loudly
        if part.get("name") == "ExitPlanMode":
            supplied = part.get("input", {}).get("planFilePath")
            if supplied:
                return supplied
        elif part.get("name") in ("Write", "Edit"):
            candidate = part.get("input", {}).get("file_path")
            if candidate and is_plan_file(candidate):
                return candidate
    return None


def context_input(event):
    messages = transcript_messages(event)
    last = messages[-1]
    if last["type"] != "assistant":
        return None
    text = "\n".join(part.get("text", "") for part in last["message"]["content"] if part.get("type") == "text")
    context = workflow.extract(workflow.CONTEXT, text)
    if context is None:
        return None
    path = plan_path(messages)
    if not path:
        raise ValueError("No native plan path in this author's transcript.")
    return path, context, last["uuid"]


def process(event, skill=workflow.SKILL, run_review=review):
    kind = event.get("hook_event_name")
    if kind not in ("PreToolUse", "Stop") or event.get("agent_id"):
        return {}
    if kind == "PreToolUse" and event.get("tool_name") != "ExitPlanMode":
        return {}
    if event.get("permission_mode") != "plan":
        return {}
    context = None
    if kind == "Stop":
        resolved = context_input(event)
        if resolved is None:
            return {}
        filename, context, turn = resolved
    else:
        # gemini-resolved(B, threads=PRRT_kwDOPfMmP86s9rCd): the hook event's tool_input is always a dict; a null one must fail loudly
        filename = event.get("tool_input", {}).get("planFilePath")
        if not filename and event.get("transcript_path"):
            filename = plan_path(transcript_messages(event))
        turn = event["tool_use_id"]
    if not isinstance(filename, str) or not Path(filename).is_absolute():
        raise ValueError("Claude did not supply an absolute native plan path.")
    native_path = Path(filename)
    if native_path.suffix != ".md" or not native_path.stem:
        raise ValueError("Expected a native Markdown plan file.")
    plan = native_path.read_bytes().decode("utf-8")
    if not plan.strip():
        raise ValueError("Native plan file is empty.")
    repo = Path(event["cwd"]).resolve()
    if context and (repo / ".agents" / "plans" / native_path.name).read_bytes() != plan.encode("utf-8"):
        raise ValueError("Native plan changed during a context-only reply; submit the revised plan for review.")
    result = workflow.process_plan(repo, native_path.stem, context or plan,
                                   None if context else plan, context, turn,
                                   skill, run_review, reviewer_name="Codex", native=True, native_path=native_path)
    approved = result.pop("review_approved", False)
    if kind == "Stop":
        return result
    message = result.get("reason") or result.get("stopReason") or result.get("systemMessage", "Plan review requires attention.")
    # Even LGTM asks the human; a reviewer can never grant tool permission.
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
              "permissionDecision": "ask" if approved else "deny",
              "permissionDecisionReason": message}}
    if result.get("continue") is False:
        output.update({"continue": False, "stopReason": message})
    return output


if __name__ == "__main__":
    try:
        print(json.dumps(process(json.load(sys.stdin))))
    except Exception as error:
        # Exit 2 blocks PreToolUse/Stop even if native input or reviewer output changed.
        print(f"Claude plan review paused ({type(error).__name__}): {error}. No approval recorded.", file=sys.stderr)
        raise SystemExit(2)
