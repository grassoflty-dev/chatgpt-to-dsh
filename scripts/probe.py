#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe the local ChatGPT/Codex stores: projects, workspace roots, threads, item counts.

Writes a UTF-8 JSON report next to this run (probe_report.json) and prints a short summary.
"""
import argparse
import glob
import json
import os
import sqlite3
import sys


def find_db(codex_home: str, prefix: str) -> str:
    """state_5.sqlite, thread_history_1.sqlite ... pick the highest numbered one."""
    cands = []
    for path in glob.glob(os.path.join(codex_home, f"{prefix}_*.sqlite")):
        base = os.path.basename(path)
        num = base[len(prefix) + 1: base.rindex(".")]
        if num.isdigit():
            cands.append((int(num), path))
    if not cands:
        raise SystemExit(f"no {prefix}_*.sqlite under {codex_home}")
    return max(cands)[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codex-home", default=os.path.join(os.path.expanduser("~"), ".codex"))
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "probe_report.json"))
    args = ap.parse_args()

    state = find_db(args.codex_home, "state")
    hist = find_db(args.codex_home, "thread_history")

    con = sqlite3.connect(f"file:{state}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    projects = []
    for p in cur.execute("select id,name from projects order by position").fetchall():
        roots = [r["path"] for r in cur.execute(
            "select path from project_roots where project_id=? order by position", (p["id"],)).fetchall()]
        projects.append({"id": p["id"], "name": p["name"], "roots": roots})

    threads = []
    for t in cur.execute(
            "select id,project_id,title,first_user_message,preview,cwd,created_at,updated_at,"
            "archived,source,model from threads order by created_at"):
        threads.append({
            "id": t["id"], "project_id": t["project_id"], "title": t["title"],
            "cwd": (t["cwd"] or "").replace("\\\\?\\", ""), "created_at": t["created_at"],
            "updated_at": t["updated_at"], "archived": t["archived"],
            "source": (t["source"] or "")[:120], "model": t["model"],
        })
    con.close()

    hcon = sqlite3.connect(f"file:{hist}?mode=ro", uri=True)
    counts = {r[0]: r[1] for r in hcon.execute(
        "select thread_id, count(*) from thread_items group by thread_id")}
    hcon.close()
    for t in threads:
        t["items"] = counts.get(t["id"], 0)

    report = {"state_db": state, "history_db": hist, "projects": projects, "threads": threads}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    user_threads = [t for t in threads if "guardian" not in t["source"] and "thread_spawn" not in t["source"]]
    print(f"state db : {state}")
    print(f"history  : {hist}")
    print(f"projects : {len(projects)}")
    print(f"threads  : {len(threads)} (user conversations: {len(user_threads)})")
    print(f"report   : {args.out}")


if __name__ == "__main__":
    main()
