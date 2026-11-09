#!/usr/bin/env node
/**
 * Import ChatGPT/Codex conversations as native DSH session logs (v0 JSONL + zstd frames),
 * then attach them to the matching DSH workspaces.
 *
 * Requires: Node >= 22 (node:sqlite, zlib zstd) and a prior export.py run.
 *
 * Usage:
 *   node import_sessions.js --export-result export_result.json [--dsh-home ~/.dsh] [--dry-run]
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const zlib = require('zlib');
const crypto = require('crypto');
const { DatabaseSync } = require('node:sqlite');

// ---------------------------------------------------------------- args
function parseArgs(argv) {
  const out = { 'dsh-home': path.join(os.homedir(), '.dsh'), 'codex-home': path.join(os.homedir(), '.codex'),
    'export-result': 'export_result.json', 'chat-dir': 'chatgpt对话', 'dry-run': false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--dry-run') { out['dry-run'] = true; continue; }
    const m = /^--([^=]+)=(.*)$/.exec(a);
    if (m) { out[m[1]] = m[2]; continue; }
    if (a.startsWith('--')) { out[a.slice(2)] = argv[++i]; }
  }
  return out;
}

const args = parseArgs(process.argv);
const DSH_HOME = args['dsh-home'].replace(/^~/, os.homedir());
const CODEX_HOME = args['codex-home'].replace(/^~/, os.homedir());
const CHAT_DIR = args['chat-dir'];
const DRY = args['dry-run'];

// ---------------------------------------------------------------- helpers
function findDb(prefix) {
  const hits = fs.readdirSync(CODEX_HOME)
    .map((n) => [n, new RegExp(`^${prefix}_(\\d+)\\.sqlite$`).exec(n)])
    .filter(([, m]) => m)
    .sort((a, b) => Number(b[1][1]) - Number(a[1][1]));
  if (!hits.length) throw new Error(`no ${prefix}_*.sqlite under ${CODEX_HOME}`);
  return path.join(CODEX_HOME, hits[0][0]);
}

const normCwd = (p) => {
  const s = String(p).replace(/[^A-Za-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return s ? `--${s}--` : '_no-cwd';
};

// archive folder -> workspace root it belongs to
function workspaceOf(dest) {
  const parent = path.dirname(dest);
  if (path.basename(parent) === CHAT_DIR) return path.dirname(parent);
  return parent;
}

function makeResolver(ws) {
  const paths = Object.values(ws.tables.workspaces).map((w) => w.path);
  return (dest) => {
    const lower = dest.toLowerCase();
    let best = null;
    for (const p of paths) {
      const pl = p.toLowerCase();
      if (lower === pl || lower.startsWith(pl + '\\')) {
        if (!best || p.length > best.length) best = p;
      }
    }
    return best || workspaceOf(dest);
  };
}

function textOf(content) {
  if (!Array.isArray(content)) return '';
  return content.map((c) => (c && c.type === 'text' ? c.text : '')).filter(Boolean).join('\n').trim();
}

// ---------------------------------------------------------------- events
function buildEvents(title, messages) {
  const events = [];
  let seq = 0;
  const push = (type, data, extra = {}) => {
    events.push({ type, seq: seq++, time: Date.now(), data, ...extra });
    return seq - 1;
  };

  push('permission/preset', { preset: 'danger-full-access' });
  push('sandbox/mode', { mode: 'danger-full-access' });
  push('approval/policy', { policy: 'never' });

  let turn = 0, step = 0, open = false, firstUserSeq = null;
  const closeTurn = () => {
    if (!open) return;
    push('step/end', { turn, step });
    push('turn/end', { turn, reason: { kind: 'completed' } });
    open = false;
  };

  for (const m of messages) {
    if (m.role === 'user') {
      closeTurn();
      turn += 1; step = 1; open = true;
      push('turn/start', { turn });
      push('step/start', { turn, step });
      const s = push('user/message', {
        content: [{ type: 'text', text: m.text }],
        source: { kind: 'user' },
        role: 'user',
        id: crypto.randomUUID(),
      }, { surfaceOp: 'append' });
      if (firstUserSeq === null) firstUserSeq = s;
    } else {
      if (!open) {
        turn += 1; step = 1; open = true;
        push('turn/start', { turn });
        push('step/start', { turn, step });
      } else {
        push('step/end', { turn, step });
        step += 1;
        push('step/start', { turn, step });
      }
      push('assistant/message', {
        turn,
        step,
        message: {
          role: 'assistant',
          id: crypto.randomUUID(),
          content: [{ type: 'text', text: m.text }],
          source: { kind: 'model', provider: 'chatgpt', model: 'codex-import' },
        },
      }, { surfaceOp: 'append' });
    }
  }
  closeTurn();

  if (firstUserSeq !== null) {
    events.push({
      type: 'session/title', seq: seq++, time: Date.now(),
      data: { title: title.slice(0, 60), messageSeqs: [firstUserSeq], source: { kind: 'fallback' } },
    });
  }
  return events;
}

function writeSession(dir, header, events) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, 'session.jsonl.zstd');
  const frames = [zlib.zstdCompressSync(Buffer.from(JSON.stringify(header) + '\n', 'utf8'))];
  for (const e of events) frames.push(zlib.zstdCompressSync(Buffer.from(JSON.stringify(e) + '\n', 'utf8')));
  fs.writeFileSync(file, Buffer.concat(frames));
  return file;
}

// ---------------------------------------------------------------- main
const result = JSON.parse(fs.readFileSync(args['export-result'], 'utf8'));
const plan = result.written;
const wsPath = path.join(DSH_HOME, 'storages', 'workspace.json');
const ws = JSON.parse(fs.readFileSync(wsPath, 'utf8'));
const resolveWorkspace = makeResolver(ws);

const db = new DatabaseSync(findDb('thread_history'), { readOnly: true });
const stmt = db.prepare('SELECT item_json FROM thread_items WHERE thread_id = ? ORDER BY rollout_ordinal');

const created = [];
for (const item of plan) {
  const rows = stmt.all(item.id).map((r) => JSON.parse(r.item_json));
  const messages = [];
  for (const it of rows) {
    if (it.type === 'userMessage') {
      const t = textOf(it.content);
      if (t) messages.push({ role: 'user', text: t });
    } else if (it.type === 'agentMessage') {
      const t = (it.text || '').trim();
      if (t) messages.push({ role: 'assistant', text: t });
    }
  }
  if (!messages.some((m) => m.role === 'user')) {
    console.log(`[skip] no user message: ${item.id} ${item.title}`);
    continue;
  }

  const cwd = resolveWorkspace(item.dest);
  const sessionId = `session-${crypto.randomUUID()}`;
  const dir = path.join(DSH_HOME, 'sessions', normCwd(cwd), sessionId);
  const header = { type: 'session', version: 0, id: sessionId, createdAt: Date.now(), cwd,
    delegationDepth: 0, agentPreset: 'standard' };
  const events = buildEvents(item.title, messages);

  created.push({ sessionId, dir, cwd, title: item.title, messages: messages.length, events: events.length });
  if (!DRY) writeSession(dir, header, events);
  console.log(`[ok] ${String(messages.length).padStart(3)} msgs  ${cwd}  ${item.title.slice(0, 40)}`);
}

let added = 0;
for (const c of created) {
  const row = Object.values(ws.tables.workspaces).find((w) => w.path.toLowerCase() === c.cwd.toLowerCase());
  if (!row) { console.log(`[warn] no workspace for ${c.cwd}`); continue; }
  if (!row.sessionIds.includes(c.sessionId)) row.sessionIds.push(c.sessionId);
  row.updatedAt = new Date().toISOString();
  added++;
}

if (!DRY) {
  const bak = `${wsPath}.bak.${new Date().toISOString().replace(/[-:T]/g, '').slice(0, 15)}`;
  fs.copyFileSync(wsPath, bak);
  fs.writeFileSync(wsPath, JSON.stringify(ws, null, 2), 'utf8');
  console.log('workspace.json backup:', bak);
}

fs.writeFileSync(path.join(path.dirname(path.resolve(args['export-result'])), 'imported_sessions.json'),
  JSON.stringify({ created }, null, 2), 'utf8');
console.log(`\ndone: ${created.length} sessions, attached to ${added} workspaces${DRY ? ' (dry-run)' : ''}`);
console.log('restart DSH to see them in the sidebar.');
db.close();
