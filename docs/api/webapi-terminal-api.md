# Terminal WebAPI (/spcode/terminal/*)

> Author: elecvoid243 · 2026-09-01
> 供 GitDiffSidebar「终端」子页面消费;鉴权与全部 spcode 端点一致
> (require_plugin_scope,登录 Dashboard 即可)。

## 概念

- 每个 `umo`(会话)最多同时存在 **1 个** 终端会话(单会话模型)。
- 终端会话 = 持久交互式 shell 进程(cmd.exe /Q 或 PowerShell
  `-NoLogo -NoProfile`),输出**落盘**到 AstrBot 临时目录,按
  `cursor`(字节偏移)增量读取。
- 传输:输出走 **SSE**(`GET /spcode/terminal/stream`),输入走 POST。
- 输入回显由前端 xterm 本地完成(后端管道无 TTY)。

## 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/spcode/terminal/start` | 启动(或替换)会话 |
| GET | `/spcode/terminal/stream` | SSE 输出流 |
| POST | `/spcode/terminal/input` | 写入输入 |
| POST | `/spcode/terminal/interrupt` | 发送中断(Ctrl+C 语义) |
| POST | `/spcode/terminal/stop` | 终止进程树 |
| GET | `/spcode/terminal/status` | 状态 + 输出快照(重连/恢复) |

所有响应信封:`{"status": "ok", "data": {...}}`;错误为 200 +
`data.error`(错误码)+ `data.reason`(人类可读原因)。

错误码:`missing_umo` / `missing_cwd` / `missing_session_id` /
`missing_chars` / `start_failed` / `session_not_found` / `no_session`。

### POST /spcode/terminal/start

请求体:

```json
{"umo": "webchat:FriendMessage:...", "shell": "powershell", "cwd": "F:/repo"}
```

- `shell`: `"powershell"`(默认,pwsh7 优先,回退 Windows PowerShell 5.1)
  或 `"cmd"`。
- `cwd`: 绝对路径(前端传当前加载项目根/选中 worktree 根)。
- 若该 umo 已有存活会话,旧会话会被终止并替换。

响应 `data`: `{session_id, pid, shell, cwd, status}`。

### GET /spcode/terminal/stream

Query:`?umo=...&session_id=...&cursor=<字节偏移,默认 0>`

SSE 事件(均为 `data: <json>`,紧凑 JSON):

- `{"type":"output","data":"<文本块>"}` — 增量输出,按序拼接
- `{"type":"exit","data":{"code":<int|null>,"status":"completed|failed|terminated"}}`
   — 进程自然退出,流结束
- `{"type":"error","data":"<原因>"}` — 会话不存在/未初始化,流结束
- 心跳注释行 `: hb`(约 1s 间隔,防代理断连)

语义说明:

- `exit` 事件仅在**进程自然退出**时发出(如用户输入 `exit` 或 Ctrl+C
  后进程退出)。
- `stop` 端点会**立即清理**会话;对已清理会话打开的流会收到
  `error` 事件(而非 `exit`)。

### POST /spcode/terminal/input

请求体:`{"umo": "...", "session_id": "...", "chars": "git status\n"}`

- `chars` **原样**写入 stdin(不自动补换行;前端把 `\r` 转为 `\n`)。

### POST /spcode/terminal/interrupt

请求体:`{"umo": "...", "session_id": "..."}`
Windows 发送 `CTRL_BREAK_EVENT`(进程组);POSIX 发送进程组 SIGINT。

### POST /spcode/terminal/stop

请求体:`{"umo": "...", "session_id": "..."}`
终止整棵进程树(Windows `taskkill /F /T`,POSIX SIGTERM→SIGKILL),
随后会话被清理。

### GET /spcode/terminal/status

Query:`?umo=...`(无 session_id = 查询该 umo 的现行会话);可选
`&session_id=...`。

响应 `data` 为 `poll()` 快照:`{session_id, pid, status, stdout,
exit_code, cursor, has_more}`;快照不推进流消费游标(advance=False)。
无会话时 `data.error = "no_session"`。

### 启动时序说明

Windows 上子进程 spawn 后,进程需要约 0.3s 建立 stdin 管道读取;
`start` 端点已在返回前等待该窗口(与 AstrBot 核心 shell boot 的
0.3s 约定一致),因此返回后立即 `input` 不会丢字节。

## 限制(V1)

- 无 PTY:行编辑(Backspace/方向键)、Tab 补全、交互式 TUI(vim/分页器)
  不可用;`git log` 等建议 `--no-pager`。
- 输入回显由前端完成;shell 自身不产生 echo。
- 会话随进程自然退出或 `stop`/插件卸载而清理;页面关闭不会杀会话,
  重开页面由 `status` 恢复。
- `python -i` 等第三方 REPL 可用(通过 stdin 驱动)。
