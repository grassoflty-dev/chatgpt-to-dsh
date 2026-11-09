# chatgpt-to-dsh

把 **ChatGPT 桌面端（Codex）的本地会话**迁移进 **DeepSeek Harness (DSH)**：导出成可读 Markdown，按本地项目目录归档，登记成 DSH 工作区，并生成 **DSH 原生会话**，让对话直接出现在侧边栏里。

> 这是一个 [DSH skill](https://github.com/grassoflty-dev/chatgpt-to-dsh)：目录放进 `~/.dsh/skills/` 即被 DSH 发现，模型在相关任务里会自动加载 `SKILL.md`。

```
ChatGPT/Codex 本地数据                  DSH
─────────────────────            ────────────────────────────
~/.codex/state_*.sqlite       ┐
  projects / project_roots    │  export.py
  threads                     ├──────────────►  各项目目录\chatgpt对话\*.md
~/.codex/thread_history_*.sqlite           │      （纯 chat 项目 → 当前工作区）
  thread_items（正文）        ┘
                                          │  import_sessions.js
                                          └──────────────►  ~/.dsh/sessions/**/session.jsonl.zstd
                                                             + ~/.dsh/storages/workspace.json
```

## 它解决什么问题

Codex 桌面端把会话存在本地 sqlite 里，DSH 又没有「导入会话」功能，社区也没有对应插件（`dsh-codex-subscription` / `dsh-hooks-codex` / `dsh-subagent-codex` 分别只做认证、hooks 桥接、子代理）。于是这个工具做三件事：

1. **读**：只读打开 `~/.codex` 下的两个 sqlite，拿到项目、工作区路径、线程元数据和逐条消息。
2. **归档**：每条真实对话导出成一个 Markdown（用户/助手全文 + 工具调用摘要），放进对应项目的 `chatgpt对话\`；没有本地目录的项目落到当前 DSH 工作区。
3. **接入 DSH**：把归档目录登记为工作区，并把每条对话写成 DSH 会话日志，挂到对应工作区。

## 安装

```powershell
# Windows
git clone https://github.com/grassoflty-dev/chatgpt-to-dsh.git "$env:USERPROFILE\.dsh\skills\chatgpt-to-dsh"
```

```bash
# macOS / Linux
git clone https://github.com/grassoflty-dev/chatgpt-to-dsh.git ~/.dsh/skills/chatgpt-to-dsh
```

放好后 DSH 会扫描到（`~/.dsh/skills` 是默认 skill root，改动无需重启）。然后直接对 agent 说：

> 把我 ChatGPT 目录下的所有对话按工作区整理进 DSH

## 手动使用

```powershell
$py = "python"   # 或 DSH 自带 python
$node = "node"   # 需要 >= 22

# 0) 看一眼数据
& $py scripts\probe.py --codex-home ~/.codex --out .\probe_report.json

# 1) 导出 Markdown（--dry-run 先看分类）
& $py scripts\export.py --codex-home ~/.codex `
    --workspace "C:\Users\you\Desktop\dsh-workspace" `
    --dev-base  "D:\B-ControlProjects" `
    --chat-dir  "chatgpt对话" `
    --out       .

# 2) 索引
& $py scripts\make_index.py --result export_result.json --chat-dir "chatgpt对话"

# 3) 登记工作区
& $py scripts\register_dsh_workspaces.py --result export_result.json

# 4) 生成 DSH 原生会话
& $node scripts\import_sessions.js --export-result export_result.json

# 5) 离线校验（务必）
& $node scripts\validate_sessions.js --imported imported_sessions.json

# 6) 重启 DSH
```

回滚：`node scripts\rollback_import.js --yes --imported imported_sessions.json`

## 分类规则

| 情况 | 去向 |
|---|---|
| 项目在 `--dev-base`（如 `D:\B-ControlProjects`）下有同名/已登记的本地目录 | `<项目目录>\chatgpt对话\` |
| 项目只存在于 `Documents\ChatGPT\<名字>`，但 `--dev-base` 下有同名 `GitProject` | `<dev-base>\<名字>\GitProject\chatgpt对话\` |
| 项目是纯 chat（没有本地目录） | `<当前 DSH 工作区>\chatgpt对话\<项目名>\` |

会话去向由「归档目录属于哪个工作区」决定：会话日志的 header `cwd` 就是工作区根，`workspace.json` 的 `sessionIds` 里加上这条会话的 id。

## DSH 集成细节（实测）

### 工作区

`~/.dsh/storages/workspace.json`：

```json
{
  "unit": { "name": "workspace", "version": 2 },
  "global": { "workspaceIds": ["<uuid>", "..."] },
  "tables": {
    "workspaces": {
      "<uuid>": {
        "path": "D:\\B-ControlProjects\\800-20\\GitProject",
        "title": "NBC800S-20",
        "sessionIds": ["session-<uuid>"],
        "createdAt": "2026-11-09T00:00:00.000Z",
        "updatedAt": "2026-11-09T00:00:00.000Z"
      }
    }
  }
}
```

存储后端是 `single` 布局、**内存为准**：运行中的 host 不会回读磁盘，所以改完必须重启 DSH。

### 会话日志

- 路径：`~/.dsh/sessions/--<规范化cwd>--/session-<uuid>/session.jsonl.zstd`
- 规范化 cwd：非字母数字替换为 `-`，两端包 `--`（`D:\a\b` → `--D-a-b--`）
- 物理格式：**多个 zstd 帧拼接**；第一帧只放 header 一行，之后每帧一个事件行

```jsonl
{"type":"session","version":0,"id":"session-...","createdAt":1794183182267,"cwd":"D:\\...","delegationDepth":0,"agentPreset":"standard"}
{"type":"permission/preset","seq":0,"time":...,"data":{"preset":"danger-full-access"}}
{"type":"sandbox/mode","seq":1,"time":...,"data":{"mode":"danger-full-access"}}
{"type":"approval/policy","seq":2,"time":...,"data":{"policy":"never"}}
{"type":"turn/start","seq":3,"time":...,"data":{"turn":1}}
{"type":"step/start","seq":4,"time":...,"data":{"turn":1,"step":1}}
{"type":"user/message","seq":5,"time":...,"data":{"content":[{"type":"text","text":"..."}],"source":{"kind":"user"},"role":"user","id":"<uuid>"},"surfaceOp":"append"}
{"type":"assistant/message","seq":6,"time":...,"data":{"turn":1,"step":1,"message":{"role":"assistant","id":"<uuid>","content":[{"type":"text","text":"..."}],"source":{"kind":"model","provider":"chatgpt","model":"codex-import"}}},"surfaceOp":"append"}
{"type":"step/end","seq":7,"time":...,"data":{"turn":1,"step":1}}
{"type":"turn/end","seq":8,"time":...,"data":{"turn":1,"reason":{"kind":"completed"}}}
{"type":"session/title","seq":9,"time":...,"data":{"title":"...","messageSeqs":[5],"source":{"kind":"fallback"}}}
```

要点：

- 对话渲染靠 **surface 事件**（`user/message`、`assistant/message`、`tool/result`、`system/message`），必须带顶层 `surfaceOp`
- `assistant/message.data.message` 必须齐 `role / content / source / id`，漏一个就被 `dsh-session-format-v0-to-v1` 拒
- `seq` 必须从 0 连续递增
- 不要写需要复杂 provenance 的事件（`assistant/chunk` 等），只导入文本最稳

校验器 `scripts/validate_sessions.js` 直接调用 DSH 自己的 `RELEASED_V0_EVENT_DISPOSITIONS`、`assertReleasedPayloadSemantics`、`assertReleasedArtifactRelationships`、`assertReleasedSurfaceMetadata`，能在重启前发现全部格式问题。

## 已知限制

- 只导入用户/助手**文本**；工具调用、命令输出不在会话里（Markdown 归档中保留摘要）
- 会话里没有 `reasoning` / `usage` / provenance，统计面板不会显示 token 细节
- 重启前如果 DSH host 又写了一次 `workspace.json`，手工登记的工作区可能被覆盖——重跑第 3、4 步即可
- `--dev-base` 缺省为空时，所有项目都会被当成「纯 chat」落到当前工作区

## License

MIT © Dinghr
