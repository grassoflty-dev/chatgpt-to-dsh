#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export ChatGPT/Codex desktop conversations to Markdown, filed under each project's workspace folder.

Source (read only):
  <codex-home>/state_*.sqlite           projects, project_roots, threads
  <codex-home>/thread_history_*.sqlite  thread_items (conversation body)

Destination:
  <project workspace>\\<chat-dir>\\<YYYYMMDD>_<title>_<id8>.md      when the project has a local folder
  <workspace>\\<chat-dir>\\<project name>\\...                      otherwise (chat-only project)

Outputs:
  export_result.json     every written file + the workspace it belongs to
  classify_report.json   classification plan and the excluded internal threads
"""
import argparse
import datetime
import glob
import json
import os
import re
import sqlite3
import sys

TZ_OFFSET = 8            # epoch seconds are UTC; render local time as UTC+8
MAX_CMD = 600            # truncation limits for tool summaries
MAX_OUT = 2500
MAX_DIFF = 3000
MAX_MISC = 800

URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


# ---------------------------------------------------------------- helpers
def find_db(codex_home: str, prefix: str) -> str:
    cands = []
    for path in glob.glob(os.path.join(codex_home, f"{prefix}_*.sqlite")):
        num = os.path.basename(path)[len(prefix) + 1:].split(".")[0]
        if num.isdigit():
            cands.append((int(num), path))
    if not cands:
        raise SystemExit(f"no {prefix}_*.sqlite under {codex_home}")
    return max(cands)[1]


def ts(v) -> str:
    if not v:
        return ""
    if v > 10 ** 12:
        v = v / 1000
    dt = datetime.datetime.fromtimestamp(v, datetime.timezone.utc) + datetime.timedelta(hours=TZ_OFFSET)
    return dt.strftime("%Y-%m-%d %H:%M")


def norm(p: str) -> str:
    return (p or "").replace("\\\\?\\", "").rstrip("\\").lower()


def clean_path(p: str) -> str:
    return (p or "").replace("\\\\?\\", "")


def clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n…（已截断，原长 {len(s)} 字符）"


def safe_name(s: str, limit: int = 48) -> str:
    s = MD_LINK_RE.sub(r"\1", s or "")
    s = URL_RE.sub(" ", s)
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].strip() or "会话"


def strip_system_prefix(s: str) -> str:
    """Drop product-injected blocks that precede the real user prompt."""
    s = (s or "").strip()
    if s.startswith("## Referenced ChatGPT conversation"):
        i = s.find("\n{")
        if i >= 0:
            j = s.find("\n", i + 1)
            s = s[j + 1:] if j >= 0 else ""
    if "## My request:" in s:
        s = s.split("## My request:", 1)[1]
    if s.lstrip().startswith("# Files mentioned by the user"):
        return ""
    return re.sub(r"\s+", " ", s).strip()


def prep_title(s: str) -> str:
    s = strip_system_prefix(s)
    s = MD_LINK_RE.sub(r"\1", s)
    s = URL_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip(" -—,，")


# ---------------------------------------------------------------- data
def load_state(codex_home: str):
    con = sqlite3.connect(f"file:{find_db(codex_home, 'state')}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    projects = []
    for p in cur.execute("select id,name from projects order by position").fetchall():
        roots = [r["path"] for r in cur.execute(
            "select path from project_roots where project_id=? order by position", (p["id"],)).fetchall()]
        projects.append({"id": p["id"], "name": p["name"], "roots": roots})

    threads = [dict(t) for t in cur.execute(
        "select id,project_id,title,first_user_message,preview,cwd,created_at,updated_at,"
        "archived,source,model from threads order by created_at")]
    con.close()
    return projects, threads


def load_items(hist_db: str, thread_id: str):
    con = sqlite3.connect(f"file:{hist_db}?mode=ro", uri=True)
    rows = con.execute("select item_json from thread_items where thread_id=? order by rollout_ordinal",
                       (thread_id,)).fetchall()
    con.close()
    out = []
    for r in rows:
        try:
            out.append(json.loads(r[0]))
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- classify
def classify(projects, threads, workspace: str, dev_base: str, chat_dir: str):
    proj_by_root = {}
    for p in projects:
        for root in p["roots"]:
            proj_by_root[norm(root)] = p

    base_norm = norm(dev_base) if dev_base else ""
    keep, dropped = [], []

    for t in threads:
        src = t["source"] or ""
        cwd = norm(t["cwd"])
        proj = proj_by_root.get(cwd)
        if not proj:
            m = re.search(r"documents\\chatgpt\\([^\\]+)$", cwd)
            if m:
                proj = {"id": None, "name": m.group(1), "roots": [t["cwd"]]}

        if "guardian" in src:
            dropped.append({"id": t["id"], "title": t["title"], "cwd": t["cwd"],
                            "reason": "内部自动审查线程（Guardian review）"})
            continue
        if "thread_spawn" in src:
            dropped.append({"id": t["id"], "title": t["title"], "cwd": t["cwd"],
                            "reason": "子代理线程（thread_spawn）"})
            continue

        local_root = None
        if base_norm:
            cands = [r for r in (proj["roots"] if proj else [])
                     if norm(r) == base_norm or norm(r).startswith(base_norm + "\\")]
            if cands:
                local_root = cands[0]
            elif proj:
                guess = os.path.join(dev_base, proj["name"], "GitProject")
                if os.path.isdir(guess):
                    local_root = guess
            if not local_root and cwd.startswith(base_norm):
                orig = clean_path(t["cwd"])
                if os.path.isdir(orig):
                    local_root = orig

        if local_root:
            dest = os.path.join(local_root, chat_dir)
            kind = "项目工作区"
        else:
            dest = os.path.join(workspace, chat_dir, proj["name"] if proj else "未分类")
            kind = "当前工作区（纯 chat 项目）"

        keep.append({"thread": t, "project": proj, "dest": dest, "kind": kind})
    return keep, dropped


# ---------------------------------------------------------------- render
def detail(title: str, body: str) -> str:
    return f"<details><summary>{title}</summary>\n\n{body}\n\n</details>\n"


def render_item(it: dict):
    typ = it.get("type")

    if typ == "userMessage":
        parts = []
        for c in it.get("content") or []:
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif "image" in (c.get("type") or "").lower():
                parts.append("（用户附了图片）")
        txt = "\n".join(p for p in parts if p).strip()
        return f"### 👤 用户\n\n{txt}\n" if txt else None

    if typ == "agentMessage":
        txt = (it.get("text") or "").strip()
        if not txt:
            return None
        tag = "（中间说明）" if (it.get("phase") or "") == "commentary" else ""
        return f"### 🤖 助手{tag}\n\n{txt}\n"

    if typ == "reasoning":
        body = "\n".join(x for x in (it.get("summary") or []) if x) or \
               "\n".join(x.get("text", "") for x in (it.get("content") or []) if isinstance(x, dict))
        return detail("🧠 思考摘要", clip(body.strip(), MAX_MISC)) if body.strip() else None

    if typ == "commandExecution":
        body = f"```powershell\n{clip(it.get('command') or '', MAX_CMD)}\n```"
        out = clip(it.get("aggregatedOutput") or "", MAX_OUT)
        if out:
            body += f"\n\n输出：\n\n```\n{out}\n```"
        return detail(f"🛠 执行命令（{it.get('status') or ''}）", body)

    if typ == "fileChange":
        ch = it.get("changes") or []
        body = "\n".join(f"- `{c.get('path')}`（{(c.get('kind') or {}).get('type', '')}）" for c in ch)
        diff = "\n".join(c.get("diff") or "" for c in ch)
        if diff:
            body += f"\n\n```diff\n{clip(diff, MAX_DIFF)}\n```"
        return detail("📝 修改文件", body)

    if typ == "webSearch":
        return detail("🌐 联网检索", f"- 查询：{it.get('query') or ''}\n"
                                    f"- 动作：{(it.get('action') or {}).get('type', '')}")

    if typ == "mcpToolCall":
        args = json.dumps(it.get("arguments") or {}, ensure_ascii=False)
        res = json.dumps(it.get("result") or {}, ensure_ascii=False)
        return detail(f"🔧 工具调用 {it.get('server')}/{it.get('tool')}（{it.get('status')}）",
                      f"参数：\n```json\n{clip(args, MAX_MISC)}\n```\n\n结果：\n```json\n{clip(res, MAX_MISC)}\n```")

    if typ in ("subAgentActivity", "collabAgentToolCall"):
        return detail("🤝 子代理活动", clip(json.dumps(it, ensure_ascii=False), MAX_MISC))

    if typ == "contextCompaction":
        return "> （上下文压缩点）\n"
    return None


def display_title(t: dict) -> str:
    for cand in (t.get("title"), t.get("first_user_message"), t.get("preview")):
        s = prep_title(cand)
        if s and s != "Guardian review":
            return s
    return "会话"


def thread_title(t: dict, items) -> str:
    title = display_title(t)
    if title != "会话":
        return title
    for it in items:
        if it.get("type") == "userMessage":
            txt = "".join(c.get("text", "") for c in (it.get("content") or []) if c.get("type") == "text")
            cand = prep_title(txt)
            if cand:
                return cand
    return title


def render_thread(t, items, title, proj, dest) -> str:
    head = [
        f"# {title}",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 会话 ID | `{t['id']}` |",
        f"| ChatGPT 项目 | {proj['name'] if proj else '—'} |",
        f"| 原工作区 | `{clean_path(t['cwd'])}` |",
        f"| 归档位置 | `{dest}` |",
        f"| 创建时间 | {ts(t['created_at'])} |",
        f"| 最后更新 | {ts(t['updated_at'])} |",
        f"| 来源 | {(t['source'] or '')[:60]} |",
        f"| 模型 | {t.get('model') or '—'} |",
        f"| 是否归档 | {'是' if t['archived'] else '否'} |",
        f"| 记录条数 | {len(items)} |",
        "",
        "> 由 ChatGPT/Codex 本地会话导出；用户与助手正文完整保留，工具调用/输出仅保留摘要。",
        "",
        "---",
        "",
    ]
    body = [r for r in (render_item(it) for it in items) if r]
    return "\n".join(head) + "\n".join(body) + "\n"


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codex-home", default=os.path.join(os.path.expanduser("~"), ".codex"))
    ap.add_argument("--workspace", default=os.getcwd(), help="DSH workspace that receives chat-only projects")
    ap.add_argument("--dev-base", default="", help=r"root holding local project folders, e.g. D:\B-ControlProjects")
    ap.add_argument("--chat-dir", default="chatgpt对话")
    ap.add_argument("--out", default=os.getcwd(), help="where export_result.json / classify_report.json go")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    hist_db = find_db(args.codex_home, "thread_history")
    projects, threads = load_state(args.codex_home)
    keep, dropped = classify(projects, threads, args.workspace, args.dev_base, args.chat_dir)

    plan, titles = [], {}
    for e in keep:
        t = e["thread"]
        items = load_items(hist_db, t["id"])
        title = thread_title(t, items)
        titles[t["id"]] = (items, title)
        day = ts(t["created_at"])[:10].replace("-", "") or "00000000"
        fname = f"{day}_{safe_name(title)}_{t['id'][:8]}.md"
        plan.append({"id": t["id"], "title": title[:80], "cwd": clean_path(t["cwd"]),
                     "project": e["project"]["name"] if e["project"] else None,
                     "kind": e["kind"], "dest": e["dest"],
                     "file": os.path.join(e["dest"], fname), "archived": bool(t["archived"]),
                     "items": len(items)})

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "classify_report.json"), "w", encoding="utf-8") as f:
        json.dump({"plan": plan, "dropped": dropped}, f, ensure_ascii=False, indent=2)

    if args.dry_run:
        print(f"[dry-run] {len(plan)} conversations, {len(dropped)} excluded")
        for p in plan:
            print(f"  {p['file']}")
        return

    written = []
    for e, p in zip(keep, plan):
        items, title = titles[e["thread"]["id"]]
        os.makedirs(p["dest"], exist_ok=True)
        text = render_thread(e["thread"], items, title, e["project"], p["dest"])
        with open(p["file"], "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        written.append({**p, "size": os.path.getsize(p["file"])})

    with open(os.path.join(args.out, "export_result.json"), "w", encoding="utf-8") as f:
        json.dump({"written": written, "dropped": dropped}, f, ensure_ascii=False, indent=2)
    print(f"[ok] exported {len(written)}, excluded {len(dropped)}")
    for w in written:
        print(f"  {w['size']:>9}  {w['file']}")


if __name__ == "__main__":
    main()
