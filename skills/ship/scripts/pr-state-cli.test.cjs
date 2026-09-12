const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const test = require('node:test');
const source = path.join(__dirname, 'pr-state-cli.sh');

test('physical skill state isolates repositories and preserves counters and thread transitions', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ship-state-'));
  t.after(() => fs.rmSync(root, {recursive:true, force:true}));
  const scripts = path.join(root, 'skill', 'scripts');
  fs.mkdirSync(scripts, {recursive:true});
  const helper = path.join(scripts, 'pr-state-cli.sh');
  fs.copyFileSync(source, helper);
  const alias = path.join(root, 'alias');
  fs.symlinkSync(path.dirname(scripts), alias);
  const repos = ['one', 'two'].map(name => {
    const repo = path.join(root, name);
    assert.equal(spawnSync('git', ['init', '-q', repo]).status, 0);
    return repo;
  });
  const call = (repo, ...args) => spawnSync('bash', [path.join(alias, 'scripts', 'pr-state-cli.sh'), ...args], {cwd:repo, encoding:'utf8'});
  const ok = (repo, ...args) => {
    const result = call(repo, ...args);
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  };
  ok(repos[0], 'init', '442');
  ok(repos[0], 'increment-root', '442');
  assert.equal(ok(repos[0], 'init', '442').root_re_reviews, 1);
  ok(repos[0], 'set-thread', '442', 'thread', 'file.js', '3', 'summary');
  ok(repos[0], 'set-state', '442', 'thread', 'AWAITING_RETRIAGE');
  assert.equal(ok(repos[0], 'set-thread', '442', 'thread', 'file.js', '4', 'updated').replyThreadMappings.thread.state, 'AWAITING_RETRIAGE');
  assert.equal(ok(repos[1], 'init', '442').root_re_reviews, 0);
  assert.equal(ok(repos[0], 'init', '443').root_re_reviews, 0);
  const state = path.join(root, 'skill', '.state');
  const ownerDir = fs.readdirSync(state).map(key => path.join(state, key)).find(dir => fs.existsSync(path.join(dir, 'pr_443.json')));
  const lock = path.join(ownerDir, 'pr_442.json.lock');
  fs.mkdirSync(lock);
  assert.notEqual(call(repos[0], 'increment-root', '442').status, 0);
  fs.rmdirSync(lock);
  assert.equal(ok(repos[0], 'read', '442').root_re_reviews, 1);
  assert.notEqual(call(repos[0], 'set-state', '442', 'thread', 'INVALID').status, 0);
  assert.equal(fs.existsSync(lock), false);
  ok(repos[0], 'set-state', '442', 'thread', 'RESOLVED');
  assert.deepEqual(ok(repos[0], 'prune-resolved', '442').replyThreadMappings, {});
  const snapshot = path.join(root, 'verified.json');
  const recovered = {pr_number:444, root_re_reviews:3, replyThreadMappings:{
    thread:{file:'file.js', line:4, summary:'awaiting confirmation', state:'AWAITING_GEMINI'}
  }};
  fs.writeFileSync(snapshot, JSON.stringify(recovered));
  assert.deepEqual(ok(repos[1], 'restore', '444', snapshot), recovered);
  assert.deepEqual(ok(repos[1], 'init', '444'), recovered);
  assert.deepEqual(ok(repos[1], 'restore', '444', snapshot), recovered);
  fs.writeFileSync(snapshot, JSON.stringify({...recovered, root_re_reviews:0}));
  assert.notEqual(call(repos[1], 'restore', '444', snapshot).status, 0);
  assert.equal(ok(repos[1], 'read', '444').root_re_reviews, 3);
  assert.notEqual(call(repos[1], 'restore', '445', snapshot).status, 0);
  fs.writeFileSync(snapshot, JSON.stringify({...recovered, pr_number:445, replyThreadMappings:{broken:{}}}));
  assert.notEqual(call(repos[1], 'restore', '445', snapshot).status, 0);
});
