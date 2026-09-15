---
name: pickup
description: Pick up a ticket and start a working session. Invoked via /pickup with a GitHub issue — by number or description — and optional mode (grill-me or grill-with-docs). Reads the issue, grounds in the relevant codebase, summarises understanding, then runs a grilling session for shared alignment. Use when starting work on a new ticket or issue.
---

# /pickup <issue> [grill-me|grill-with-docs]

Default mode: `grill-me`

1. Read the GitHub issue by number, or search by description.
2. Send Explore agents on Sonnet to ground yourself in the codebase.
3. Give us a brief summary and context of your understanding of the ticket and why we are doing it.
4. `/grill-me` (or `/grill-with-docs` if specified) so we have shared understanding.
