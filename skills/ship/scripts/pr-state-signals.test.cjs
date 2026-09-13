const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');
const test = require('node:test');

for (const [signal, code] of [['SIGINT', 130], ['SIGTERM', 143]]) {
  test(`${signal} releases the owned lock and preserves review state`, async t => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ship-signal-'));
    t.after(() => fs.rmSync(root, {recursive:true, force:true}));
    const scripts = path.join(root, 'skill', 'scripts');
    fs.mkdirSync(scripts, {recursive:true});
    const helper = path.join(scripts, 'pr-state-cli.sh');
    fs.copyFileSync(path.join(__dirname, 'pr-state-cli.sh'), helper);
    assert.equal(spawnSync('git', ['init', '-q', root]).status, 0);
    const invoke = (...args) => spawnSync('bash', [helper, ...args], {cwd:root, encoding:'utf8'});
    assert.equal(invoke('init', '442').status, 0);
    assert.equal(invoke('increment-root', '442').status, 0);
    const before = invoke('read', '442').stdout;
    const preload = path.join(root, 'hold.cjs');
    // Keep the real CLI alive after its synchronous operation so an OS signal
    // deterministically arrives while it still owns the real lock.
    fs.writeFileSync(preload, `
      const fs = require('node:fs');
      const mkdir = fs.mkdirSync;
      fs.mkdirSync = (...args) => {
        const result = mkdir(...args);
        if (String(args[0]).endsWith('.lock')) {
          setInterval(() => {}, 1000);
          setImmediate(() => process.stderr.write('LOCK_READY:' + process.pid + '\\n'));
        }
        return result;
      };
    `);
    const child = spawn('bash', [helper, 'read', '442'], {
      cwd:root, env:{...process.env, NODE_OPTIONS:`--require=${preload}`},
      stdio:['ignore', 'pipe', 'pipe'],
    });
    const ready = {pid:null, output:''};
    t.after(() => {
      if (child.exitCode !== null) return;
      if (ready.pid) {
        try { process.kill(ready.pid, 'SIGKILL'); } catch (error) {
          if (error.code !== 'ESRCH') throw error;
        }
      }
      child.kill('SIGKILL');
    });
    const result = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('signal fixture timed out')), 5000);
      child.on('error', reject);
      child.stderr.on('data', data => {
        ready.output += data.toString();
        const match = ready.output.match(/LOCK_READY:(\d+)/);
        if (!match || ready.pid) return;
        ready.pid = Number(match[1]);
        process.kill(ready.pid, signal);
      });
      child.on('close', exitCode => { clearTimeout(timeout); resolve(exitCode); });
    });
    assert.equal(result, code, ready.output);
    const after = invoke('read', '442');
    assert.equal(after.status, 0, after.stderr);
    assert.equal(after.stdout, before);
  });
}
