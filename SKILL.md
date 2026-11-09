---
name: chatgpt-to-dsh
description: Import ChatGPT/Codex desktop conversations into DeepSeek Harness — export every local thread from the Codex SQLite stores to Markdown, file them under each project's workspace folder, register DSH workspaces, and generate native DSH session logs so the conversations show up in the sidebar.
whenToUse: Use when the user asks to migrate, archive, classify, or organize their ChatGPT/Codex conversation history into DSH workspaces, or asks why a migrated conversation does not appear as a DSH session.
metadata:
  version: 1.0.0
  author: Dinghr
  license: MIT
  repository: https://github.com/grassoflty-dev/chatgpt-to-dsh
---

# ChatGPT / Codex → DSH 迁移

把 ChatGPT 桌面端（Codex）本地会话完整搬到 DSH：**文件归档 + 工作区 + 原生会话**三件套。

脚本都在本 skill 的 `scripts/` 下，用参数传路径，不要改脚本里的常量。

## 前置条件

- Windows（脚本按 Windows 路径写的；Linux/macOS 需自行替换路径分隔符与规范化规则）
- Python 3.9+（只读 sqlite）
- Node.js ≥ 22（用到 `node:sqlite` 与 `zlib` 的 zstd）
- ChatGPT/Codex 桌面端的本地数据目录：`~/.codex`（含 `state_*.sqlite`、`thread_history_*.sqlite`）

## 流程

### 0. 先探一眼数据

```bash
python scripts/probe.py --codex-home ~/.codex --out ./probe_report.json
```

输出项目数、线程数、真实用户会话数（自动排除 `guardian` 内部审查线程和 `thread_spawn` 子代理线程）。

### 1. 导出 Markdown 归档

```bash
python scripts/export.py \
  --codex-home ~/.codex \
  --workspace  "<当前 DSH 工作区绝对路径>" \
  --dev-base   "D:\B-ControlProjects" \
  --chat-dir   "chatgpt对话" \
  --out        "<结果 json 目录>"
```

- 项目在 `--dev-base` 下有本地目录 → 归档到 `<项目目录>\chatgpt对话\`
- 纯 chat 项目（没有本地目录）→ 归档到 `<workspace>\chatgpt对话\<项目名>\`
- 文件名 `YYYYMMDD_<标题>_<会话ID前8位>.md`，正文是用户/助手全文，工具调用折叠成 `<details>` 摘要
- 先加 `--dry-run` 看分类计划，确认后再真跑
- 产物：`export_result.json`（写了哪些文件、属于哪个工作区）、`classify_report.json`（含被排除线程）

### 2. 生成索引

```bash
python scripts/make_index.py --result export_result.json --chat-dir "chatgpt对话" --master 索引总表.md
```

每个归档目录写一份 `索引.md`，另生成总索引。

### 3. 登记 DSH 工作区

```bash
python scripts/register_dsh_workspaces.py --result export_result.json --dsh-home ~/.dsh
```

写 `~/.dsh/storages/workspace.json`（自动备份 `.bak.<时间戳>`），已存在同路径工作区则跳过。

### 4. 把对话写成 DSH 原生会话

```bash
node scripts/import_sessions.js --export-result export_result.json --dsh-home ~/.dsh
```

写 `~/.dsh/sessions/--<规范化cwd>--/session-<uuid>/session.jsonl.zstd`，并把 sessionId 挂到对应工作区。
**必须先跑第 3 步**：会话要挂到一个已登记的工作区路径上。

### 5. 离线校验（一定要跑）

```bash
node scripts/validate_sessions.js --imported imported_sessions.json --dsh-home ~/.dsh
```

脚本会自动找 `~/.dsh/profiles/**/dsh-session-format-v0-to-v1`，用 DSH 自己的 v0 规则检查：
顶层成员白名单、`data` 必填成员、嵌套载荷语义、跨事件关系、surface 元数据。
**exit code 非 0 就不要让用户重启**，先按报错补字段。

### 6. 让用户重启 DSH

工作区与会话挂载都在 host 内存里，`workspace.json` 改了不会热加载；浏览器刷新没用，**要重启应用**。
重启后：侧边栏出现工作区与会话，文件树里能看到 `chatgpt对话\<...>.md`。

## 回滚

```bash
node scripts/rollback_import.js --yes --imported imported_sessions.json --dsh-home ~/.dsh
```

删除导入的会话目录、从 `workspace.json` 摘掉 sessionId（先备份）。重启后生效。

## 必须知道的坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 侧边栏「未命名」、红字 `assistant/message N message lacks required member "id"` | v0 的 `assistant/message.data.message` 按成员精确校验，必须 `role/content/source/id` | 补 `id`(uuid) 与 `source:{kind:"model",provider,model}`；用第 5 步校验器先验证 |
| 工作区有了但里面什么都没有 | 工作区 = 目录 + `sessionIds`，列表空就是空 | 跑第 4 步导入会话 |
| 文件树看不到归档目录 | 目录名以 `.` 开头会被隐藏 | `--chat-dir` 不要用点开头，例如 `chatgpt对话` |
| `workspace.json` 改动又消失了 | 存储是内存为准的 single-layout，运行中的 host 会覆盖 | 改完立刻重启 DSH；必要时重跑第 3/4 步 |
| 读会话日志只读到 header | zstd 是**分帧**拼接，`zstdDecompressSync` 只解第一帧 | 按魔数 `28 B5 2F FD` 切帧逐帧解 |

## 不要做的事

- 不要写 `tool/result`、`request/header` 之类没把握的事件：v0 对成员与关系做严格校验，宁可只导入用户/助手文本
- 不要改 `~/.codex` 下的 sqlite：全程 `mode=ro` 只读
- 不要声称「社区有现成迁移插件」：npm 上 `dsh-codex-subscription`、`dsh-hooks-codex`、`dsh-subagent-codex` 都只做认证 / hooks 桥接 / 子代理，没有历史会话导入
