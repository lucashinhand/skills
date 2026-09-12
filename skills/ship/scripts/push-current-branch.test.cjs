const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const helper = path.join(__dirname, "push-current-branch.sh");

test("pushes only the current agent branch, without tags or configured refspecs", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agent-push-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const remote = path.join(root, "remote.git");
  const local = path.join(root, "local");
  const git = (...args) => {
    const result = spawnSync("git", args, { cwd: root, encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout.trim();
  };
  git("init", "--bare", remote);
  git("init", "-b", "main", local);
  git("-C", local, "config", "user.name", "Fixture");
  git("-C", local, "config", "user.email", "fixture@example.invalid");
  git("-C", local, "commit", "--allow-empty", "-m", "fixture");
  git("-C", local, "remote", "add", "origin", remote);
  git("-C", local, "config", "remote.origin.push", "HEAD:refs/heads/main");
  git("-C", local, "config", "push.followTags", "true");
  git("-C", local, "tag", "-a", "fixture", "-m", "must stay local");
  const push = (...args) => spawnSync("bash", [helper, ...args], { cwd: local, encoding: "utf8" });
  assert.notEqual(push().status, 0);
  for (const branch of ["claude/test", "codex/test"]) {
    git("-C", local, "checkout", "-b", branch);
    assert.notEqual(push("--force").status, 0);
    const result = push();
    assert.equal(result.status, 0, result.stderr);
  }
  assert.equal(git("--git-dir", remote, "for-each-ref", "--format=%(refname)"),
    "refs/heads/claude/test\nrefs/heads/codex/test");
  git("-C", local, "checkout", "--detach");
  assert.notEqual(push().status, 0);
});
