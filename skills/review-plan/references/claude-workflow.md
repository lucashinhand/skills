# Claude native plan review

`scripts/claude_plan.py` accepts Claude hook JSON on stdin. Register it for
PreToolUse matching ExitPlanMode and for Stop in the local CLI only. It reads
the plan path from the live event's `planFilePath`, else recovers the newest
evidence in the author's own session transcript: an earlier ExitPlanMode path,
or a successful `Write`/`Edit` of a file directly in `~/.claude/plans`,
whichever came last. A plan written after an earlier ExitPlanMode supersedes it,
or that newer plan would ship behind the older one's approval. It then uses the
resolved filename stem as plan identity and
exports an unchanged copy to `.agents/plans/<native-filename>.md`. The native
file stays untouched. No plan marker, custom naming or plan-directory scan.
Export and context checks preserve bytes, including CRLF and absent final
newlines. Each pass reviews a separate snapshot; approval requires the snapshot,
exported copy and native source still to match the submitted SHA-256. Evidence
and progress reporting follow [the shared workflow](codex-workflow.md).

The shared loop runs Codex Astra/medium with subscription authentication and an
enforced read-only tool surface. Revisions reuse the reviewer and three-pass
budget keyed by repository and filename. Skill-local `.state` is disposable;
the exported plan remains the implementation/ship handoff after reinstall.

CHANGES NEEDED denies ExitPlanMode and returns feedback. Context or reasoned
pushback uses `<plan_review_context>...</plan_review_context>` in an assistant
response without revising the native plan. Stop resolves the exact plan path
from this author's transcript and resumes the reviewer. New human decisions
must be asked rather than invented. Errors/exhaustion pause visibly.
LGTM asks for human permission at ExitPlanMode; it never approves implementation.

Claude 2.1.263's interactive CLI was verified to create a native plan and supply
`plan` and, when present, `planFilePath` to PreToolUse without naming/tool-sequence instructions.
The tested `claude -p` planning session created a native file but lacked
ExitPlanMode; it is not an end-to-end substitute for interactive hook testing.

Register both events in the repository's ignored `.claude/settings.local.json`,
not its shared settings: standalone Claude cloud must not depend on local Codex.
Set each hook timeout above the reviewer's 900-second limit (for example 960), or
the CLI kills a slow review before it can record a visible failure.
Use the installed skill path, not a developer's source checkout. Restart/resume
the local CLI after registration and verify the hook is loaded.
The source adapter's native initial-review and human-approval boundary have been
tested interactively. Context continuation also has isolated regression tests.
