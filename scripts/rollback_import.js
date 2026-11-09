#!/usr/bin/env node
/**
 * Undo import_sessions.js: delete the imported session directories and remove their ids
 * from ~/.dsh/storages/workspace.json. Backs up workspace.json first.
 *
 * Usage:
 *   node rollback_import.js --yes [--imported imported_sessions.json] [--dsh-home ~/.dsh]
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const argv = process.argv.slice(2);
function arg(name, dflt) {
  const i = argv.indexOf('--' + name);
  return i >= 0 ? argv[i + 1] : dflt;
}

const DSH_HOME = arg('dsh-home', path.join(os.homedir(), '.dsh'));
const IMPORTED = arg('imported', path.join(process.cwd(), 'imported_sessions.json'));

if (!argv.includes('--yes')) {
  console.log('破坏性操作：删除导入的会话日志并从工作区移除。确认请加 --yes');
  process.exit(0);
}

const info = JSON.parse(fs.readFileSync(IMPORTED, 'utf8'));
const ids = new Set(info.created.map((c) => c.sessionId));

let removedDirs = 0;
for (const c of info.created) {
  if (fs.existsSync(c.dir)) {
    fs.rmSync(c.dir, { recursive: true, force: true });
    removedDirs++;
  }
}

const store = path.join(DSH_HOME, 'storages', 'workspace.json');
const ws = JSON.parse(fs.readFileSync(store, 'utf8'));
let removedIds = 0;
for (const row of Object.values(ws.tables.workspaces)) {
  const before = row.sessionIds.length;
  row.sessionIds = row.sessionIds.filter((id) => !ids.has(id));
  removedIds += before - row.sessionIds.length;
}
const bak = `${store}.bak.${new Date().toISOString().replace(/[-:T]/g, '').slice(0, 15)}`;
fs.copyFileSync(store, bak);
fs.writeFileSync(store, JSON.stringify(ws, null, 2), 'utf8');

console.log(`已删除会话目录 ${removedDirs} 个，从工作区移除 sessionId ${removedIds} 条`);
console.log('workspace.json 备份:', bak);
console.log('重启 DSH 后生效。');
