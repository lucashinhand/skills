#!/bin/bash
# PR state-file CLI for the GitHub Interaction Rules workflow.
#
# Manages physical-skill .state/<checkout-key>/pr_<PR_NUMBER>.json:
#   {
#     "pr_number": <int>,
#     "root_re_reviews": <int>,
#     "replyThreadMappings": {
#       "<github_thread_id>": {
#         "file": "<path>",
#         "line": <int>,
#         "summary": "<short topic cue, <=200 chars>",
#         "state": "AWAITING_GEMINI" | "AWAITING_RESOLVE_REPLY"
#                | "AWAITING_RETRIAGE" | "RESOLVED"
#       }
#     }
#   }
#
# Purpose: let Claude mutate the state file via Bash (allowlisted) instead of
# Edit/Write (per-file approval prompts). replyThreadMappings binds opaque
# GitHub review-thread IDs to scannable topic cues so Claude does not misroute
# per-thread replies on PRs with many open Gemini threads. All mutations are
# atomic (write to temp file, then rename). No `jq` dependency — uses `node`
# (guaranteed in Claude Code sandboxes).
#
# Usage:
#   pr-state-cli.sh init <pr_number>
#   pr-state-cli.sh read <pr_number>
#   pr-state-cli.sh restore <pr_number> <verified_snapshot.json>
#   pr-state-cli.sh increment-root <pr_number>
#   pr-state-cli.sh reset-root <pr_number>
#   pr-state-cli.sh set-thread <pr_number> <thread_id> <file> <line> <summary...>
#   pr-state-cli.sh set-state <pr_number> <thread_id> <STATE>
#   pr-state-cli.sh remove-thread <pr_number> <thread_id>
#   pr-state-cli.sh prune-resolved <pr_number>
#   pr-state-cli.sh list-threads <pr_number>
#
# Thread states (ownership semantics — "whose turn is it"):
#   AWAITING_GEMINI         — Claude replied; waiting for Gemini.
#   AWAITING_RESOLVE_REPLY  — Gemini confirmed; Claude owes /resolve-thread.
#   AWAITING_RETRIAGE       — Gemini pushed back; Claude must re-triage.
#   RESOLVED                — Claude posted /resolve-thread; prune at cycle end.
#
# set-thread is an upsert: new entries default to state=AWAITING_GEMINI;
# re-upserts update file/line/summary but preserve the existing state.
# Use set-state for state transitions. summary is silently truncated to 200
# chars (cosmetic cap, never fatal). Multi-word summaries may be passed
# unquoted — remaining argv from position 4+ is joined with single spaces.
#
# Calls use an exclusive per-PR lock across the complete read-modify-write.
# Contention fails explicitly; retry after the current call completes. A stale
# lock requires confirming no owner is running before manual recovery.
#
# All mutation commands print the updated JSON on stdout and exit 0 on
# success. read and list-threads also print (read → JSON; list-threads →
# table). On error, exit non-zero with a message on stderr.

set -euo pipefail

if ! command -v node >/dev/null 2>&1; then
  echo "pr-state-cli: node is required but not found on PATH" >&2
  exit 1
fi

# Resolve the physical installed skill, including when invoked through a symlink.
git_common_dir=$(git rev-parse --path-format=absolute --git-common-dir)
state_dir=$(node -e '
  const fs = require("fs");
  const path = require("path");
  const crypto = require("crypto");
  const skill = path.dirname(path.dirname(fs.realpathSync(process.argv[1])));
  const key = crypto.createHash("sha256").update(fs.realpathSync(process.argv[2])).digest("hex");
  process.stdout.write(path.join(skill, ".state", key));
' "${BASH_SOURCE[0]}" "$git_common_dir")

usage() {
  # Print the leading block of `#`-prefixed comment lines (skipping the
  # shebang). Stops at the first non-comment line, so adding/removing header
  # lines never requires touching this function.
  awk '
    NR == 1 && /^#!/ { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$0" >&2
  exit 2
}

cmd="${1:-}"
if [[ -z "$cmd" ]]; then usage; fi
shift

pr="${1:-}"
if [[ -z "$pr" ]]; then
  echo "pr-state-cli: missing <pr_number>" >&2
  usage
fi
if ! [[ "$pr" =~ ^[1-9][0-9]*$ ]]; then
  echo "pr-state-cli: <pr_number> must be a positive integer, got: $pr" >&2
  exit 2
fi
shift

# Per-command argv gates. Extra args beyond <pr_number> are command-specific,
# so we validate here (cheap) and env-pass to the node dispatch below. Unknown
# commands fall through and hit the node switch default, which emits a clear
# "unknown command" error.
thread_id=""
file_path=""
line_num=""
new_state=""
summary=""
snapshot=""
case "$cmd" in
  init|read|increment-root|reset-root|prune-resolved|list-threads)
    if [[ $# -ne 0 ]]; then
      echo "pr-state-cli: $cmd takes no extra arguments, got $#" >&2
      exit 2
    fi
    ;;
  restore)
    if [[ $# -ne 1 ]]; then
      echo "pr-state-cli: restore requires one verified snapshot path" >&2
      exit 2
    fi
    snapshot="$1"
    ;;
  remove-thread)
    if [[ $# -ne 1 ]]; then
      echo "pr-state-cli: remove-thread requires <thread_id>" >&2
      exit 2
    fi
    thread_id="$1"
    ;;
  set-state)
    if [[ $# -ne 2 ]]; then
      echo "pr-state-cli: set-state requires <thread_id> <STATE>" >&2
      exit 2
    fi
    thread_id="$1"
    new_state="$2"
    ;;
  set-thread)
    if [[ $# -lt 4 ]]; then
      echo "pr-state-cli: set-thread requires <thread_id> <file> <line> <summary...>" >&2
      exit 2
    fi
    thread_id="$1"
    file_path="$2"
    line_num="$3"
    shift 3
    # Join remaining argv into a single summary string so multi-word summaries
    # work without mandatory caller-side quoting.
    summary="$*"
    ;;
  *)
    # Let the node switch default handle unknown commands.
    ;;
esac

mkdir -p "$state_dir"
state_file="${state_dir}/pr_${pr}.json"

# Dispatch: all operations run inside a single node invocation so reads,
# mutations, and writes are coherent and atomic (write-temp-then-rename).
STATE_FILE="$state_file" \
PR_NUMBER="$pr" \
CMD="$cmd" \
THREAD_ID="$thread_id" \
FILE_PATH="$file_path" \
LINE_NUM="$line_num" \
NEW_STATE="$new_state" \
SUMMARY="$summary" \
SNAPSHOT="$snapshot" \
node -e '
  const fs = require("fs");
  const file = process.env.STATE_FILE;
  const pr = parseInt(process.env.PR_NUMBER, 10);
  const cmd = process.env.CMD;
  const threadId = process.env.THREAD_ID || "";
  const filePath = process.env.FILE_PATH || "";
  const lineNumRaw = process.env.LINE_NUM || "";
  const summaryRaw = process.env.SUMMARY || "";
  const newState = process.env.NEW_STATE || "";

  // Thread-state enum. UPPER_SNAKE_CASE. Ownership semantics: each value
  // answers "whose turn is it / what does Claude owe on this thread?".
  const ALLOWED_STATES = [
    "AWAITING_GEMINI",
    "AWAITING_RESOLVE_REPLY",
    "AWAITING_RETRIAGE",
    "RESOLVED",
  ];
  const SUMMARY_MAX = 200;

  const DEFAULT = () => ({
    pr_number: pr,
    root_re_reviews: 0,
    replyThreadMappings: {},
  });

  const die = (msg) => {
    process.stderr.write("pr-state-cli: " + msg + "\n");
    process.exit(1);
  };

  const warn = (msg) => {
    process.stderr.write("pr-state-cli: " + msg + "\n");
  };

  const isPlainObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

  const isPositiveInt = (n) => Number.isInteger(n) && n > 0;

  const truncateSummary = (s) => {
    if (typeof s !== "string") return "";
    if (s.length <= SUMMARY_MAX) return s;
    return s.slice(0, SUMMARY_MAX - 3) + "...";
  };

  const coerceLine = (raw) => {
    if (typeof raw === "number" && isPositiveInt(raw)) return raw;
    if (typeof raw === "string" && /^[1-9][0-9]*$/.test(raw)) return parseInt(raw, 10);
    return null;
  };

  const normalizeThreadMappings = (raw) => {
    if (!isPlainObject(raw)) return {};
    const out = {};
    for (const [id, entry] of Object.entries(raw)) {
      if (typeof id !== "string" || id.length === 0) {
        warn("dropped malformed thread entry <empty id>: id must be non-empty string");
        continue;
      }
      if (!isPlainObject(entry)) {
        warn("dropped malformed thread entry " + id + ": entry must be an object");
        continue;
      }
      if (typeof entry.file !== "string" || entry.file.length === 0) {
        warn("dropped malformed thread entry " + id + ": file must be non-empty string");
        continue;
      }
      const line = coerceLine(entry.line);
      if (line === null) {
        warn("dropped malformed thread entry " + id + ": line must be positive integer");
        continue;
      }
      if (typeof entry.summary !== "string") {
        warn("dropped malformed thread entry " + id + ": summary must be string");
        continue;
      }
      if (!ALLOWED_STATES.includes(entry.state)) {
        warn("dropped malformed thread entry " + id + ": state must be one of " + ALLOWED_STATES.join("|"));
        continue;
      }
      out[id] = {
        file: entry.file,
        line,
        summary: truncateSummary(entry.summary),
        state: entry.state,
      };
    }
    return out;
  };

  function load() {
    if (!fs.existsSync(file)) return DEFAULT();
    let raw;
    try { raw = fs.readFileSync(file, "utf8"); }
    catch (e) { die(`cannot read ${file}: ${e.message}`); }
    let parsed;
    try { parsed = JSON.parse(raw); }
    catch (e) { die(`state file is not valid JSON: ${e.message}`); }
    if (typeof parsed !== "object" || parsed === null) die("state file root must be an object");
    if (parsed.pr_number !== pr) die("state PR does not match; refusing to overwrite");
    if (typeof parsed.root_re_reviews !== "number" || !Number.isInteger(parsed.root_re_reviews) || parsed.root_re_reviews < 0) {
      die("invalid review counter; recover verified state rather than reset the budget");
    }
    // Preserve the three known top-level keys; any future schema extension
    // MUST update this builder or the new key will be silently stripped.
    // (Legacy keys like thread_replies from the pre-batched-reviews schema
    // are dropped here on purpose.)
    const normalized = {
      pr_number: parsed.pr_number,
      root_re_reviews: parsed.root_re_reviews,
      replyThreadMappings: normalizeThreadMappings(parsed.replyThreadMappings),
    };
    if (!require("util").isDeepStrictEqual(normalized.replyThreadMappings, parsed.replyThreadMappings)) {
      die("invalid thread mappings; refusing lossy state normalisation");
    }
    return normalized;
  }

  function save(obj) {
    const tmp = file + ".tmp." + process.pid;
    fs.writeFileSync(tmp, JSON.stringify(obj, null, 2) + "\n", { mode: 0o644 });
    fs.renameSync(tmp, file);
  }

  const requireThreadId = () => {
    if (threadId.length === 0) die("missing <thread_id>");
  };

  const requireThreadPresent = (s, id) => {
    if (!Object.prototype.hasOwnProperty.call(s.replyThreadMappings, id)) {
      die("thread_id not found in replyThreadMappings: " + id);
    }
  };

  const padRight = (s, n) => {
    const str = String(s);
    if (str.length >= n) return str;
    return str + " ".repeat(n - str.length);
  };

  const printThreadTable = (mappings) => {
    const entries = Object.entries(mappings);
    if (entries.length === 0) {
      process.stdout.write("(no threads)\n");
      return;
    }
    const rows = entries.map(([id, e]) => [id, e.state, e.file + ":" + e.line, e.summary]);
    const headers = ["THREAD_ID", "STATE", "FILE:LINE", "SUMMARY"];
    const widths = headers.map((h, i) => {
      const colMax = rows.reduce((m, r) => Math.max(m, String(r[i]).length), h.length);
      return colMax;
    });
    // SUMMARY is the last column — no need to pad its width.
    const render = (row) => row.map((cell, i) => i === row.length - 1 ? String(cell) : padRight(cell, widths[i])).join("  ");
    process.stdout.write(render(headers) + "\n");
    for (const row of rows) {
      process.stdout.write(render(row) + "\n");
    }
  };

  const lock = file + ".lock";
  try { fs.mkdirSync(lock); }
  catch (error) {
    if (error.code === "EEXIST") die("state is locked; retry after the active call finishes; do not steal a live lock");
    throw error;
  }
  process.on("exit", () => fs.rmdirSync(lock));
  const s = load();

  switch (cmd) {
    case "init":
      // Idempotent. Writes DEFAULT() only if file is absent; otherwise leaves
      // existing counters intact and just normalizes pr_number.
      if (!fs.existsSync(file)) save(DEFAULT());
      else save(s);
      break;
    case "restore": {
      const incoming = JSON.parse(fs.readFileSync(process.env.SNAPSHOT, "utf8"));
      if (!isPlainObject(incoming) || incoming.pr_number !== pr ||
          !Number.isInteger(incoming.root_re_reviews) || incoming.root_re_reviews < 0 ||
          !isPlainObject(incoming.replyThreadMappings)) {
        die("snapshot must contain the verified PR, review count and thread mappings");
      }
      const normalized = normalizeThreadMappings(incoming.replyThreadMappings);
      if (!require("util").isDeepStrictEqual(normalized, incoming.replyThreadMappings) ||
          Object.keys(incoming).sort().join(",") !== "pr_number,replyThreadMappings,root_re_reviews") {
        die("snapshot contains malformed or non-canonical thread mappings; refusing lossy recovery");
      }
      if (fs.existsSync(file) &&
          !require("util").isDeepStrictEqual(s, incoming)) {
        die("existing state differs; reconcile snapshots explicitly, never overwrite live state");
      }
      save(incoming);
      process.stdout.write(JSON.stringify(incoming, null, 2) + "\n");
      process.exit(0);
    }
    case "read":
      // No mutation — do not rewrite the file.
      break;
    case "increment-root":
      s.root_re_reviews += 1;
      save(s);
      break;
    case "reset-root":
      s.root_re_reviews = 0;
      save(s);
      break;
    case "set-thread": {
      requireThreadId();
      if (filePath.length === 0) die("set-thread: <file> must be non-empty");
      const line = coerceLine(lineNumRaw);
      if (line === null) die("set-thread: <line> must be a positive integer, got: " + lineNumRaw);
      if (summaryRaw.length === 0) die("set-thread: <summary> must be non-empty");
      const existing = s.replyThreadMappings[threadId];
      const preservedState = existing && ALLOWED_STATES.includes(existing.state)
        ? existing.state
        : "AWAITING_GEMINI";
      s.replyThreadMappings[threadId] = {
        file: filePath,
        line,
        summary: truncateSummary(summaryRaw),
        state: preservedState,
      };
      save(s);
      break;
    }
    case "set-state": {
      requireThreadId();
      if (!ALLOWED_STATES.includes(newState)) {
        die("set-state: <STATE> must be one of " + ALLOWED_STATES.join("|") + ", got: " + newState);
      }
      requireThreadPresent(s, threadId);
      s.replyThreadMappings[threadId].state = newState;
      save(s);
      break;
    }
    case "remove-thread": {
      requireThreadId();
      // Idempotent: no error if the id is absent.
      if (Object.prototype.hasOwnProperty.call(s.replyThreadMappings, threadId)) {
        delete s.replyThreadMappings[threadId];
        save(s);
      }
      break;
    }
    case "prune-resolved": {
      const kept = {};
      let removed = 0;
      for (const [id, entry] of Object.entries(s.replyThreadMappings)) {
        if (entry.state === "RESOLVED") {
          removed += 1;
          continue;
        }
        kept[id] = entry;
      }
      if (removed > 0) {
        s.replyThreadMappings = kept;
        save(s);
      }
      break;
    }
    case "list-threads":
      // Render the compact table to stdout and exit early — do NOT fall
      // through to the JSON dump below. Use `read` for JSON output.
      printThreadTable(s.replyThreadMappings);
      process.exit(0);
    default:
      die("unknown command: " + cmd);
  }

  process.stdout.write(JSON.stringify(s, null, 2) + "\n");
'
