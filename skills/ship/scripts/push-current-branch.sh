#!/bin/bash
# Pushes HEAD to the same named origin branch for claude/* and codex/* only.
# Takes no arguments, so it can be allowlisted exactly in settings.json
# without opening bare `git push` to pushes on main.
set -euo pipefail
if [[ $# -ne 0 ]]; then
  echo "refusing: this helper takes no arguments" >&2
  exit 1
fi
branch=$(git rev-parse --abbrev-ref HEAD)
case "$branch" in
  claude/*|codex/*) exec git -c remote.origin.mirror=false push --no-follow-tags origin "HEAD:refs/heads/$branch" ;;
  *) echo "refusing: '$branch' is not a claude/* or codex/* branch" >&2; exit 1 ;;
esac
