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
    if args.command == "resume":
        if state["status"] != "paused" or not args.evidence.strip():
            raise ValueError("Resume requires a paused handoff and acknowledgement evidence.")
        if not state["session_id"] or args.session != state["session_id"]:
            raise ValueError("Resume must retain the recorded owner; reconcile an unknown launch first.")
        return dict(state, status="awaiting-ready", recovery_evidence=args.evidence)
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
    for name in ("bind", "ready", "pause", "complete", "resume", "replace"):
        command = commands.add_parser(name)
        command.add_argument("--handoff", required=True)
        if name in ("bind", "ready", "resume"):
            command.add_argument("--session", required=True)
        if name == "pause":
            command.add_argument("--reason", required=True)
        if name in ("complete", "replace"):
            command.add_argument("--head", required=True)
        if name in ("resume", "replace"):
            command.add_argument("--evidence", required=True)
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
