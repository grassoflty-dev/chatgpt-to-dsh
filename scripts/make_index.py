#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild the per-folder index files from export_result.json, plus a master index.

Usage:
  python make_index.py --result export_result.json --chat-dir "chatgpt对话" --master 索引总表.md
"""
import argparse
import collections
import datetime
import json
import os


def label_of(dest, items, chat_dir):
    projs = sorted({(w.get("project") or "未分类") for w in items})
    parent = os.path.dirname(dest)
    if os.path.basename(parent) == chat_dir:
        return f"{projs[0]}（当前工作区）"
    grand = os.path.basename(os.path.dirname(parent))
    return f"{grand}\\{os.path.basename(parent)}（{', '.join(projs)}）"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="export_result.json")
    ap.add_argument("--chat-dir", default="chatgpt对话")
    ap.add_argument("--master", default="索引总表.md")
    ap.add_argument("--master-title", default="ChatGPT 对话整理总索引")
    args = ap.parse_args()

    with open(args.result, encoding="utf-8") as f:
        data = json.load(f)
    written, dropped = data["written"], data.get("dropped", [])

    by_dest = collections.OrderedDict()
    for w in written:
        by_dest.setdefault(w["dest"], []).append(w)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    for dest, items in by_dest.items():
        items.sort(key=lambda x: os.path.basename(x["file"]))
        lines = [
            f"# ChatGPT 对话归档 — {label_of(dest, items, args.chat_dir)}",
            "",
            f"- 归档目录：`{dest}`",
            f"- 对话数量：{len(items)}",
            f"- 生成时间：{now}",
            "",
            "| 日期 | 标题 | 原工作区 | 文件 |",
            "|---|---|---|---|",
        ]
        for w in items:
            name = os.path.basename(w["file"])
            lines.append(f"| {name[:8]} | {w['title'].replace('|', chr(92) + '|')[:56]} | "
                         f"`{w['cwd']}` | [{name}]({name}) |")
        lines += ["", "---", "",
                  "> 由 ChatGPT/Codex 本地会话导出：用户与助手正文完整，工具调用/命令输出仅保留摘要。", ""]
        with open(os.path.join(dest, "索引.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        print("wrote", os.path.join(dest, "索引.md"))

    lines = [
        f"# {args.master_title}",
        "",
        f"- 导出时间：{now}",
        f"- 导出会话：{len(written)} 条；排除：{len(dropped)} 条（产品内部线程）",
        "",
        "## 按工作区分类",
        "",
    ]
    for dest, items in by_dest.items():
        lines += [f"### {label_of(dest, items, args.chat_dir)}（{len(items)} 条）", "",
                  f"归档目录：`{dest}`", "",
                  "| 日期 | 标题 | 项目 | 文件 |", "|---|---|---|---|"]
        for w in sorted(items, key=lambda x: os.path.basename(x["file"])):
            name = os.path.basename(w["file"])
            lines.append(f"| {name[:8]} | {w['title'].replace('|', chr(92) + '|')[:56]} | "
                         f"{w.get('project') or '—'} | [{name}]({dest}\\{name}) |")
        lines.append("")

    if dropped:
        lines += ["## 排除的会话（产品内部线程）", "",
                  f"共 {len(dropped)} 条，均为 ChatGPT/Codex 内部线程，不属于用户对话：", "",
                  "| 类型 | 数量 |", "|---|---|"]
        for k, v in collections.Counter(d.get("reason", "其他") for d in dropped).items():
            lines.append(f"| {k} | {v} |")
        lines.append("")

    with open(args.master, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print("wrote", args.master)


if __name__ == "__main__":
    main()
