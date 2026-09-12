#!/usr/bin/env python3
"""Persist one local supervisor handoff per checkout/PR; never launch a CLI."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import time


def monitor(state, args):
    now = time.time()
    progress = args.cursor != state.get("activity_cursor")
    index = 0 if progress else min(state.get("poll_index", -1) + 1, 4)
    updated = dict(state, activity_cursor=args.cursor, poll_index=index,
                   next_check_seconds=(60, 60, 120, 180, 300)[index], action="wait")
    if state["status"] in ("paused", "complete"):
        return dict(updated, action="stop-monitoring")
    if state["status"] in ("launching", "awaiting-ready"):
        age = now - state.get("startup_at", state["created_at"])
        if age >= 600:
            return dict(updated, status="paused", reason="readiness deadline exceeded", action="report-blocker")
        if age >= 300 and not state.get("startup_nudge_sent"):
            action = "send-startup-nudge" if state["session_id"] else "reconcile-launch"
            return dict(updated, action=action)
        return updated
    if progress or not args.work_owed:
        updated = dict(updated, work_owed_since=now if args.work_owed else None,
                       recovery_nudge_sent_at=None)
        return updated
    owed_since = state.get("work_owed_since")
    if owed_since is None:
        return dict(updated, work_owed_since=now)
    nudged = state.get("recovery_nudge_sent_at")
    if nudged is not None and now - nudged >= 300:
        return dict(updated, status="paused", reason="recovery acknowledgement deadline exceeded",
                    action="report-blocker")
    if nudged is None and now - owed_since >= 1800:
        return dict(updated, action="send-recovery-nudge")
    return updated


def change(state, args):
    if args.command == "begin":
        if state:
            raise ValueError("A handoff already exists; reconcile it instead of launching again.")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository):
            raise ValueError("Expected owner/repository.")
        if not re.fullmatch(r"[0-9a-f]{40}", args.head):
            raise ValueError("Expected full head SHA.")
        if not args.branch.startswith(("claude/", "codex/")):
            raise ValueError("Expected an agent feature branch.")
        return dict(repository=args.repository, pr=args.pr, branch=args.branch,
                    head=args.head, plan=args.plan,
                    handoff=f"{args.pr}-{args.head[:8]}-{secrets.token_hex(8)}",
                    status="launching", created_at=time.time(), session_id=None,
                    session_url=None)
    if not state:
        raise ValueError("No handoff exists.")
    if args.command == "read":
        return state
    if args.handoff != state["handoff"]:
        raise ValueError("Stale handoff; refusing to change another owner's record.")
    if args.command == "observe":
        return monitor(state, args)
    if args.command == "nudged":
        if args.kind == "startup" and state.get("action") == "send-startup-nudge":
            return dict(state, startup_nudge_sent=True, action="wait")
        if args.kind == "recovery" and state.get("action") == "send-recovery-nudge":
            return dict(state, recovery_nudge_sent_at=time.time(), action="wait")
        raise ValueError("No matching nudge is pending.")
    if args.command == "acknowledged":
        if state["status"] != "active" or not args.evidence.strip():
            raise ValueError("Acknowledgement requires an active owner and receipt evidence.")
        return dict(state, recovery_nudge_sent_at=None, work_owed_since=time.time(),
                    acknowledgement=args.evidence)
    if args.command == "resume":
        if state["status"] != "paused" or not args.evidence.strip():
            raise ValueError("Resume requires a paused handoff and acknowledgement evidence.")
        if not state["session_id"] or args.session != state["session_id"]:
            raise ValueError("Resume must retain the recorded owner; reconcile an unknown launch first.")
        return dict(state, status="awaiting-ready", recovery_evidence=args.evidence,
                    startup_at=time.time(), startup_nudge_sent=False)
    if args.command == "replace":
        if state["status"] not in ("paused", "complete"):
            raise ValueError("Pause and verify the old owner has stopped before replacement.")
        if not args.previous_owner_stopped or not args.evidence.strip():
            raise ValueError("Replacement requires recorded evidence that the previous owner stopped.")
        if not re.fullmatch(r"[0-9a-f]{40}", args.head):
            raise ValueError("Expected freshly verified full PR head SHA.")
        previous = {key: value for key, value in state.items() if key != "history"}
        return dict(repository=state["repository"], pr=state["pr"], branch=state["branch"],
                    head=args.head, plan=state["plan"],
                    handoff=f"{state['pr']}-{args.head[:8]}-{secrets.token_hex(8)}",
                    status="launching", created_at=time.time(), session_id=None,
                    session_url=None, replacement_evidence=args.evidence,
                    history=[*state.get("history", []), previous])
    if args.command == "bind":
        if state["status"] != "launching" or state["session_id"]:
            raise ValueError("Launch already bound or paused; reconcile the existing owner.")
        if not re.fullmatch(r"session_[A-Za-z0-9]+", args.session):
            raise ValueError("Invalid cloud session ID.")
        return dict(state, session_id=args.session,
                    session_url=f"https://claude.ai/code/{args.session}", status="awaiting-ready")
    if args.command == "ready":
        if state["status"] != "awaiting-ready" or args.session != state["session_id"]:
            raise ValueError("Readiness must match the recorded cloud session.")
        return dict(state, status="active", ready_at=time.time())
    if args.command == "pause":
        if state["status"] == "complete":
            raise ValueError("Handoff already complete.")
        return dict(state, status="paused", reason=args.reason)
    if args.command == "complete":
        if state["status"] != "active":
            raise ValueError("Only an active handoff can complete.")
        if not re.fullmatch(r"[0-9a-f]{40}", args.head):
            raise ValueError("Expected full approved head SHA.")
        return dict(state, status="complete", approved_head=args.head)
    raise ValueError("Unknown operation.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True, type=int)
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("begin")
    for name in ("repository", "branch", "head"):
        begin.add_argument(f"--{name}", required=True)
    begin.add_argument("--plan", help="Exact approved exported plan path, if any")
    commands.add_parser("read")
    for name in ("bind", "ready", "pause", "complete", "resume", "replace", "observe", "nudged", "acknowledged"):
        command = commands.add_parser(name)
        command.add_argument("--handoff", required=True)
        if name in ("bind", "ready", "resume"):
            command.add_argument("--session", required=True)
        if name == "pause":
            command.add_argument("--reason", required=True)
        if name in ("complete", "replace"):
            command.add_argument("--head", required=True)
        if name in ("resume", "replace", "acknowledged"):
            command.add_argument("--evidence", required=True)
        if name == "observe":
            command.add_argument("--cursor", required=True, help="Last meaningful owner progress, not arbitrary PR activity")
            command.add_argument("--work-owed", action="store_true")
        if name == "nudged":
            command.add_argument("--kind", choices=("startup", "recovery"), required=True)
        if name == "replace":
            command.add_argument("--previous-owner-stopped", action="store_true", required=True)
    args = parser.parse_args()
    if args.pr <= 0:
        parser.error("PR must be positive.")
    common = subprocess.check_output(["git", "rev-parse", "--path-format=absolute",
                                      "--git-common-dir"], text=True).strip()
    key = hashlib.sha256(str(Path(common).resolve()).encode()).hexdigest()
    folder = Path(__file__).resolve().parent.parent / ".state" / key
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"supervisor_{args.pr}.json"
    with target.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        updated = change(state, args)
        if args.command != "read":
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
            temporary.replace(target)
        print(json.dumps(updated))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
