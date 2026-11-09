#!/usr/bin/env node
/**
 * Validate imported DSH session logs offline, using DSH's own released-v0 rules,
 * so a bad format never reaches the app.
 *
 * It loads @deepseek-ai/dsh-session-format-v0-to-v1 from the local DSH profile when present and
 * checks: exact top-level members, per-event data members, nested payload semantics,
 * cross-event relationships and surface metadata. Without the package it still checks
 * the structural basics (header, contiguous seq, surface events).
 *
 * Usage:
 *   node validate_sessions.js [--imported imported_sessions.json] [--pkg <path to lib/index.js>]
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const zlib = require('zlib');

const argv = process.argv.slice(2);
function arg(name, dflt) {
  const i = argv.indexOf('--' + name);
  return i >= 0 ? argv[i + 1] : dflt;
}

const DSH_HOME = arg('dsh-home', path.join(os.homedir(), '.dsh'));
const IMPORTED = arg('imported', path.join(process.cwd(), 'imported_sessions.json'));
const PKG = arg('pkg', findPkg());

function findPkg() {
  const candidates = [
    path.join(DSH_HOME, 'profiles', 'node_modules', '@deepseek-ai',
      'dsh-session-format-v0-to-v1', 'lib', 'index.js'),
  ];
  const profiles = path.join(DSH_HOME, 'profiles');
  if (fs.existsSync(profiles)) {
    for (const d of fs.readdirSync(profiles)) {
      candidates.push(path.join(profiles, d, 'node_modules', '@deepseek-ai',
        'dsh-session-format-v0-to-v1', 'lib', 'index.js'));
    }
  }
  return candidates.find((p) => fs.existsSync(p)) || null;
}

let rules = null;
if (PKG) {
  try {
    rules = require(PKG);
  } catch (e) {
    console.warn(`[warn] 无法加载 ${PKG}: ${e.message}`);
  }
}
if (!rules) console.warn('[warn] 未找到 dsh-session-format-v0-to-v1，只做基础结构校验');

const MAGIC = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);
function decompressAll(buf) {
  const idx = [];
  let i = 0;
  while (true) {
    const k = buf.indexOf(MAGIC, i);
    if (k < 0) break;
    idx.push(k);
    i = k + 4;
  }
  let out = '';
  for (let n = 0; n < idx.length; n++) {
    const f = buf.subarray(idx[n], n + 1 < idx.length ? idx[n + 1] : buf.length);
    out += zlib.zstdDecompressSync(f).toString('utf8');
  }
  return out;
}

const TOP = new Set(['type', 'seq', 'time', 'data', 'surfaceOp', 'sourceEventSeqs']);

function checkFile(file) {
  const problems = [];
  let rows;
  try {
    rows = decompressAll(fs.readFileSync(file)).split('\n').filter(Boolean).map((l) => JSON.parse(l));
  } catch (e) {
    return [`无法解压/解析：${e.message}`];
  }
  const header = rows.shift();
  if (!header || header.type !== 'session') problems.push('缺少 session header');
  if (header && header.version !== 0) problems.push(`header.version=${header.version}，期望 0`);

  rows.forEach((ev, i) => {
    const tag = `seq=${ev.seq} ${ev.type}`;
    if (ev.seq !== i) problems.push(`${tag}: seq 不连续（期望 ${i}）`);
    for (const k of Object.keys(ev)) if (!TOP.has(k)) problems.push(`${tag}: 多余的顶层成员 ${k}`);
    if (!rules) return;
    const disp = rules.RELEASED_V0_EVENT_DISPOSITIONS[ev.type];
    if (!disp) { problems.push(`${tag}: v0 不认识的类型`); return; }
    const data = ev.data && typeof ev.data === 'object' ? ev.data : {};
    for (const k of disp.required) if (!(k in data)) problems.push(`${tag}: 缺少成员 ${k}`);
    for (const k of Object.keys(data)) {
      if (!disp.required.includes(k) && !disp.optional.includes(k)) problems.push(`${tag}: 不允许的成员 ${k}`);
    }
    try {
      rules.assertReleasedPayloadSemantics(ev, 0);
    } catch (e) {
      problems.push(`${tag}: ${e.message}`);
    }
  });

  if (rules) {
    try {
      rules.assertReleasedArtifactRelationships({ header, events: rows });
    } catch (e) {
      problems.push('关系校验: ' + e.message);
    }
    for (const ev of rows) {
      if (!ev.surfaceOp) continue;
      try {
        rules.assertReleasedSurfaceMetadata(ev, ev.seq, ev.type, 'allow-empty-assistant');
      } catch (e) {
        problems.push(`surface seq=${ev.seq} ${ev.type}: ${e.message}`);
      }
    }
  }
  return problems;
}

const info = JSON.parse(fs.readFileSync(IMPORTED, 'utf8'));
let bad = 0;
for (const c of info.created) {
  const file = path.join(c.dir, 'session.jsonl.zstd');
  if (!fs.existsSync(file)) { bad++; console.log(`\n✗ 文件不存在 ${file}`); continue; }
  const problems = checkFile(file);
  if (problems.length) {
    bad++;
    console.log(`\n✗ ${path.basename(c.dir)}  ${c.cwd}`);
    for (const p of problems.slice(0, 8)) console.log('   ' + p);
    if (problems.length > 8) console.log(`   … 共 ${problems.length} 条`);
  }
}
console.log(bad ? `\n${bad}/${info.created.length} 个会话有问题` : `\n全部 ${info.created.length} 个会话通过校验`);
process.exit(bad ? 1 : 0);
