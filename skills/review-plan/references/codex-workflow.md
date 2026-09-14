# Codex plan export and review

The repo-local Stop hook runs scripts/plan_workflow.py hook using Python 3.
It persists complete native Plan Mode proposals to .agents/plans/<slug>.md.
Every proposal includes one <!-- plan-slug: descriptive-unique-slug --> marker.
Keep that marker across revisions, title changes and context replies. A genuinely
different plan gets a new unique slug, independently of /pickup or author session.
Review state is keyed by repository and slug, so revisions reuse the reviewer and
three-pass budget even when the author session changes. Do not change the slug
to evade the review limit. Slugs use lowercase letters, digits and single hyphens,
at most 80 characters. Missing or ambiguous markers pause visibly.
The hook owns all writes and reviewer bookkeeping; the planning agent remains read-only.

The hook invokes the subscription-authenticated Claude CLI (claude-opus-5, low)
with only Read, Glob and Grep, safe/restricted mode, no MCP and no project hooks.
This initial integration reviews local repository evidence only. If GitHub context
is essential and absent from the plan, request it; never silently treat it as checked.

CHANGES NEEDED continues the parent in Plan Mode; LGTM continues it once to report
the result and evidence links, without another proposal or review, then waits
for explicit human implementation approval. A normal status reply does not
trigger the exporter. Context requests and reasoned pushback
use a complete <plan_review_context>...</plan_review_context> response, without
changing the plan. New human decisions must be asked, not invented. All reviewer
responses count towards the three-pass limit. Errors and exhaustion stop, not retry.

State and latest feedback live in the physical installed skill's .state/ directory.
Each attempt also retains a unique pass directory containing the exact submitted
plan bytes, optional author context, feedback and a timestamped review record.
The reviewer reads that snapshot, and completion verifies it against the canonical
file before accepting LGTM. Attempt directories remain distinct after authorised
budget resets. The output links the plan, snapshot, feedback and state and names
the actual reviewer/session; the author surfaces that result to the human.

Schema version 2 records `submitted_plan_sha256` and `approved_plan_sha256` over
saved bytes. `last_output_hash` remains the text hash for duplicate deliveries;
legacy `plan_hash` retains its original extracted-text meaning. An unchanged
legacy approval is historical/unverified and consumes no new pass. A revised
proposal enters the normal bounded loop; do not reset budgets to migrate state.
For explicit re-review of an unchanged legacy proposal, the human must authorise
clearing its old reviewed status to require a fresh pass, retaining its counters
and session. No manual review command or automatic migration is introduced.

Codex exports extracted text with its existing final-newline convention. Native
Claude exports copy the original bytes exactly, even CRLF or no final newline.
Neither context continuations nor approval checks normalise native bytes.
Missing/changed snapshots, feedback or plan bytes cannot reuse an approval.
Before implementation, the author checks current bytes against the recorded
approved hash and snapshot as well as obtaining human implementation approval.

Before a file-backed invocation the author checks that its latest conversational
proposal is saved. The reviewer itself cannot see unsaved author history. A
behavioural check is: give the author a saved proposal A and a newer proposal B
in conversation, then request review; it must report “Latest proposal is not
saved” without launching a reviewer or changing files during read-only review.
Reinstallation may wipe this disposable state. Paused/failed sessions require human
attention; this initial slice provides no automatic reset or fallback.
Plans remain available throughout implementation and PR review. The later ship
integration must delete only its own plan after both final approvals cover the
current head; it is intentionally not implemented in this plumbing-only slice.

Setup: install the skill using npx skills, register the repo-local Stop hook,
then start a fresh Codex session and review/trust the exact hook in /hooks.
Do not bypass hook trust. No user-level hook is required.
If installing into an already-running CLI, exit and resume that conversation
before testing: an active/trusted entry in /hooks alone does not prove the
running session dispatches it. Verify an actual plan export and reviewer result.
For Codex 0.154, the hook reads the exact Stop transcript_path and verifies its
session, turn, working directory and collaboration mode before exporting the
completed Plan item. Permission mode is not collaboration mode. Transcript formats
are version-sensitive; mismatches pause visibly, never search other sessions.
Direct Plan Mode payloads with complete assistant text are also supported.

TODO: supply an explicit setup command for repo hook wiring after the migration.

Validation on 14 September 2026 replayed an actual Codex Stop transcript through
the updated workflow with isolated state and a live Claude reviewer. The result
was LGTM with matching canonical/snapshot hashes, session identity and evidence
links. A separate live author-behaviour check refused an unsaved newer proposal
without launching a reviewer. Unit tests cover the LGTM reporting continuation
and its subsequent no-op status reply; visual rendering of the in-app progress
indicator still needs confirmation on the next interactive plan submission.

## Codex reviewer transport

`scripts/codex_review.py --repo <repo> --plan <absolute-plan-path>` runs an
independent Codex Astra/low review through the ChatGPT subscription. Add
`--resume <session-id>` for follow-ups and `--context <path>` for author context.
It returns JSON containing verdict, feedback and session_id; errors exit nonzero
with an error field. This adapter is not itself a plan-ready hook.

The runner reapplies read-only sandboxing and never-approve on initial/resumed
runs, ignores user configuration, disables hooks/apps/plugins, and replaces MCP
configuration with an explicit GitHub read-tool allowlist (or no MCP without a
PAT). It never includes credential values in command arguments.

Codex 0.154's exec JSONL does not expose nested code-mode command outcomes. The
adapter therefore verifies the exact reviewer transcript/session and current
turn's cwd/sandbox, and refuses approval after failed tool results. This is
conservative: nonzero command results require attention, even if the model later
prints LGTM. Code mode remains enabled because this CLI requires it for tools.
Transcript/schema mismatches fail visibly. Native initial and resumed reviews
and a harmless sandbox-denied write have been tested; keep these checks when
changing the adapter or CLI version.
