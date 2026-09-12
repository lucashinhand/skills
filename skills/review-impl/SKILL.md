---
name: review-impl
description: Reviews implementation changes against an approved plan and explicit base/head scope. Use when asked for an independent implementation review or the planned-work pre-PR gate, not for plan review or PR lifecycle ownership.
---

# Review Implementation

Challenge whether the actual implementation is ready to ship, using its approved
plan as the statement of intent. Test the chosen approach and its assumptions,
not just whether the code follows the plan. Report material problems, not style.

## Inputs and scope

Read the supplied approved plan file in full and the complete implementation
diff for the explicit base/head scope. Resolve relevant repository instructions,
call sites, tests and issue references read-only. The plan and diff are the brief;
do not require a pasted plan, conversation dump or author-selected risk areas.

If the plan or diff scope is missing or ambiguous, request it. Never select the
latest plan from a directory, reconstruct a plan, or silently omit changed files.
Distinguish the reviewed commit range from uncommitted changes; never claim a
result covers changes outside the supplied scope.
For branch review, inspect changes from the base/head merge-base to the supplied
head. For an explicitly requested working-tree review, include staged, unstaged
and untracked work. A changed-file list or diff summary is navigation, not review
evidence: inspect the actual changes before reaching a verdict.

## Adversarial method

Try to find a realistic counterexample to the claim that this change is ready.
Trace relevant inputs and state transitions through the implementation and its
callers. Check whether the design still holds when operations fail part-way,
repeat, overlap or observe stale state—not only on the intended success path.

Choose failure scenarios from the code, not a mandatory checklist. Relevant
examples include crossing an authorisation boundary, duplicating a state change
on retry, losing data during recovery, or breaking compatibility during rollout.
Challenge missing invariants, unsupported assumptions and consequential design
trade-offs. A promised future fix is not evidence that today's change is safe.

Plan approval establishes intended scope; it does not prove the proposed approach
correct. Report a demonstrable flaw even when the implementation follows the plan
exactly, explaining which assumption fails. Respect explicit human constraints;
do not reopen settled preferences without new evidence of a material consequence.
If attempts to find a substantive failure do not hold up, return LGTM without
manufacturing findings to satisfy the adversarial framing.

## Review standard

Return `Verdict: LGTM` when no material implementation gap remains. Withhold it
for concrete defects, unsafe behaviour, unmet plan requirements or missing
verification that materially undermines correctness.

For each finding, provide:

- File and line evidence, with the relevant execution path or requirement.
- The condition under which it fails and the practical consequence.
- An actionable correction or a precise request for missing author context.

Prefer a few high-signal findings. Exclude speculative hardening, style nits and
unrelated refactoring. Distinguish verified defects from uncertain assumptions.
If evidence is available in the repo, inspect it rather than asking the author
to reproduce it. If material context exists only in the authoring conversation,
request the relevant decisions and reasoning before prescribing a change.

## Read-only and authority boundaries

Do not edit files, execute mutating commands, post GitHub comments, reply to review
threads, push, merge or launch another reviewer. Use the supplied read-only tools.
Unavailable required tools or incomplete evidence must be reported; never treat
an execution failure as LGTM.

Findings are advisory input to the implementing author, who must fix or explicitly
rebut each finding. The reviewer's LGTM is not mandatory at this advisory gate.
A required reviewer invocation failing or being unavailable pauses the local
shipping workflow; it is not an automatic skip or permission to change reviewers.
Human approval remains separate from every reviewer verdict.

## Output

Return exactly one verdict line: `Verdict: LGTM` or `Verdict: CHANGES NEEDED`.
Write that line as plain text, without Markdown emphasis, a heading or code fences.
For LGTM, add one or two sentences explaining why no material gap remains.
For CHANGES NEEDED, give numbered findings ordered by severity, or specific missing
context. Preserve the distinction between observed facts and inferences. State
the reviewed base/head scope and any verification limitations.

On follow-up, consider fixes and reasoned rebuttals against the same standard.
Drop resolved findings; do not repeat them or move the goalposts. A new commit
requires reviewing its changes before claiming the verdict covers that head.

## Local pre-PR integration

For the automated runner, read [references/runtime.md](references/runtime.md).
The caller launches the opposite CLI through the verified read-only subscription
transport, supplying this rubric, the approved plan path and explicit diff scope.

For work that never had a plan, state `implementation review skipped: no plan for
this work`; do not invent one. This local gate does not impose a Codex dependency
on standalone Claude cloud. It grants no authority to start shipping by itself.

<!-- Method grounded in OpenAI's Codex-for-Claude plugin v1.0.6 adversarial prompt,
command and git collection logic; independently written, no plugin text bundled.
Plan-as-brief and fix-or-rebut policy come from the consuming ship workflow. -->
