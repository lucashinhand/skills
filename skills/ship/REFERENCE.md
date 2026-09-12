# Ship — Reference

Detailed templates and response-handling logic for the /ship workflow. [SKILL.md](SKILL.md) selects the flow, [CLOUD.md](CLOUD.md) / [LOCAL.md](LOCAL.md) are the orchestration; this file provides the formats and rules for each step.

## PR Body {#pr-body}

Append these two collapsible blocks verbatim to the end of every PR description created by /ship:

```markdown
<details>
<summary>How Claude triages Gemini Code Assist comments (A / B / C)</summary>

Per `.claude/github.md` §3, Claude classifies every Gemini inline comment into one of three categories and acts accordingly:

- **A — Genuine bug / logical error.** Implement the fix, push, and reply on the thread `@gemini-code-assist Addressed in <sha> — <fix summary>.`
- **B — Incorrect suggestion.** Don't change the code. Reply on the thread `@gemini-code-assist <technical justification for why the current implementation is correct>.`
- **C — Nitpick / subjective best practice.** Don't change the code. Log the nitpick at the destination specified in the repository's GitHub rules. Reply on the thread `@gemini-code-assist Acknowledged as a non-blocking nitpick. Logged to the repository's nitpick destination for later review.`

Threads stay open until either Gemini explicitly confirms resolution on the thread (Claude then posts `/resolve-thread`) or Gemini confirms resolution in the next batched root re-review.
</details>

<details>
<summary>Notes for Gemini Code Reviewer — please read before reviewing</summary>

You MUST follow these conventions when reviewing this PR:

1. **If everything is fine, reply `LGTM`.** That single signal is how we mark the PR approved — no elaboration needed.
2. **Confirm resolution in reply threads.** When Claude replies to one of your inline comments with an `@gemini-code-assist` mention asking you to confirm, reply on that same thread with an explicit resolved-or-not-resolved signal. A clear per-thread signal lets us close the thread immediately; silence or ambiguity leaves it open.
3. **Do NOT re-raise previously-resolved concerns.** The `## Previously-resolved concerns — please do not re-raise` section of this PR body (when present) is authoritative. Any concern listed there is settled for the life of this PR — treat it as shared context across every review round.
4. **Inline `gemini-resolved(…)` shortcodes are metadata, not code.** Lines shaped like `// gemini-resolved(B|C, threads=<id>[,<id>…]): <rationale>` (using the file's native line-comment syntax) are markers Claude stamps at a `file:line` where a B/C pushback has been resolved. They are workflow annotations — do NOT flag them as code changes or style issues.
</details>
```

## Re-Review Request {#re-review}

### When NOT to Request

- The push contains only non-Claude commits.
- There is no open PR yet.
- The circuit breaker is tripped (`root_re_reviews >= 3`).
- Any actionable Gemini thread lacks a Claude reply.

**Per-thread confirmations do NOT substitute for a root re-review.** Even if every thread was resolved per-thread, you MUST still post a root `/gemini` and wait for Gemini's root response. The cycle ends ONLY on a root-level LGTM / "no feedback" or a tripped circuit breaker.

### Format

Post via `mcp__github__add_issue_comment`. Enumerate open threads from `bash <physical-skill-directory>/scripts/pr-state-cli.sh list-threads <PR>` — one bullet per non-RESOLVED entry:

```text
/gemini Requesting re-review.

Pushed: <short-sha | sha..sha | "no push — replies only">

Thread outcomes since last review:
- Thread <thread_id> (<file>:<line>) — <A|B|C> — <one-line resolution>
- Thread <thread_id> (<file>:<line>) — <A|B|C> — <one-line resolution>

Please account for all prior review comments, replies, and the resolutions listed above before raising new issues. For each thread above, if you agree it is resolved, please confirm in your reply so we can close it. If everything looks good overall, reply "LGTM" for an explicit approval signal.
```

Ensure your PR activity channel is active after posting — `subscribe_pr_activity` in the cloud flow; in the local flow the delegated session holds the subscription.

### Zero Open Threads Variant

If every thread is already closed, omit the bullet list:

```text
/gemini Requesting re-review.

Pushed: <short-sha | sha..sha | "no push — replies only">

All prior inline threads have been resolved per-thread. No open threads remain. Please confirm overall — reply "LGTM" if everything looks good, or raise any remaining issues.
```

## Previously-Resolved Concerns Block {#resolved-block}

Before every re-review (including the first cycle), update a `## Previously-resolved concerns — please do not re-raise` heading in the PR body via `mcp__github__update_pull_request`.

- **Location:** immediately above the `<details><summary>How Claude triages…</summary>` block.
- **Scope:** B and C resolutions only. Do NOT list Category A fixes.
- **Entry format:**
  ```
  - **<file>:<line>** — <B|C> — <1-sentence decision and rationale>. Threads: <id>, <id>, …
  ```
- **Thread-ID merging:** if Gemini re-raises the same concern under a new thread ID, append the new ID — do NOT create a second entry.
- **Stability:** entries are never auto-pruned. Line numbers are re-anchored on each update. Only explicit user instruction removes an entry.
- **Empty state:** if no B/C resolutions exist yet, omit the heading entirely.

## Handling Gemini Responses {#gemini-responses}

### Per-Thread Reply (preferred, faster)

When Gemini replies on a thread Claude has responded to:

1. **Confirms resolution** — `list-threads`, verify thread_id matches topic. `set-state <PR> <thread_id> AWAITING_RESOLVE_REPLY`. Reply:
   ```
   /resolve-thread — Gemini confirmed resolution in per-thread reply.
   ```
   Then `set-state <PR> <thread_id> RESOLVED`.

2. **Pushes back / re-raises** — `set-state <PR> <thread_id> AWAITING_RETRIAGE`. Re-triage per `.claude/github.md` §3. The new reply's `set-thread` upsert + explicit `set-state … AWAITING_GEMINI` completes the transition.

3. **Ambiguous** — leave in current state. Let the next root re-review adjudicate.

### Root Re-Review Response

After Gemini replies at root level:

1. **Explicit thread ID match** — Gemini cites a thread ID and agrees resolved. `list-threads`, confirm match. `set-state <PR> <thread_id> AWAITING_RESOLVE_REPLY`. Reply:
   ```
   /resolve-thread — Gemini confirmed resolution in root re-review (thread ID cited).
   ```
   Then `set-state <PR> <thread_id> RESOLVED`.

2. **Inferred match** — Gemini doesn't cite an ID but clearly describes a resolved thread's issue. Use `list-threads` to match by file:line + summary. `set-state <PR> <thread_id> AWAITING_RESOLVE_REPLY`. Reply:
   ```
   /resolve-thread — Inferred from Gemini's root re-review: "<short quote>" matches this thread's issue (<file>:<line>).
   ```
   Then `set-state <PR> <thread_id> RESOLVED`.

3. **Ambiguous match** — multiple candidates or unclear. Do NOT resolve. Leave for next cycle.

4. **Broad approval (LGTM / "no feedback")** — Do NOT fan out `/resolve-thread`. End the cycle: `prune-resolved`, unsubscribe, inform user. Open threads stay open for human resolution; their mappings stay in whatever non-RESOLVED state they held.

5. **Re-raises an issue** — `set-state <PR> <thread_id> AWAITING_RETRIAGE` for each, re-triage per `.claude/github.md` §3.

### /resolve-thread Requirements

Every `/resolve-thread` reply MUST include a short "why" after the `—` dash. An external watcher tool closes the thread automatically. Claude does NOT call `mcp__github__resolve_pull_request_review_thread`.
