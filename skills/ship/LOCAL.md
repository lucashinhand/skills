# Ship — Local Flow

For Codex or Claude CLI without native PR activity subscriptions. Claude cloud
owns fixes and review-thread writes; the local implementer hands the session to
the human and performs final review. This workflow never merges.

## Implementation review

For planned work, invoke the installed `review-impl` runner with the exact
approved plan file and explicit base/head. Claude-authored work uses Codex;
Codex-authored work uses Claude. Keep the plan as the whole review brief.
Fix or explicitly rebut findings. An unavailable or failed reviewer pauses
shipping; never auto-skip. This replaces the codex-companion invocation, while
preserving its adversarial method. Unplanned work skips with an explicit reason;
never reconstruct or select a plan by listing a directory.

Use the existing file-backed plan export. Claude identity is its native filename;
Codex retains its current slug marker. Keep the exact plan path for final review.

## Create or reuse the PR

Verify the project GitHub MCP identity and follow the repository's GitHub rules.
Reuse the PR for the implementation branch or create one using the shared body
templates. Record repository, PR number, head branch and full SHA. Do not create
or mutate the cloud owner's review counters or thread state.

## Launch one owner

Use `python3 <physical-skill-directory>/scripts/supervisor.py --pr <number>`
for the following operations, with the verified checkout as working directory:

- `begin --repository <owner/repo> --branch <branch> --head <sha> [--plan <exact-path>]`
  records launch intent and returns the fresh handoff ID. Do this before launching.
- `bind --handoff <id> --session <session_id>` records the CLI's returned identity.
- `ready --handoff <id> --session <session_id>` only after verifying the matching
  readiness comment, subscription and checkout facts; the script is not evidence
  those external checks happened.
- `read` retrieves the record after interruption; never call `begin` as recovery.
- `pause --handoff <id> --reason <reason>` preserves the known owner on failure.
- `resume --handoff <id> --session <same-session-id> --evidence <acknowledgement>`
  returns a paused, known owner to awaiting readiness. Verify a fresh owner
  acknowledgement and current checkout facts before recording readiness again.
- `replace --handoff <old-id> --head <current-sha> --previous-owner-stopped
  --evidence <termination-proof>` creates a fresh launch record, retaining the
  prior handoff in history. First verify the old owner has stopped and the PR's
  repository and branch are unchanged. A missing heartbeat or CLI send acceptance
  is not termination proof. Recover existing review counters and thread outcomes
  for the replacement owner; replacement never grants a new review budget.
- `complete --handoff <id> --head <sha>` only after both current-head approvals
  and the owner's termination acknowledgement have been verified.

The recorder locks its separate supervisor file for every operation. It never
launches a CLI, edits owner review state or proves a remote approval. Existing
records cannot be overwritten by `begin`, even after pause or completion.
Evidence fields record checks performed by the caller; they do not independently
verify external facts. For an unknown launch, locate the existing session and
confirm it has stopped before replacement. Do not erase records to bypass this.

Mint a fresh handoff ID for this attempt and persist it with the target facts in
supervisor state before launching. Record launch intent before calling the CLI.
Use subscription-authenticated `claude --cloud` from a TTY. The short prompt
identifies the installed ship skill, delegated mode, target repository/PR/head,
handoff ID and authorised fixing scope. Put workflow instructions in the skill,
not a generated wall of prompt text. Do not use `/autofix-pr` or remote Agent.

Capture the returned session ID and URL. A timeout or unparsable result is an
ambiguous launch, not permission to retry: reconcile the existing session first.
Never launch a second potential owner because a readiness comment is missing.

The launch prompt is only a dispatch envelope; for example:

```text
Use the installed ship skill in delegated cloud mode. Fix and shepherd the
existing PR <owner/repo>#<number> on <branch>, expected head <full-sha>.
Handoff: <id>. Follow CLOUD.md, including verified checkout, subscription,
readiness and current-head dual approval. Do not merge.
```

Pass this as one quoted argument to `claude --cloud <prompt>` in a TTY from the
verified checkout. The skill must already be committed and available to the cloud
checkout. Save the exact returned session ID with `bind` before handing over. Do
not pass a model/effort or replace subscription authentication with an API key:
the cloud session takes the repository's pinned settings defaults, never the
launching session's interactive model or effort.

The owner must verify the target checkout, fast-forward to the exact current PR
head, subscribe, initialise its own state and post the handoff-specific readiness
comment with its session URL before fixing. A subscription handshake alone is
not readiness. A cloud-provisioned branch is never an alternative push target.

## Hand over, then step back

Give the human the cloud session URL and stop. Do not schedule recurring checks,
send nudges or run readiness and recovery timers: the human watches the session,
answers its permission prompts and reports back when it is done or stuck. A
silent owner is usually waiting on a prompt only the human can see.

If the human has not returned after thirty minutes, make one read-only GitHub
check. Use the harness's one-shot scheduling, and cancel it as soon as the
human returns or reports Gemini approval. Report whether Gemini has approved
the current head and whether any review threads are unanswered, with the
session URL. Do not message the session from that check. Without one-shot
scheduling, say so rather than claim a check is pending.

When the human reports back or the check finds Gemini approval, verify the
handoff-specific readiness comment and current head, record `ready`, and
continue to final approval. If the owner has stopped or failed, `pause` with
the reason and let the human choose between `resume` and `replace`.

Relay explicit human direction to the existing owner with
`claude -p <message> --cloud <recorded-session-id>`; CLI send acceptance is not
proof of receipt. Do not critique or steer every intermediate fix. Final
objections go to that same owner, who continues fixing. Only the owner pushes,
replies to review threads and updates review counters. An archived session
cannot be resumed; confirm the old owner has stopped before replacing it.

## Final approval

Assess the current diff against the exact approved plan, when one exists, and
the repository's rules. Re-read the PR head immediately before posting
`LGTM — implementer session @ <full-head-SHA>`. A moved head needs fresh review.
The owner terminates only when Gemini and implementer approval cover that head.
After confirmed completion, remove only the associated exported plan and cancel
any pending one-shot check. Never delete Claude's native plan or unrelated plan files.

## Installation requirements

Install `ship`, `review-plan` and `review-impl` together for local use. Standalone
cloud needs only `ship` and the consuming repository's GitHub policy. Before
activation, update repository hook/script paths and narrowly scoped permission
entries to the installed paths, and migrate any active state with verified
snapshots. Do not install over an active owner. State lives under the physical
skill's `.state/<checkout-key>/`; review and supervisor records are separate.
The state helper must not be invoked via an outdated repository-local copy.
Verify the consuming harness's actual scheduling and permissions; this package
does not install permissions or start a background process by itself.
