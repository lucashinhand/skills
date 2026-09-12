#!/usr/bin/env python3
"""Review a clean committed implementation against an explicit approved plan."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

SKILL = Path(__file__).resolve().parent.parent
# Both skills are installed together; resolve siblings from the physical install.
sys.path.insert(0, str(SKILL.parent / "review-plan" / "scripts"))
import codex_review
import plan_workflow


def git(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    return result.stdout


def commit(repo, ref):
    return git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


def assert_checkout(repo, head):
    if commit(repo, "HEAD") != head:
        raise RuntimeError("Checkout HEAD differs from the reviewed head; review paused.")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("Pre-PR review requires a clean checkout, including untracked files. Commit intended changes first.")


def transport(reviewer, repo, plan, scope, context, session):
    rubric = SKILL / "SKILL.md"
    if reviewer == "claude":
        return plan_workflow.review(repo, plan, context, session, rubric=rubric, scope=scope)
    return codex_review.review(repo, plan, rubric, context=context, session=session, scope=scope)


def run(repo, plan, base, head, author, context=None, session=None, state_root=None, run_review=transport):
    repo, plan = repo.resolve(), plan.resolve(strict=True)
    if not plan.read_text().strip():
        raise ValueError("Approved plan is empty.")
    if author not in ("claude", "codex"):
        raise ValueError("Author must be claude or codex.")
    head_sha, base_sha = commit(repo, head), commit(repo, base)
    assert_checkout(repo, head_sha)
    merge_base = git(repo, "merge-base", base_sha, head_sha).decode().strip()
    diff = git(repo, "diff", "--binary", "--no-ext-diff", "--no-textconv", "--submodule=diff", merge_base, head_sha, "--")
    if not diff:
        raise RuntimeError("The supplied branch scope has no changes to review.")
    reviewer = "claude" if author == "codex" else "codex"
    root = state_root or SKILL / ".state"
    artefacts = root / uuid.uuid4().hex
    artefacts.mkdir(parents=True)
    plan_bytes = plan.read_bytes()
    snapshot = artefacts / "approved-plan.md"
    snapshot.write_bytes(plan_bytes)
    diff_path = artefacts / "implementation.diff"
    diff_path.write_bytes(diff)
    scope = {"repo": str(repo), "base": base_sha, "merge_base": merge_base,
             "head": head_sha, "approved_plan": str(plan),
             "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
             "diff": str(diff_path), "reviewer": reviewer}
    scope_path = artefacts / "scope.json"
    scope_path.write_text(json.dumps(scope, indent=2) + "\n")
    try:
        verdict, feedback, reviewer_session = run_review(reviewer, repo, snapshot, scope_path, context, session)
        assert_checkout(repo, head_sha)
        if plan.read_bytes() != plan_bytes:
            raise RuntimeError("Approved plan changed during review; result is stale.")
        if verdict not in ("LGTM", "CHANGES NEEDED") or not reviewer_session:
            raise RuntimeError("Reviewer returned an invalid verdict/session.")
        if session and reviewer_session != session:
            raise RuntimeError("Reviewer resumed a different session.")
        result = {**scope, "status": "reviewed", "verdict": verdict, "feedback": feedback,
                  "session_id": reviewer_session, "artefacts": str(artefacts)}
    except Exception as error:
        result = {**scope, "status": "failed", "error": str(error)}
        (artefacts / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        raise
    (artefacts / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--author", choices=("claude", "codex"), required=True)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--resume")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.repo, args.plan, args.base, args.head, args.author,
                             args.context.resolve(strict=True) if args.context else None, args.resume)))
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        raise SystemExit(1)
