# 外部程序启动检查

检查版本：v2.33.1，2026-11-04，Windows x64。

## 运行包依赖

`scripts/_runtime_patches.py` 在构建运行包时直接修补锁定 wheel 的源码，
给以下调用传入 Windows 专用的 `creationflags=subprocess.CREATE_NO_WINDOW`。
其他平台展开空 kwargs。补丁不改变启动参数、管道或进程清理方式，
也不会屏蔽 Vivado 自身的 GUI。

| 依赖 | 启动位置 | 修补数量 |
| --- | --- | ---: |
| vivado-mcp 0.3.27 | TCL 会话、GUI 启动器、两处 taskkill、iverilog/verilator 编译检查 | 5 |
| ruff 0.17.3 | `python -m ruff` 包装入口 | 1 |
| clang-format 23.1.3 | 包装入口、clang-format-diff、git-clang-format | 14 |
| cppcheck wheel 1.5.3 | 包装入口、版本查询、addon 及 HTML 报告的 Python 辅助脚本 | 4 |
| MCP SDK 2.3.0 | Windows stdio 的 anyio 与 Popen 回退启动 | 原本已带标志 |
| cpplint、crap4py、lizard | 插件现有调用使用进程内 Python 分析 | 无额外启动 |
| python-ripgrep | 原生 Rust 扩展在工作进程内搜索 | 无独立 rg 进程 |
| Send2Trash、pywin32 | 回收站操作使用 Windows API / COM | 无额外启动 |

补丁清单固定检查上述 24 个启动位置；上游新增或删除调用、改变显式
`creationflags` 时中止构建，要求重新审查。保留上游注释，支持重复应用。
`runtime/manifest.json` 的 `source_patches` 记录补丁 ID、修改文件与 SHA-256。
重新构建和离线安装均携带补丁，不依赖启动时的全局 monkey patch。

## 插件调用入口

插件直接启动的工作进程、ruff、clang-format、cppcheck、Git、es.exe、npm
和 codegraph CLI 均已有 Windows 无控制台标志。构建器的 curl、Python 和
原生工具探测也有该标志。MCP 服务器的首层启动由 AstrBot 的 MCP 客户端负责。

`inta_shell` 使用 `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`。
`terminal` 有意使用 `CREATE_NEW_CONSOLE + STARTF_USESHOWWINDOW/SW_HIDE`：
它需要一个专用隐藏控制台，以支持 PowerShell 5.1 的标题与中断行为。
不能机械叠加 `CREATE_NO_WINDOW`；Windows 在与 `CREATE_NEW_CONSOLE`
组合时会忽略该标志。参见 [Microsoft 进程创建标志文档](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)。

当前检查的 AstrBot 宿主 MCP SDK 1.27.0 正常路径和 SelectorEventLoop 回退路径已有
无控制台标志，但它的通用异常重试分支会移除标志。该宿主包不在插件运行包中，
本次没有修改；运行包自带的 MCP 2.3.0 没有这种无标志重试。
本次检查覆盖可读取的 Python 启动位置，不修改 Vivado、codegraph 等原生程序
内部自行启动其他程序的实现。

## 验证

- `tests/test_no_window_kwargs.py` 检查插件与构建器的启动位置。
- `tests/test_runtime_patches.py` 验证源码修补、幂等性、跨平台 kwargs、上游变化阻断。
- `tests/test_tool_runtime_integration.py` 检查运行包源码与补丁散列，并用
  `pythonw.exe` 执行真实依赖方法，拦截实际 subprocess API 检查全部 5 条
  Vivado 路径及 4 个 CLI 包装入口。不会启动真实 Vivado 或执行真实 taskkill。
- pythonw 用例还启动真实命令行 Python 子进程，确认 `GetConsoleWindow() == 0`。
  另有真实格式化、检查、搜索、MCP 初始化与离线目录迁移测试。

安装 v2.33.1 插件源码后，还需更新 `runtime/` 或重新运行构建器；v2.33.0
已经部署的 wheel 不会因插件源码更新而自动改变。
