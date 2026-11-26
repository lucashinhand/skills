---
name: tugboat
description: "Escorts an agent branch through review to a merge-ready PR, never merging it: opens or reuses the PR as the repository's GitHub bot, runs the cross-model implementation review, hands the PR to one Claude cloud owner, runs the Gemini Code Assist review loop, and finishes on Gemini plus implementer approval bound to the head commit. Use when the user types /tugboat, asks to open or create a PR for agent work, or the post-push hook on claude/* branches asks for it. Not Anthropic's native /ship or /steward."
---

# Tugboat

End-to-end PR lifecycle: create, review, approve. A tug escorts the ship in; the human docks it (merges).

## Repository bindings

Read the consuming repository's agent instructions and `.claude/github.md` explicitly in both harnesses. The repository supplies its nitpick destination and GitHub policy; do not assume an issue number. Resolve scripts relative to this skill's physical installed directory, never a hard-coded `.claude/skills` path: `<physical-skill-directory>` in the linked files means `${CLAUDE_SKILL_DIR}` (in Codex, the directory containing this SKILL.md). Use the verified checkout as the working directory.

<!-- TODO: revisit repository bindings and permission setup with a setup script. -->

## Prerequisites

- Commits pushed to remote
- MCP GitHub tools available — verify with `mcp__github__get_me`; use `ToolSearch` if missing
- All GitHub interactions via `mcp__github__*` only — no `gh` CLI, no REST/GraphQL

## Flow Selection

Selection is capability-based, not environment-based: check whether `mcp__github__subscribe_pr_activity` exists (`ToolSearch` `select:mcp__github__subscribe_pr_activity`).

- **Available** → follow [CLOUD.md](CLOUD.md) — the event-driven review loop (create PR → subscribe → await auto-review → review loop → terminate on Gemini LGTM).
- **Absent** → follow [LOCAL.md](LOCAL.md) — opposite-CLI implementation review (planned work only) → create PR → automatically launch Claude cloud → hand the session to the human (one check after 30 minutes) → current-head dual approval.

## Rules Reference

Triage categories, reply templates, thread state management, inline shortcodes, and tooling constraints live in `.claude/github.md`. The pre-tool-use hook injects it automatically on `mcp__github__*` calls. If not in context, `Read .claude/github.md`. Shared templates (PR body blocks, re-review format, resolved-concerns block, Gemini response handling) live in [REFERENCE.md](REFERENCE.md).
