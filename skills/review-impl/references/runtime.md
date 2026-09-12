# Local implementation-review runner

Install `review-plan` and `review-impl` together; this skill reuses the former's
verified CLI transports by physical sibling path. No companion plugin is needed.

```sh
python3 <review-impl>/scripts/review_impl.py --repo <repo> \
  --plan <approved-plan.md> --base <base-ref> --head <head-ref> --author codex
```

`--author` selects the opposite reviewer. Both refs are resolved to commits;
review covers merge-base to head. This automated pre-PR runner requires a clean
checkout at that head, including no untracked files. It does not implement the
rubric's optional manual working-tree mode. An absent plan is handled by the
caller as an explicit no-plan skip, not by fabricating a plan for this command.

The caller supplies the human-approved plan; the script does not infer human
approval from a filename or a previous reviewer verdict. It snapshots the plan
and complete binary-capable diff under the physical skill's disposable `.state`,
and gives the reviewer paths to the snapshot and scope manifest. It checks the
head, clean checkout and plan again before accepting a result.

Output is JSON with exact scope, reviewer/session, verdict, feedback and artefact
path. Both LGTM and CHANGES NEEDED mean a completed advisory review (exit 0), not
shipping approval. The author fixes or explicitly rebuts findings. Invocation
failure exits nonzero and pauses shipping; no auto-skip or alternate reviewer.
Both reviewer prompts request source inspection, not test execution. The author
runs tests separately. Codex's read-only sandbox can reject even shell heredoc
temporary files; a recovered failed tool call still invalidates this strict run.
Add `--resume <exact-session-id>` and `--context <author-response.md>` when needed.
No implicit latest-session lookup, review loop or PR mutation is performed here.
