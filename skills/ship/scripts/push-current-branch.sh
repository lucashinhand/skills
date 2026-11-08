#!/bin/bash
# Pushes HEAD to the same named origin branch for claude/*, codex/* and dream/* only.
# Takes no arguments, so it can be allowlisted exactly in settings.json
# without opening bare `git push` to pushes on main.
set -euo pipefail
if [[ $# -ne 0 ]]; then
  echo "refusing: this helper takes no arguments" >&2
  exit 1
fi
branch=$(git rev-parse --abbrev-ref HEAD)
case "$branch" in
  claude/*|codex/*|dream/*) exec git -c remote.origin.mirror=false push --no-follow-tags origin "HEAD:refs/heads/$branch" ;;
  *) echo "refusing: '$branch' is not a claude/*, codex/* or dream/* branch" >&2; exit 1 ;;
esac
