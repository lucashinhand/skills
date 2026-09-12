# Codex plan export and review

The repo-local Stop hook runs scripts/plan_workflow.py hook using Python 3.
It persists complete native Plan Mode proposals to .agents/plans/<title>-<session-key>.md.
One active plan per Codex session; revisions retain the same absolute path.
The hook owns all writes and reviewer bookkeeping; the planning agent remains read-only.

The hook invokes the subscription-authenticated Claude CLI (claude-opus-5, low)
with only Read, Glob and Grep, safe/restricted mode, no MCP and no project hooks.
This initial integration reviews local repository evidence only. If GitHub context
is essential and absent from the plan, request it; never silently treat it as checked.

CHANGES NEEDED continues the parent in Plan Mode; LGTM ends the loop and waits
for explicit human implementation approval. Context requests and reasoned pushback
use a complete <plan_review_context>...</plan_review_context> response, without
changing the plan. New human decisions must be asked, not invented. All reviewer
responses count towards the three-pass limit. Errors and exhaustion stop, not retry.

State and latest feedback live in the physical installed skill's .state/ directory.
Reinstallation may wipe this disposable state. Paused/failed sessions require human
attention; this initial slice provides no automatic reset or fallback.
Plans remain available throughout implementation and PR review. The later ship
integration must delete only its own plan after both final approvals cover the
current head; it is intentionally not implemented in this plumbing-only slice.

Setup: install the skill using npx skills, register the repo-local Stop hook,
then start a fresh Codex session and review/trust the exact hook in /hooks.
Do not bypass hook trust. No user-level hook is required.
The hook assumes Stop supplies complete assistant text; absent/malformed output
produces a visible warning rather than guessing from unrelated session history.

TODO: supply an explicit setup command for repo hook wiring after the migration.
