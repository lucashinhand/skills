#!/usr/bin/env python3
"""Adapt Claude's native plan-ready event to the shared bounded review loop."""
import json
from pathlib import Path
import sys

import codex_review
import plan_workflow as workflow


def review(repo, plan, context, session):
    return codex_review.review(repo, plan, workflow.SKILL / "SKILL.md", context, session)


def context_input(event):
    # Resolve only this author's exact transcript, never list the native plans directory.
    rows = [json.loads(line) for line in Path(event["transcript_path"]).read_text().splitlines() if line.strip()]
    messages = [row for row in rows if row.get("type") in ("assistant", "user")]
    if not messages or any(row.get("sessionId") != event["session_id"] for row in messages):
        raise ValueError("Claude transcript session mismatch.")
    last = messages[-1]
    if last["type"] != "assistant":
        return None
    text = "\n".join(part.get("text", "") for part in last["message"]["content"] if part.get("type") == "text")
    context = workflow.extract(workflow.CONTEXT, text)
    if context is None:
        return None
    paths = [part.get("input", {}).get("planFilePath") for row in messages
             for part in row.get("message", {}).get("content", []) if isinstance(part, dict)
             and part.get("type") == "tool_use" and part.get("name") == "ExitPlanMode"]
    if not paths or not paths[-1]:
        raise ValueError("No native plan path in this author's transcript.")
    return paths[-1], context, last["uuid"]


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
        filename = event.get("tool_input", {}).get("planFilePath")
        turn = event["tool_use_id"]
    if not isinstance(filename, str) or not Path(filename).is_absolute():
        raise ValueError("Claude did not supply an absolute native plan path.")
    native_path = Path(filename)
    if native_path.suffix != ".md" or not native_path.stem:
        raise ValueError("Expected a native Markdown plan file.")
    plan = native_path.read_text()
    if not plan.strip():
        raise ValueError("Native plan file is empty.")
    repo = Path(event["cwd"]).resolve()
    if context and (repo / ".agents" / "plans" / native_path.name).read_text() != plan:
        raise ValueError("Native plan changed during a context-only reply; submit the revised plan for review.")
    result = workflow.process_plan(repo, native_path.stem, context or plan,
                                   None if context else plan, context, turn,
                                   skill, run_review, reviewer_name="Codex", native=True)
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
