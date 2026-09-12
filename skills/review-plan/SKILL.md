---
name: review-plan
description: Review implementation plans from Claude Code or another coding assistant against the actual repository and relevant GitHub issues. Use when asked to review, re-review, approve, or drive an implementation plan toward LGTM, including follow-up turns containing a revised plan, assistant pushback, requested context, or human comments.
---

# Review Plans

## Objective

Review the proposed plan rather than restating it. Ground every material judgment in the current codebase, relevant GitHub issues, and the conversation context available in this review thread.

Default to `LGTM`. Withhold it only for glaring gaps, genuine errors, or missed scope that should be resolved before implementation.

Address the entire response directly to the coding assistant so the user can copy and paste it without editing or removing surrounding commentary.

## Resolve the Review Context

1. Resolve the repository from the supplied plan, explicit user request or current working directory; do not assume a particular project.
2. Use another repository only when the user names it or the supplied material unmistakably concerns another repository. Do not spend time generalizing this choice unnecessarily.
3. Read the latest plan plus prior review turns, assistant pushback, requested context, and human comments in the current conversation.
4. Resolve relevant GitHub issues in this order:
   - Use issue URLs or numbers supplied by the user or plan.
   - Infer issue references from branch names, ticket identifiers, named features, acceptance criteria, and plan language.
   - Perform a narrow GitHub issue search when a likely issue can be identified.
   - Continue with codebase-grounded review when no issue is discoverable; do not invent an issue association.
5. Use local read-only file tools and, when available, the connected GitHub MCP/app read-only. Inspect the smallest useful set of repository files, call sites, tests, configuration, schemas, and established patterns needed to verify the plan's claims. Never mutate the repository, issues, or pull requests during plan review.
6. Cite decisive evidence inline with repository paths, symbols, or issue numbers. Distinguish verified facts from inferences.

## Apply the Review Standard

Return `Verdict: LGTM` when the plan is implementable, technically coherent, consistent with the codebase, and covers the relevant issue scope well enough to begin.

Return `Verdict: CHANGES NEEDED` only for material concerns such as:

- incorrect assumptions about current code or architecture;
- missed acceptance criteria or meaningful issue scope;
- a plan that cannot work as described;
- omitted integration, migration, compatibility, security, data, or testing work likely to make the implementation incorrect or incomplete;
- unresolved ambiguity that materially changes the implementation approach.

Do not block LGTM for optional refinements, stylistic preferences, generic best practices, speculative edge cases, or improvements that can safely be handled during implementation.

Do not provide a playback of the plan or an inventory of everything inspected. Include only the verdict and useful recommendations. Make every recommendation:

- specific and actionable;
- supported by evidence;
- explicit about the consequence if ignored;
- directed at changing the plan, explaining a choice, or supplying missing context.

Prefer one to five high-signal recommendations. Include more only when independently material.

## Request Missing Coding-Assistant Context

Request context from the coding assistant when the plan, repository, and GitHub issues leave material uncertainty and relevant information may exist in its session history. Account for unknown unknowns: do not limit the request to only the gaps already identified by the reviewer.

Do not ask it for information that can be retrieved from GitHub or the codebase. Request a broad but high-signal context packet summarizing the relevant coding-session history, including:

- an ad hoc user request not captured in an issue;
- constraints or acceptance criteria established earlier in the coding session;
- decisions, deliberate tradeoffs, and rejected approaches;
- what the human asked the assistant to preserve or exclude;
- assumptions, unresolved questions, and known risks;
- any other session context that could materially change the review, even when the reviewer did not ask about it explicitly.

Keep this branch copy-pasteable. Use `Verdict: CHANGES NEEDED` and state that the assistant should not revise the plan yet. Ask for a structured summary rather than a raw conversation transcript, while permitting short exact excerpts when wording matters. Tell it to include the relevant facts, human decisions, and its reasoning without padding. Reassess the verdict when the user supplies its response.

## Continue Until LGTM

Treat follow-up plans, assistant explanations, assistant pushback, and human responses as the next review iteration.

- Re-evaluate outstanding recommendations against the new evidence.
- Accept sound pushback and drop the recommendation.
- Preserve a recommendation when the response does not resolve its material consequence, and explain the remaining gap concisely.
- Treat human product or scope decisions as authoritative while still checking factual claims against the codebase.
- Do not repeat resolved findings, replay the revised plan, or introduce new standards without new evidence.
- Do not move the goalposts. Return `Verdict: LGTM` as soon as no material blocker remains.

## Output Contract

Begin every `CHANGES NEEDED` response with this exact text:

```text
Here is a review of the plan. Incorporate or push back. You have the power to push back if a recommendation is incorrect or misguided, but you must explain why.
```

For approval, use:

```text
Verdict: LGTM

[One or two concise sentences explaining why no material gap remains.]
```

For changes, use:

```text
Here is a review of the plan. Incorporate or push back. You have the power to push back if a recommendation is incorrect or misguided, but you must explain why.

Verdict: CHANGES NEEDED

1. **[Short finding]** — [Evidence, material consequence, and the specific revision or explanation required.]
```

For missing coding-assistant context, use:

```text
Here is a review of the plan. Incorporate or push back. You have the power to push back if a recommendation is incorrect or misguided, but you must explain why.

Verdict: CHANGES NEEDED

Before revising the plan, provide a high-signal context packet from this coding session:

1. Summarize the user's relevant requests and any chat history not represented in the plan or linked issues.
2. List relevant constraints, decisions, tradeoffs, rejected approaches, assumptions, unresolved questions, and known risks.
3. Include any other session context that could materially change this review, even if it does not answer an explicit question above.

Return a structured summary of the relevant human decisions, facts, and your reasoning. Use short exact excerpts only where the wording matters; do not provide a raw full conversation transcript.
```

Do not add a user-facing preface, closing note, sources section, review-process summary, or text outside this copy-pasteable response.

## Codex plan persistence

Automatic plan proposals carry a stable `<!-- plan-slug: descriptive-unique-slug -->`
marker inside the proposal. Preserve it across revisions and title changes; include
it in context replies too. A new plan gets a new slug, without any /pickup dependency.
The hook exports `.agents/plans/<slug>.md`; its review session and pass budget belong
to that repository/slug, not the author's CLI session. This marker is not required
for ordinary standalone manual reviews of an existing supplied plan.

When setting up or diagnosing automatic plan export and review, read [references/codex-workflow.md](references/codex-workflow.md). Ordinary standalone reviews use only the rubric above; never recursively launch a reviewer.
