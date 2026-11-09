#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Register the archive folders as DSH workspaces (writes ~/.dsh/storages/workspace.json).

- backs up workspace.json before writing
- skips folders that are already registered
- requires a DSH restart afterwards: the running host keeps workspaces in memory

Usage:
  python register_dsh_workspaces.py --result export_result.json [--dsh-home ~/.dsh] [--dry-run]
"""
import argparse
import collections
import datetime
import json
import os
import shutil
import sys
import uuid


def workspace_of(dest: str, chat_dir: str) -> str:
    parent = os.path.dirname(dest)
    if os.path.basename(parent) == chat_dir:
        return os.path.dirname(parent)          # <workspace>\<chat-dir>\<project>
    return parent                               # <project workspace>\<chat-dir>


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="export_result.json")
    ap.add_argument("--dsh-home", default=os.path.join(os.path.expanduser("~"), ".dsh"))
    ap.add_argument("--chat-dir", default="chatgpt对话")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.result, encoding="utf-8") as f:
        written = json.load(f)["written"]

    targets = collections.OrderedDict()
    for w in written:
        ws = workspace_of(w["dest"], args.chat_dir)
        targets.setdefault(ws.lower(), (ws, set()))[1].add(w.get("project") or os.path.basename(ws))

    store = os.path.join(args.dsh_home, "storages", "workspace.json")
    with open(store, encoding="utf-8") as f:
        data = json.load(f)

    rows = data["tables"]["workspaces"]
    existing = {v["path"].lower() for v in rows.values()}
    ids = data["global"]["workspaceIds"]
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    added = []
    for _, (path, projs) in targets.items():
        if path.lower() in existing:
            print(f"[skip] 已登记：{path}")
            continue
        if not os.path.isdir(path):
            print(f"[warn] 目录不存在，跳过：{path}")
            continue
        title = "、".join(sorted(p for p in projs if p))
        wid = str(uuid.uuid4())
        rows[wid] = {"path": path, "title": title, "sessionIds": [],
                     "createdAt": now, "updatedAt": now}
        ids.append(wid)
        added.append((wid, path, title))

    print(f"新增 {len(added)} 个工作区：")
    for wid, path, title in added:
        print(f"  {wid}  {title}  {path}")

    if args.dry_run or not added:
        print("[dry-run] 未写入" if args.dry_run else "没有变化")
        return

    bak = store + ".bak." + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(store, bak)
    tmp = store + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, store)
    print("备份:", bak)
    print("已写入", store)
    print("提示：重启 DSH 后新工作区才会出现在侧边栏。")


if __name__ == "__main__":
    main()
