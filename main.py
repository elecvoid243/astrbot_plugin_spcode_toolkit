"""
astrbot_plugin_spcode_toolkit — spcode 精简开发工具箱

为 LLM Agent 提供 7 个核心开发工具：
  - code_check（Python / C·C++ 语法+风格合并检查）
  - codegraph（语义索引 + 调用链追踪）
  - es_search（文件名极速搜索）
  - file_remove（沙箱化删除）
  - file_compare（文件差异比较）
  - todo_list（LLM 自我管理）

参考自 Irmia DevKit 大幅精简。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import StarTools, register  # noqa: F401  (re-export for test compat: tests/test_todo_list.py uses main_mod.StarTools)

from .tools._config_filter import ALL_TOOL_NAMES, filter_enabled_tools
from .tools._codegraph_mcp import (
    SHELL_META_RE,
    build_cli_launcher,
    detect_codegraph_launcher,
    ensure_stdio_allowlist,
    resolve_project_path,
)
# spcode webapi handlers all live in tools/webapi/*; main.py no longer
# re-exports them.  The 6 endpoints are registered in one shot via
# ``register_webapi_routes`` below.  We still import the git-diff
# encoding constant here for the startup-time ``git --version`` probe.
from tools.webapi import register_webapi_routes
from tools.webapi.git_diff import _GIT_DIFF_ENCODING
from .tools import agentsmd as _agentsmd_mod
from .tools.inta_shell.component import LocalInteractiveShellComponent
from .tools._path_safety import is_path_safe as _is_path_safe
# PR-3 (2026-06-23): L1 鉴权 + plan 模式控制器已提取到 tools/security/
from .tools.security import PlanModeController, check_is_admin
# PR-4 (2026-06-23): LLM system_prompt 注入样板集中到 tools/llm_inject.inject_guidance
from .tools.llm_inject import inject_guidance
from .tools._guidance_text import (
    PROJECT_GUIDANCE_MARKER,
    PROJECT_CODEGRAPH_GUIDANCE,
    FILE_REMOVE_GUIDANCE_MARKER,
    FILE_REMOVE_GUIDANCE,
)
# PR-2 (2026-06-23): 13 个 FunctionTool 类 + ALL_TOOL_CLASSES 集中注册表。
# 模块级导入以便 tests/test_*.py 中 `main_mod.TodoCreateTool` / `main_mod.StarTools`
# 等旧用法继续可用(下划线开头的 _common / _TodoToolBase 不在 re-export 列表)。
from .tools.function_tools import (
    ALL_TOOL_CLASSES,
    CodeCheckTool,  # noqa: F401  (re-export for test compat)
    EsSearchTool,  # noqa: F401
    FileDiffTool,  # noqa: F401
    FileRemoveTool,  # noqa: F401
    IntaShellListTool,  # noqa: F401
    IntaShellReadTool,  # noqa: F401
    IntaShellSendTool,  # noqa: F401
    IntaShellStartTool,  # noqa: F401
    IntaShellStopTool,  # noqa: F401
    TodoClearTool,  # noqa: F401
    TodoCreateTool,  # noqa: F401  (used in tests/test_todo_list.py)
    TodoModifyTool,  # noqa: F401
    TodoQueryTool,  # noqa: F401
)
import time as _time
import datetime as _datetime









# inta_shell 组件单例 + 默认 cwd 已迁移到 tools/inta_shell/runtime.py(PR-2 2026-06-23)
# 5 个 IntaShell*Tool 类从 tools.function_tools.* 引用

# 防御性:老版本 AstrBot 可能没有 MCP 异常类
try:
    from astrbot.core.provider.func_tool_manager import (  # type: ignore
        MCPShutdownTimeoutError,
        MCPInitTimeoutError,
    )

    _HAS_MCP_EXCEPTIONS = True
except ImportError:
    _HAS_MCP_EXCEPTIONS = False
    MCPShutdownTimeoutError = None  # type: ignore
    MCPInitTimeoutError = None  # type: ignore

# _stats + _record 已提取到 tools/_stats.py(PR-1 2026-06-23)


_DEFAULT_CONFIG = {
    "es_path": "",  # Everything es.exe 路径（Windows）；Linux/macOS 留空
    "git_path": "",  # git 可执行文件绝对路径;留空走系统 PATH
    "cppcheck_path": "",  # cppcheck.exe 路径（Windows/Linux/macOS）；C/C++ 检查时优先于 cpplint
    "cppcheck_shortcircuit": "error",  # cppcheck 短路策略：error/warning/never（仅 auto 模式生效）
    "codegraph_enabled": True,  # 是否启用 codegraph MCP 集成
    "codegraph_install_dir": "",  # codegraph 安装目录(含 node.exe); 留空则不启动 MCP
    "codegraph_project": "",  # codegraph daemon 默认操作的工程根目录
    "agentsmd_enabled": True,  # 是否启用 AGENTS.md 管理
    # inta_shell 配置(v2.5)
    "inta_shell_max_sessions": 10,  # 最大并发会话数
    "inta_shell_session_timeout": 1800,  # 会话空闲超时(秒)
    "inta_shell_block_unsafe": True,  # 是否阻止危险命令
    "inta_shell_default_cwd": "",  # 默认工作目录
    "allowed_ids": "",  # 逗号分隔额外允许的用户 ID
    "enabled_tools": [],  # 启用的工具名列表；空 = 全部禁用（安全默认）
    "file_remove_blacklist": [],  # file_remove 用户自定义黑名单：绝对路径前缀列表
}

# ALL_TOOL_NAMES 和 filter_enabled_tools 已提取到 tools/_config_filter.py
# 方便单元测试直接 import（避免 main.py 顶层依赖 astrbot.api）


# ── /project 命令组(v2.7) ─────────────────────


class _ProjectLoadAbort(BaseException):
    """私有信号异常 — ``_project_load_step`` 用以中止 ``_project_load_impl``。

    为什么用 ``BaseException`` 而非 ``Exception``?
        子方法(``_agentsmd_init`` 等)和 helpers 内部有大量
        ``except Exception`` 兜底(见 ``_codegraph_set_project`` 等)。
        用 ``BaseException`` 可避免该异常被这些 ``except`` 误吞,
        确保中止信号一定能传到 :meth:`_project_load_impl` 顶层。

    捕获方: :meth:`_project_load_impl` 的 ``try/except _ProjectLoadAbort``
    块,捕获后 ``return`` 即可。
    """

    def __init__(self, step_label: str) -> None:
        self.step_label = step_label
        super().__init__(step_label)


# 注入文本常量已提取到 tools/_guidance_text.py(PR-1 2026-06-23)
# 原 _PROJECT_GUIDANCE_MARKER / _PROJECT_CODEGRAPH_GUIDANCE /
#     _FILE_REMOVE_GUIDANCE_MARKER / _FILE_REMOVE_GUIDANCE 4 个常量。


# 13 个 FunctionTool 类(CodeCheck / EsSearch / FileRemove / FileDiff /
# TodoCreate / TodoQuery / TodoModify / TodoClear / IntaShellStart / IntaShellSend /
# IntaShellRead / IntaShellStop / IntaShellList) + 1 个 _TodoToolBase 已提取到
# tools/function_tools/(PR-2 2026-06-23)。原 main.py 第 153-980 行 ~830 行整体下沉。


# ── 插件入口 ────────────────────────────────────────


# PR-2 (2026-06-23): 工具实例化从硬编码列表改为 ALL_TOOL_CLASSES 迭代。
# 每个类继续用默认参数实例化(无状态差异,保持 v2.5 行为)。
_PLUGINS_TOOLS = [cls() for cls in ALL_TOOL_CLASSES]


@register(
    "astrbot_plugin_spcode_toolkit",
    "elecvoid243",
    ("spcode 开发工具箱 — 提供实用开发工具, 部分实现基于Irmia DevKit插件。"),
    "2.9.0",
)
class SPCodeToolkit(star.Star):
    def __init__(self, context: star.Context, config: dict = None) -> None:
        super().__init__(context)
        self.context = context

        # 合并配置
        _config = dict(_DEFAULT_CONFIG)
        if config:
            # 拍平嵌套分组(如 {codegraph:{install_dir:"..."}} → {codegraph_install_dir:"..."})
            config = self._flatten_config(config)
            for k, v in config.items():
                if v not in (None, "", []):
                    _config[k] = v
        self._config = _config  # 持久化,供异步 bootstrap + terminate 访问

        # inta_shell 配置快照(initialize 前暂存,initialize 中消费)
        self._inta_shell_cfg = {
            "max_sessions": int(_config.get("inta_shell_max_sessions", 10) or 10),
            "session_timeout": int(
                _config.get("inta_shell_session_timeout", 1800) or 1800
            ),
            "block_unsafe": bool(_config.get("inta_shell_block_unsafe", True)),
            "default_cwd": str(_config.get("inta_shell_default_cwd", "") or "").strip(),
        }
        # 注入 es_path 到环境变量供 es_search 读取
        if _config.get("es_path"):
            os.environ["ES_PATH"] = _config["es_path"]
        # 注入 cppcheck_path 到环境变量供 code_check._find_cppcheck 读取
        if _config.get("cppcheck_path"):
            os.environ["CPPCHECK_PATH"] = _config["cppcheck_path"]
        # 注入 cppcheck_shortcircuit 模式供 code_check._run_cppcheck 读取
        os.environ["CPPCHECK_SHORTCIRCUIT"] = _config.get(
            "cppcheck_shortcircuit", "all"
        )

        # codegraph 异步启动的 task 引用 + 并发锁
        self._codegraph_task: asyncio.Task | None = None
        self._codegraph_dir_locks: dict[str, asyncio.Lock] = {}

        # AGENTS.md 加载状态: {umo: {"path": str, "directory": str,
        # "last_content": str, "mtime": float}}
        # 每会话独立管理加载状态(v2.4 合并自 agentsmd 插件)
        self._loaded_agents: dict[str, dict] = {}

        # 已加载项目(per-umo)。/project load 时填充,/project unload 时清空。
        # 与 _loaded_agents 平行——一个跟踪 AGENTS.md,一个跟踪整个项目组合状态。
        # 格式: {umo: {"directory": str, "loaded_at": float}}
        self._loaded_projects: dict[str, dict] = {}

        # PR-3 (2026-06-23): plan 模式状态已提取到 tools.security.plan_mode.PlanModeController
        # 内部维护 self._plan_mode / self._plan_reminded 两个 per-umo dict,
        # 外部通过 self._plan.is_active() / activate() / deactivate() 访问。
        self._plan = PlanModeController(get_config=lambda: self._config)


        # 根据 enabled_tools 配置过滤实际注册的工具
        enabled_names, unknown = filter_enabled_tools(
            ALL_TOOL_NAMES,
            _config.get("enabled_tools") or [],
        )
        if unknown:
            logger.warning(
                f"enabled_tools 中包含未识别的工具名: {sorted(unknown)}，已忽略"
            )
        tools_to_register = [t for t in _PLUGINS_TOOLS if t.name in enabled_names]

        # 把 file_remove_blacklist 配置注入到 FileRemoveTool 实例。
        # 注意：必须在 enabled_tools 过滤之后做——未启用的工具不需要注入。
        for t in tools_to_register:
            if isinstance(t, FileRemoveTool):
                t.custom_blacklist = list(_config.get("file_remove_blacklist") or [])

        # 注册过滤后的工具
        self._tool_names = {t.name for t in tools_to_register}
        context.add_llm_tools(*tools_to_register)
        if tools_to_register:
            logger.info(
                f"spcode_toolkit ready — {len(tools_to_register)} tools registered: "
                f"{sorted(t.name for t in tools_to_register)}"
            )
        else:
            logger.warning(
                "spcode_toolkit ready — 0 tools registered (enabled_tools 为空)。"
                "请在 WebUI 配置页勾选要启用的工具后重启 AstrBot。"
            )

        # 异步启动 codegraph MCP(不阻塞插件加载)
        if _config.get("codegraph_enabled", True):
            self._codegraph_task = asyncio.create_task(self._bootstrap_codegraph_mcp())

        # ── git 可用性探测 ──
        # 启动期一次同步探测,缺失不阻塞插件加载(端点注册照常);
        # 失败仅记 WARNING,用户首次调用 /spcode/git-diff 时会得到
        # reason="git_unavailable" 的结构化响应。
        try:
            import subprocess as _sp

            _git_probe = _sp.run(
                [self._git_binary(), "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                encoding=_GIT_DIFF_ENCODING,
                errors="replace",
            )
            if _git_probe.returncode == 0:
                _first_line = (
                    (_git_probe.stdout or "").splitlines()[0]
                    if _git_probe.stdout
                    else "unknown"
                )
                logger.info(f"[git-diff] detected: {_first_line}")
            else:
                logger.warning(
                    f"[git-diff] git 探测失败(returncode={_git_probe.returncode}, "
                    f"stderr={(_git_probe.stderr or '').strip()!r})"
                    " — /spcode/git-diff 端点将不可用。"
                    " 请安装 git 或在插件配置中设置 git_path。"
                )
        except FileNotFoundError:
            logger.warning(
                "[git-diff] git 未安装或不在 PATH 中"
                " — /spcode/git-diff 端点将不可用。"
                " 请安装 git 或在插件配置中设置 git_path。"
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"[git-diff] 启动期探测异常: {exc!s}")

    async def initialize(self) -> None:
        """插件激活（AstrBot 框架在 __init__ 后调用）。

        构造 inta_shell 组件单例。codegraph MCP 由 __init__ 内异步任务管理，
        不在这里重复处理。
        """
        # Register all 6 /spcode/* dashboard-facing web APIs in one shot.
        # Each handler lives in its own tools/webapi/*.py module; the route
        # table is owned by tools/webapi/__init__.ROUTES and adapter by
        # tools/webapi/_wrap (auto-injects umo/worktree/scope/path/etc.
        # from the request based on handler signature).
        register_webapi_routes(self)

        cfg = self._inta_shell_cfg
        component = LocalInteractiveShellComponent(
            max_sessions=cfg["max_sessions"],
            session_timeout_seconds=cfg["session_timeout"],
            enable_block=cfg["block_unsafe"],
        )
        # PR-2 (2026-06-23): 组件 + cwd 写入 tools.inta_shell.runtime 模块级单例
        # (替代 main.py 原模块级 _inta_component / _inta_default_cwd,便于 5 个
        # IntaShell*Tool 类从外部引用)
        from .tools.inta_shell import runtime as _inta_runtime

        _inta_runtime.component = component
        _inta_runtime.default_cwd = cfg["default_cwd"]
        logger.info(
            "[inta_shell] initialized: max_sessions=%d, session_timeout=%ds, "
            "block_unsafe=%s, default_cwd=%s",
            cfg["max_sessions"],
            cfg["session_timeout"],
            cfg["block_unsafe"],
            cfg["default_cwd"] or "(auto)",
        )

    @staticmethod
    def _flatten_config(config: dict) -> dict:
        """拍平嵌套分组为扁平 dict。

        AstrBot 的 "type":"object" 分组会把子字段存入嵌套 dict:
          {"codegraph": {"install_dir": "...", "project": "..."}}
        →
          {"codegraph_install_dir": "...", "codegraph_project": "..."}

        顶级非 dict 字段原样保留。
        """
        flat: dict = {}
        for key, value in config.items():
            if isinstance(value, dict):
                flat.update(value)
            else:
                flat[key] = value
        return flat

    def _git_binary(self) -> str:
        """解析 git 二进制路径。

        优先级:配置 `git_path` > 默认 `"git"`(走系统 PATH)。
        空字符串、纯空白都会被规整为 `"git"`。

        Returns:
            git 可执行文件名或绝对路径。
        """
        raw = self._config.get("git_path") or "git"
        return raw.strip() or "git"

    # 注意:旧的 ``_parse_files_changed`` 方法(v3.3 之前)在 2026-06-21 的
    # git-diff 4 合 1 重构中已被 ``_parse_diff_status_map`` + ``_parse_numstat_counts``
    # 替代;status / numstat 现在分别从 ``git diff`` (raw) 和 ``git diff --numstat``
    # 输出解析,join 在 handler 处内联。保留此注释作为变更记录。

    # ── /codegraph 命令组(AstrBot 规范: 命令组和子命令必须是插件类方法)───
    @filter.command_group("codegraph", alias={"cg"})
    def codegraph(self):
        """codegraph 项目管理指令组。"""
        pass

    # ── /project 命令组(v2.7 组合 agentsmd + codegraph) ───────────

    @filter.command_group("project")
    def project(self, event, sub_command: str = "", *args):
        """``/project`` 指令组的统一入口。

        装饰器模式下,本方法被 ``@filter.command_group`` 替换为
        ``RegisteringCommandable``,实际不会执行;真正的分发由
        :meth:`_project_router` 处理,以便单元测试可以直接调用。

        Args:
            event: AstrBot 事件对象(由框架注入)。
            sub_command: ``/project`` 后面的第一个子命令(load / unload / status / ...)。
            *args: 子命令对应的额外参数。
        """
        return None

    async def _project_router(self, event, sub_command: str, *args):
        """Implementation of the ``/project`` command group.

        Dispatches ``sub_command`` to the matching ``_project_*_impl`` helper
        and yields its messages. Unknown sub-commands yield a single error
        message.

        Args:
            event: AstrBot 事件对象。
            sub_command: 子命令字符串(load / unload / status / ...)。
            *args: 子命令对应的额外参数。

        Yields:
            Plain text messages for the user.
        """
        sub = (sub_command or "").strip().lower()
        if sub == "load":
            if not args:
                yield event.plain_result("❌ /project load 需要 <directory> 参数。")
                return
            async for msg in self._project_load_impl(event, args[0]):
                yield msg
            return
        if sub == "unload":
            async for msg in self._project_unload_impl(event):
                yield msg
            return
        if sub == "status":
            async for msg in self._project_status_impl(event):
                yield msg
            return
        # Unknown subcommand.
        yield event.plain_result(
            f"❌ 未知子命令: {sub_command!r}。支持: load / unload / status"
        )
        return

    @project.command("load")
    async def project_load(self, event, directory: str):
        """/project load <directory>

        一键加载项目到当前会话(委托给 ``_project_load_impl``)。

        Args:
            event: AstrBot 事件对象。
            directory: 用户提供的项目目录路径。
        """
        # Delegate to the testable helper so unit tests can exercise the
        # state-mutation path without depending on the @register decorator.
        async for msg in self._project_load_impl(event, directory):
            yield msg
        return

    async def _project_load_step(self, event, sub_gen, step_label: str):
        """Forward messages from a sub-step; abort on first "❌" message.

        用作 :meth:`_project_load_impl` 中所有 4 个子步骤的统一包装层:

        - **透传**: ``sub_gen`` 产出的每条消息都原样 ``yield`` 出去
        - **检测**: 任何以 ``"❌"`` 开头的消息视为失败
        - **中止**: 失败时 yield 一条总结消息,然后抛 :class:`_ProjectLoadAbort`
          终止整个 ``_project_load_impl`` 流程(stop at first error)

        为什么用异常而不是 flag?
            ``_project_load_impl`` 自己也是 async generator, ``return`` 只能
            终止自身;无法从 ``async for`` 循环内部跳出整个流。抛出一个私有
            异常是最干净的方式 — 父函数用 ``try/except _ProjectLoadAbort``
            接住后直接 ``return`` 即可。

        为什么"❌"而不是返回值?
            子方法(``_agentsmd_init`` / ``_agentsmd_load`` /
            ``_codegraph_init_or_uninit`` / ``_codegraph_set_project``)都
            遵循 "yield 错误消息 + return" 模式,从不抛异常。``❌`` 前缀是
            它们的统一约定(见 :data:`tools.agentsmd`)。``⚠️`` 不算失败 —
            ``_codegraph_init_or_uninit`` 在 "已初始化 → 自动 --force 重试"
            路径上以 ``⚠️`` 起头但最终可能成功。

        Args:
            event: AstrBot 事件对象(用于 yield abort 总结消息)。
            sub_gen: 子方法返回的 async generator,**不消耗**,只在这里转发。
            step_label: 本步的人类可读标签,如 ``"[1/3] AGENTS.md 加载"``。

        Yields:
            ``sub_gen`` 的全部消息 + (若失败) 一条 abort 总结消息。

        Raises:
            _ProjectLoadAbort: ``sub_gen`` 至少 yield 过一次以 ``"❌"``
                开头的消息。调用方应捕获并 ``return``。
        """
        failed = False
        async for msg in sub_gen:
            yield msg
            # msg 在生产里是 ``MessageEventResult``(由 ``event.plain_result()``
            # 返回),在单元测试里 mock 可能直接 yield 字符串。这里做"防御式"
            # 抽取,主路径(MER)走 ``.chain[0].text``;测试/mock 路径走 str。
            text: str | None = None
            if isinstance(msg, str):
                text = msg
            else:
                chain = getattr(msg, "chain", None)
                if chain:
                    first = chain[0]
                    text = getattr(first, "text", None)
            if isinstance(text, str) and text.startswith("❌"):
                failed = True
        if failed:
            yield event.plain_result(
                f"❌ {step_label} 失败,/project load 中止。"
                "请根据上方错误信息修复后,重试 /project load <directory>。"
            )
            raise _ProjectLoadAbort(step_label)

    async def _project_load_impl(self, event, directory: str):
        """Implementation of :meth:`project_load`.

        Performs the multi-step project load: feature-flag check, duplicate
        load guard, path safety, agentsmd init+load, codegraph init+set,
        records the load into ``self._loaded_projects[umo]``, and finally
        yields a summary message.

        任一子步骤失败(yield 任何以 ``❌`` 开头的消息)→ 立即中止整个 load:
        后续子方法不会被调用, ``_loaded_projects[umo]`` 不会被填充,
        也不会 yield "✅ 项目已加载"。``⚠️`` 不算失败
        (见 :meth:`_project_load_step`)。

        Args:
            event: AstrBot 事件对象。
            directory: 用户提供的项目目录路径。

        Yields:
            Plain text messages for the user。
        """
        umo = event.unified_msg_origin
        # 1. Feature flag 校验
        agentsmd_on = self._config.get("agentsmd_enabled", True)
        codegraph_on = self._config.get("codegraph_enabled", True)
        if not (agentsmd_on and codegraph_on):
            yield event.plain_result(
                "❌ /project 命令需要先启用 codegraph 和 AGENTS.md 功能。\n"
                "请在插件配置中打开这两项后再试一次。"
            )
            return

        # 2. 重复 load 拦截(Q2=B 决策)
        if umo in self._loaded_projects:
            loaded = self._loaded_projects[umo]
            yield event.plain_result(
                f"❌ 当前会话已加载项目: {loaded['directory']}\n"
                f"请先执行 /project unload,再 load 新项目。"
            )
            return

        # 3. 路径解析与安全校验
        directory = _agentsmd_mod.strip_surrounding_quotes(directory)
        target = Path(directory).resolve()
        ok, reason = _is_path_safe(
            target, user_blacklist=self._config.get("file_remove_blacklist")
        )
        if not ok:
            yield event.plain_result(f"❌ 路径不允许: {reason}")
            return

        # 4. 多步加载(任一子步骤失败 → 立即中止,不再登记 _loaded_projects)
        try:
            # 步骤 1/3: agentsmd(init 条件性 + load)
            agents_md_path = target / "AGENTS.md"
            if not agents_md_path.exists():
                yield event.plain_result(
                    f"⏳ [1/3] AGENTS.md 不存在,正在 init: {target}"
                )
                async for msg in self._project_load_step(
                    event,
                    self._agentsmd_init(event, str(target)),
                    "[1/3] AGENTS.md 初始化",
                ):
                    yield msg
            else:
                yield event.plain_result(
                    f"ℹ️ [1/3] AGENTS.md 已存在,跳过 init: {agents_md_path}"
                )
            yield event.plain_result(f"⏳ [1/3] 正在 load AGENTS.md: {target}")
            async for msg in self._project_load_step(
                event,
                self._agentsmd_load(event, str(target)),
                "[1/3] AGENTS.md 加载",
            ):
                yield msg

            # 步骤 2/3: codegraph init + set
            yield event.plain_result(f"⏳ [2/3] codegraph init: {target}")
            async for msg in self._project_load_step(
                event,
                self._codegraph_init_or_uninit(event, str(target), init=True),
                "[2/3] codegraph init",
            ):
                yield msg

            yield event.plain_result(f"⏳ [2/3] codegraph set: {target}")
            async for msg in self._project_load_step(
                event,
                self._codegraph_set_project(event, str(target)),
                "[2/3] codegraph set",
            ):
                yield msg
        except _ProjectLoadAbort:
            return

        # 5. 记录状态(仅在所有子步骤都成功后才登记)
        loaded_at_ts = _time.time()
        self._loaded_projects[umo] = {
            "directory": str(target),
            "loaded_at": loaded_at_ts,
        }

        yield event.plain_result(
            f"✅ 项目已加载: {target}\n"
            f"已自动进行如下步骤:\n"
            f"  - 设定工作目录\n"
            f"  - AGENTS.md 注入到 system_prompt\n"
            f"  - 载入 codegraph 索引\n"
            f"\n若要卸载，请执行`/project unload`\n"
        )

    @project.command("unload")
    async def project_unload(self, event):
        """/project unload(委托给 ``_project_unload_impl``)。

        Args:
            event: AstrBot 事件对象。
        """
        async for msg in self._project_unload_impl(event):
            yield msg
        return

    async def _project_unload_impl(self, event):
        """Implementation of :meth:`project_unload`.

        Unloads the current session's project: feature-flag check, no-op guard,
        agentsmd unload, codegraph set to default, and finally clears
        ``self._loaded_projects[umo]``.

        Args:
            event: AstrBot 事件对象。

        Yields:
            Plain text messages for the user.
        """
        # 1. Feature flag 校验
        agentsmd_on = self._config.get("agentsmd_enabled", True)
        codegraph_on = self._config.get("codegraph_enabled", True)
        if not (agentsmd_on and codegraph_on):
            yield event.plain_result(
                "❌ /project 命令需要先启用 codegraph 和 AGENTS.md 功能。\n"
                "请在插件配置中打开这两项后再试一次。"
            )
            return

        umo = event.unified_msg_origin
        if umo not in self._loaded_projects:
            yield event.plain_result("ℹ️ 当前会话未加载项目,无需 unload。")
            return

        # 2. agentsmd unload(同步返回单条消息)
        yield self._agentsmd_unload(event)

        # 3. codegraph set 回默认项目
        default_project = (self._config.get("codegraph_project") or "").strip()
        if default_project:
            yield event.plain_result(f"⏳ codegraph set 回默认项目: {default_project}")
            async for msg in self._codegraph_set_project(event, default_project):
                yield msg
        else:
            yield event.plain_result(
                "ℹ️ codegraph_project 未配置,跳过 codegraph set。"
                "MCP 当前默认项目维持原状。"
            )

        # 4. 清理状态(必须在最末,即便 set 失败也清,避免用户无法重试)
        info = self._loaded_projects.pop(umo)
        yield event.plain_result(
            f"✅ 项目已卸载: {info['directory']}\n"
            f"  - AGENTS.md 注入已移除\n"
            f"  - codegraph 默认项目已重置\n"
        )

    @project.command("status")
    async def project_status(self, event):
        """/project status(委托给 ``_project_status_impl``)。

        Args:
            event: AstrBot 事件对象。
        """
        async for msg in self._project_status_impl(event):
            yield msg
        return

    async def _project_status_impl(self, event):
        """Implementation of :meth:`project_status`.

        Reads ``self._loaded_projects[umo]`` and yields a human-readable
        status for the chat response. The authoritative state used by the
        dashboard's spcode chip is exposed separately via
        :meth:`handle_get_project_status` (mounted at
        ``GET /spcode/project-status``) — that endpoint is the single
        source of truth for the dashboard; the chat response is plain
        text and intentionally does NOT carry any hidden marker.

        Args:
            event: AstrBot 事件对象。

        Yields:
            Plain text messages for the user.
        """
        umo = event.unified_msg_origin
        info = self._loaded_projects.get(umo)
        if info is None:
            yield event.plain_result("📂 当前会话未加载项目")
            return
        directory = info.get("directory", "")
        loaded_at_ts = info.get("loaded_at", 0)
        loaded_at_str = (
            _datetime.datetime.fromtimestamp(loaded_at_ts).strftime("%Y-%m-%d %H:%M:%S")
            if loaded_at_ts
            else "未知"
        )
        yield event.plain_result(
            f"📂 当前已加载项目\n路径: {directory}\n加载于: {loaded_at_str}\n"
        )

    def get_loaded_project(self, umo: str) -> dict | None:
        """Get the loaded project info for a given unified message origin.

        Args:
            umo: The unified message origin (session key).

        Returns:
            A dict with `directory` and `loaded_at` keys, or ``None`` if no
            project is currently loaded for the given ``umo``.
        """
        info = self._loaded_projects.get(umo)
        if info is None:
            return None
        # Return a shallow copy so callers cannot mutate internal state.
        return dict(info)



    @codegraph.command("init")
    async def codegraph_init(self, event, directory: str):
        """/codegraph init <directory>

        初始化指定目录为 codegraph 项目(创建 .codegraph/ 索引)。
        """
        async for msg in self._codegraph_init_or_uninit(event, directory, init=True):
            yield msg

    @codegraph.command("uninit")
    async def codegraph_uninit(self, event, directory: str):
        """/codegraph uninit <directory>

        反初始化指定目录(删除 .codegraph/ 索引)。
        """
        async for msg in self._codegraph_init_or_uninit(event, directory, init=False):
            yield msg

    @codegraph.command("set")
    async def codegraph_set(self, event, directory: str):
        """/codegraph set <directory>

        修改 codegraph 的默认执行目录。后续 LLM 调用的 codegraph_* 工具
        会以新目录为根。
        """
        async for msg in self._codegraph_set_project(event, directory):
            yield msg

    # ── /agentsmd 命令组(v2.4 合并自独立 agentsmd 插件)──

    @filter.command_group("agentsmd")
    def agentsmd(self):
        """AGENTS.md 管理指令组(从独立插件合并)。"""
        pass

    @agentsmd.command("init")
    async def agentsmd_init(self, event, directory: str):
        """/agentsmd init <directory>"""
        if not self._config.get("agentsmd_enabled", True):
            yield event.plain_result(
                "AGENTS.md 管理功能已被禁用(agentsmd_enabled=false)。"
            )
            return
        async for msg in self._agentsmd_init(event, directory):
            yield msg

    @agentsmd.command("load")
    async def agentsmd_load(self, event, directory: str):
        """/agentsmd load <directory>"""
        if not self._config.get("agentsmd_enabled", True):
            yield event.plain_result(
                "AGENTS.md 管理功能已被禁用(agentsmd_enabled=false)。"
            )
            return
        async for msg in self._agentsmd_load(event, directory):
            yield msg

    @agentsmd.command("unload")
    async def agentsmd_unload(self, event):
        """/agentsmd unload"""
        if not self._config.get("agentsmd_enabled", True):
            yield event.plain_result(
                "AGENTS.md 管理功能已被禁用(agentsmd_enabled=false)。"
            )
            return
        yield self._agentsmd_unload(event)

    @agentsmd.command("update")
    async def agentsmd_update(self, event):
        """/agentsmd update"""
        if not self._config.get("agentsmd_enabled", True):
            yield event.plain_result(
                "AGENTS.md 管理功能已被禁用(agentsmd_enabled=false)。"
            )
            return
        async for msg in self._agentsmd_update(event):
            yield msg

    def _build_mcp_cfg(self) -> dict | None:
        """根据当前 _config 构造 codegraph MCP 启动配置。

        供 _bootstrap_codegraph_mcp 和 _codegraph_set_project 复用。
        Returns None 如果 install_dir 未配置或验证失败(详细原因已在 _detect_from_install_dir 内 log)。
        """
        install_dir = (self._config.get("codegraph_install_dir") or "").strip()
        if not install_dir:
            return None
        cfg = detect_codegraph_launcher(install_dir=install_dir)
        if not cfg:
            return None
        # 若用户在插件配置了 codegraph_project,作为默认项目注入
        project = (self._config.get("codegraph_project") or "").strip()
        if project and not SHELL_META_RE.search(project):
            cfg = dict(cfg)  # 浅拷贝避免污染
            cfg["args"] = list(cfg["args"]) + ["--path", project]
        return cfg

    async def _bootstrap_codegraph_mcp(self) -> None:
        """插件加载后异步拉起 codegraph MCP server。失败不抛异常。"""
        # 防御性:即便 __init__ 已 gate,函数本身也再 check 一次,便于单测
        if not self._config.get("codegraph_enabled", True):
            return
        try:
            # v2.1: 必须显式配置 install_dir 才会启动 MCP(去除 auto-detect)
            install_dir = (self._config.get("codegraph_install_dir") or "").strip()
            if not install_dir:
                logger.info(
                    "codegraph_install_dir 未配置,跳过 MCP 集成"
                    "(spcode 其它工具照常工作;如需启用请配置 codegraph_install_dir)"
                )
                return

            ensure_stdio_allowlist()
            cfg = self._build_mcp_cfg()
            if not cfg:
                # install_dir 已配置但 _detect_from_install_dir 验证失败,
                # 详细原因已在 _detect_from_install_dir 内 log warning
                logger.warning(
                    f"codegraph_install_dir 验证失败,MCP 不启动: {install_dir!r}"
                )
                return

            mgr = self.context.get_llm_tool_manager()
            # 兼容用户在 mcp_server.json 手写过 codegraph 的情况
            if "codegraph" in mgr.mcp_server_runtime:
                logger.info("检测到已注册的 codegraph MCP,先停掉再用插件配置重启")
                await mgr.disable_mcp_server("codegraph")

            await mgr.enable_mcp_server(
                name="codegraph",
                config=cfg,
                timeout=180,  # 与 AstrBot DEFAULT_ENABLE_MCP_TIMEOUT_SECONDS 一致
            )
            logger.info(
                f"codegraph MCP 已启动: {Path(cfg['command']).name} "
                f"{' '.join(cfg['args'])}"
            )
        except Exception as e:
            logger.warning(f"codegraph MCP 启动失败,spcode 其它工具不受影响: {e}")

    async def terminate(self):
        """Star 框架在插件卸载/重载时调用。"""
        # PR-2 (2026-06-23): 组件状态从 main.py 模块级变量改为
        # tools.inta_shell.runtime 单例。这里延迟 import 避免循环依赖。
        from .tools.inta_shell import runtime as _inta_runtime

        # 0. 停 inta_shell 交互式 Shell 组件
        if _inta_runtime.component is not None:
            try:
                logger.info("[inta_shell] terminating component...")
                summary = await _inta_runtime.component.shutdown()
                logger.info(
                    "[inta_shell] terminated: %d session(s) cleaned (graceful=%d forced=%d)",
                    summary.get("total", 0),
                    summary.get("graceful", 0),
                    summary.get("forced", 0),
                )
            except Exception as e:
                logger.warning("[inta_shell] shutdown error: %s", e)
            finally:
                _inta_runtime.component = None
                _inta_runtime.default_cwd = ""

        mgr = self.context.get_llm_tool_manager()

        # 1. 取消还在跑的 bootstrap 任务
        if self._codegraph_task and not self._codegraph_task.done():
            self._codegraph_task.cancel()
            try:
                await self._codegraph_task
            except (asyncio.CancelledError, Exception):
                pass

        # 2. 停 codegraph MCP server (只杀 serve --mcp 代理子进程)
        if "codegraph" in mgr.mcp_server_runtime:
            try:
                await mgr.disable_mcp_server("codegraph", timeout=15)
            except Exception as e:
                if (
                    _HAS_MCP_EXCEPTIONS
                    and MCPShutdownTimeoutError
                    and isinstance(e, MCPShutdownTimeoutError)
                ):
                    logger.warning("codegraph MCP 关闭超时,可能留有僵尸代理子进程")
                else:
                    raise

    async def _codegraph_set_project(self, event, directory: str):
        """`/codegraph set <dir>` 命令实现:修改 codegraph 的默认项目根目录。

        行为:
        1. 校验目录(必须存在)
        2. 更新 self._config["codegraph_project"]
        3. 如果 MCP server 正在跑,重启它以应用新的 --path 参数
        4. 后续 LLM 调用的 codegraph_* 工具会以新项目为默认根
        """
        # 1. 路径校验(必须存在的目录)
        target = resolve_project_path(
            directory,
            init=True,
            user_blacklist=self._config.get("file_remove_blacklist") or [],
        )
        if isinstance(target, str):  # 错误消息
            yield event.plain_result(target)
            return

        target_str = str(target)
        # 2. 更新配置
        old = (self._config.get("codegraph_project") or "").strip()
        self._config["codegraph_project"] = target_str
        logger.info(f"codegraph_project: {old!r} → {target_str!r}")

        mgr = self.context.get_llm_tool_manager()
        mcp_running = "codegraph" in mgr.mcp_server_runtime

        # 3a. 如果 MCP 没在跑(可能 install_dir 未配置 / 验证失败),
        # 不重启,只更新 config;bootstrap 或下次重启会生效
        if not mcp_running:
            yield event.plain_result(
                f"✅ codegraph_project 已更新为: {target_str}\n"
                "   (MCP 当前未运行,新项目将在下次启动 codegraph MCP 时生效)"
            )
            return

        # 3b. MCP 在跑 → 重启以应用新 --path
        try:
            yield event.plain_result(
                f"🔄 正在重启 codegraph MCP 以应用新项目: {target_str}..."
            )
            await mgr.disable_mcp_server("codegraph", timeout=15)
        except Exception as e:
            logger.warning(f"codegraph MCP 关闭失败(可能 zombie 进程): {e}")
            # 继续尝试启动新实例

        try:
            ensure_stdio_allowlist()
            cfg = self._build_mcp_cfg()
            if not cfg:
                yield event.plain_result(
                    f"⚠️ 已更新 codegraph_project,但无法构造新 MCP 配置"
                    f"(install_dir 可能已失效)。新值: {target_str}"
                )
                return
            await mgr.enable_mcp_server(
                name="codegraph",
                config=cfg,
                timeout=180,
            )
            yield event.plain_result(
                f"✅ codegraph 已切换到新项目: {target_str}\n"
                "   后续 LLM 调用的 codegraph_* 工具默认在此目录下操作"
            )
            logger.info(f"codegraph MCP 已重启,新 --path: {target_str}")
        except Exception as e:
            logger.warning(f"codegraph MCP 重启失败: {e}")
            yield event.plain_result(
                f"❌ codegraph MCP 重启失败: {e}\n"
                f"   (codegraph_project 已更新为 {target_str},"
                f"重启 AstrBot 后生效)"
            )

    async def _codegraph_init_or_uninit(self, event, directory: str, *, init: bool):
        """共享实现,init/uninit 只差一个 subcommand。"""
        # 1. 路径校验
        #    v2.9: init 时要求目录下至少存在一个代码文件(对齐 /agentsmd init);
        #    uninit 故意跳过此检查(允许对空目录 uninit,语义上无害)。
        target = resolve_project_path(
            directory,
            init=init,
            user_blacklist=self._config.get("file_remove_blacklist") or [],
            require_code_files=init,
        )
        if isinstance(target, str):  # 错误消息
            yield event.plain_result(target)
            return

        # 2. 找 codegraph CLI 启动器
        # 优先用用户配置的 install_dir;若未配置(单次命令场景),fallback 到 auto-detect
        install_dir = (self._config.get("codegraph_install_dir") or "").strip() or None
        mcp_cfg = detect_codegraph_launcher(install_dir=install_dir)
        cli_launcher = build_cli_launcher(mcp_cfg)
        if not cli_launcher:
            yield event.plain_result(
                "❌ 找不到 codegraph CLI,请先 `npm install -g @colbymchenry/codegraph`"
            )
            return

        # 3. 并发锁(防止同目录并发 init/uninit 把 .codegraph/ 写坏)
        target_str = str(target)
        lock = self._codegraph_dir_locks.setdefault(target_str, asyncio.Lock())
        if lock.locked():
            yield event.plain_result(
                f"⏳ 目录 {target_str} 已有 codegraph 操作在跑,请等待完成"
            )
            return
        async with lock:
            # 4. 立即回应
            action = "初始化" if init else "反初始化"
            yield event.plain_result(
                f"⏳ 正在 {action} codegraph 项目 {target_str}...\n"
                f"   (大型项目可能耗时数分钟,期间请勿重复执行)"
            )

            # 5. 异步执行
            sub = "init" if init else "uninit"
            cmd_args = cli_launcher["args"] + [sub, target_str]
            if not init:
                cmd_args.append("--force")

            try:
                proc = await asyncio.create_subprocess_exec(
                    cli_launcher["command"],
                    *cmd_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as e:
                yield event.plain_result(f"❌ 启动 codegraph 失败: {e}")
                return

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                yield event.plain_result(f"❌ codegraph {sub} 超时(300s),已终止")
                return

            if proc.returncode == 0:
                if init:
                    yield event.plain_result(
                        f"✅ codegraph 初始化完成: {target_str}\n"
                        f"   下一步:在对话中用 codegraph_status 验证索引,"
                        f"或直接用 codegraph_explore 触发懒加载建索引"
                    )
                else:
                    yield event.plain_result(
                        f"✅ codegraph 反初始化完成: {target_str}\n"
                        f"   (.codegraph/ 目录已删除)"
                    )
            else:
                err = (
                    (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
                )
                err_lower = err.lower()
                if init and ("not found" in err_lower or "no such file" in err_lower):
                    yield event.plain_result(
                        f"❌ 目录不存在: {target_str}\n   请先创建或检查路径"
                    )
                elif init and (
                    "already initialized" in err_lower or "exists" in err_lower
                ):
                    # 自动用 --force 重试一次
                    yield event.plain_result(
                        "⚠️ 目标目录已初始化 codegraph,自动用 --force 重试..."
                    )
                    retry_args = cli_launcher["args"] + [
                        sub,
                        target_str,
                        "--force",
                    ]
                    proc2 = await asyncio.create_subprocess_exec(
                        cli_launcher["command"],
                        *retry_args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        stdout2, stderr2 = await asyncio.wait_for(
                            proc2.communicate(), timeout=180
                        )
                        if proc2.returncode == 0:
                            yield event.plain_result(
                                f"✅ codegraph 重新初始化完成: {target_str}"
                            )
                        else:
                            err2 = (
                                (stderr2 or stdout2 or b"")
                                .decode("utf-8", errors="replace")
                                .strip()
                            )
                            yield event.plain_result(
                                f"❌ --force 重试也失败:\n{err2[:1500]}"
                            )
                    except asyncio.TimeoutError:
                        proc2.kill()
                        yield event.plain_result("❌ --force 重试超时")
                elif (not init) and (
                    "not initialized" in err_lower or "no .codegraph" in err_lower
                ):
                    yield event.plain_result(
                        "ℹ️ 目标目录未初始化 codegraph(没有 .codegraph/),无需 uninit"
                    )
                else:
                    yield event.plain_result(
                        f"❌ codegraph {sub} 失败(退出码 {proc.returncode}):\n{err[:1500]}"
                    )

    # ── /agentsmd 业务方法(v2.4)────────────────────

    async def _agentsmd_init(self, event, directory: str):
        """/agentsmd init <directory> 实现。"""
        directory = _agentsmd_mod.strip_surrounding_quotes(directory)
        target = Path(directory).resolve()

        ok, reason = _is_path_safe(
            target, user_blacklist=self._config.get("file_remove_blacklist")
        )
        if not ok:
            yield event.plain_result(f"❌ 路径不允许: {reason}")
            return

        # v2.9: 显式拒绝不存在的目录(取消自动 mkdir,避免对路径拼错场景静默创建空目录)
        if not target.exists():
            yield event.plain_result(
                f"❌ 目录 `{directory}` 不存在。\n请先创建该目录,或确认路径是否正确。"
            )
            return
        if not target.is_dir():
            yield event.plain_result(f"❌ `{directory}` 不是一个有效的目录。")
            return

        # v2.9: 要求目录下至少存在一个代码文件,避免对空目录或纯文档目录误用。
        # AGENTS.md 是给"在此仓库工作的编程代理"用的规范,没有代码的项目无意义。
        if not _agentsmd_mod.has_code_files(target):
            supported = ", ".join(
                f".{ext}" for ext in sorted(_agentsmd_mod.CODE_FILE_EXTENSIONS)
            )
            yield event.plain_result(
                f"❌ 目录 `{directory}` 下未找到代码文件。\n"
                f"AGENTS.md 仅用于代码项目,支持的后缀: {supported}\n"
                "请确认目录是否正确,或选择包含源代码的目录。"
            )
            return

        agents_md_path = target / "AGENTS.md"
        if agents_md_path.exists():
            yield event.plain_result(
                f"⚠️ 目录 `{directory}` 下已存在 AGENTS.md。\n"
                "如需重新生成,请先删除该文件后再执行 init。"
            )
            return

        yield event.plain_result(f"🔄 正在为 `{directory}` 生成 AGENTS.md,请稍候…")

        umo = event.unified_msg_origin
        provider = self.context.get_using_provider(umo=umo)
        init_template = _agentsmd_mod.resolve_init_template(self._config)

        content = await _agentsmd_mod.generate_agents_md_via_llm(
            provider, target, umo=umo, init_template=init_template
        )

        try:
            await asyncio.to_thread(
                agents_md_path.write_text, content, encoding="utf-8"
            )
            yield event.plain_result(
                f"✅ 已在 `{directory}` 下创建 AGENTS.md({len(content)} 字符)。\n"
                f"路径: {agents_md_path}\n"
                f"您可以使用 `/agentsmd load {directory}` 将其加载到当前会话的系统提示词中。"
            )
        except Exception as e:
            yield event.plain_result(f"❌ 写入 AGENTS.md 失败: {e}")

    async def _agentsmd_load(self, event, directory: str):
        """/agentsmd load <directory> 实现。"""
        directory = _agentsmd_mod.strip_surrounding_quotes(directory)
        target = Path(directory).resolve()

        ok, reason = _is_path_safe(
            target, user_blacklist=self._config.get("file_remove_blacklist")
        )
        if not ok:
            yield event.plain_result(f"❌ 路径不允许: {reason}")
            return

        # v2.9: 显式校验目标目录存在且是目录,避免把"目录不存在"和
        # "目录存在但缺 AGENTS.md"混为同一个错误消息。
        if not target.exists():
            yield event.plain_result(
                f"❌ 目录 `{directory}` 不存在。\n请先创建该目录,或确认路径是否正确。"
            )
            return
        if not target.is_dir():
            yield event.plain_result(f"❌ `{directory}` 不是一个目录。")
            return

        # v2.9: 代码文件检测(与 init 对齐)
        if not _agentsmd_mod.has_code_files(target):
            supported = ", ".join(
                f".{ext}" for ext in sorted(_agentsmd_mod.CODE_FILE_EXTENSIONS)
            )
            yield event.plain_result(
                f"❌ 目录 `{directory}` 下未找到代码文件。\n"
                f"AGENTS.md 仅用于代码项目,支持的后缀: {supported}\n"
                "请确认目录是否正确,或选择包含源代码的目录。"
            )
            return

        agents_md_path = target / "AGENTS.md"
        if not agents_md_path.exists():
            yield event.plain_result(
                f"❌ 目录 `{directory}` 下未找到 AGENTS.md 文件。\n"
                f"请先使用 `/agentsmd init {directory}` 创建。"
            )
            return

        try:
            content = await asyncio.to_thread(
                agents_md_path.read_text, encoding="utf-8"
            )
            mtime = agents_md_path.stat().st_mtime
        except Exception as e:
            yield event.plain_result(f"❌ 读取 AGENTS.md 失败: {e}")
            return

        umo = event.unified_msg_origin
        self._loaded_agents[umo] = {
            "path": str(agents_md_path),
            "directory": str(target),
            "last_content": content,
            "mtime": mtime,
        }

        yield event.plain_result(
            f"✅ 已加载 `{directory}/AGENTS.md` 到当前会话的系统提示词。\n"
            f"内容长度: {len(content)} 字符\n"
            "后续每次 LLM 请求都会自动注入该内容。\n"
            "使用 `/agentsmd unload` 可卸载,使用 `/agentsmd update` 可手动更新。"
        )

    def _agentsmd_unload(self, event):
        """/agentsmd unload 实现。"""
        umo = event.unified_msg_origin
        if umo not in self._loaded_agents:
            return event.plain_result("ℹ️ 当前会话未加载任何 AGENTS.md。")
        info = self._loaded_agents.pop(umo)
        return event.plain_result(
            f"✅ 已卸载 AGENTS.md 注入。\n原文件: `{info['path']}`"
        )

    async def _agentsmd_update(self, event):
        """/agentsmd update 实现。"""
        umo = event.unified_msg_origin
        if umo not in self._loaded_agents:
            yield event.plain_result(
                "当前会话未加载 AGENTS.md。请先使用 `/agentsmd load <路径>` "
                "加载一个 AGENTS.md。"
            )
            return

        info = self._loaded_agents[umo]
        agents_md_path = Path(info["path"])
        dir_path = Path(info["directory"])
        existing_content = info["last_content"]

        if not agents_md_path.exists():
            yield event.plain_result(
                f"AGENTS.md 文件不存在: {agents_md_path}\n"
                "请检查文件是否被删除,或重新使用 `/agentsmd init` 初始化。"
            )
            return

        yield event.plain_result(
            "正在使用 LLM 重新生成 AGENTS.md (基于现有内容 + 最新目录结构) . .."
        )

        provider = self.context.get_using_provider(umo=umo)
        if provider is None:
            yield event.plain_result("当前会话未配置 LLM Provider,无法更新。")
            return

        init_template = _agentsmd_mod.resolve_init_template(self._config)
        prompt = (
            f"{init_template}\n\n"
            "以下是该项目的文件结构和关键文件内容摘要:\n\n"
            f"{_agentsmd_mod.scan_project_context(dir_path)}\n\n"
            "## 现有 AGENTS.md 内容\n\n"
            f"{existing_content}\n\n"
            "请比较现有实现和目录结构与 AGENTS.md 是否有差异,并更新 AGENTS.md\n\n"
            "请直接输出 AGENTS.md 的完整内容(Markdown 格式),不要添加任何额外说明,"
            "也不要使用 ```markdown 等代码块包裹整个内容。"
        )

        try:
            llm_resp = await provider.text_chat(
                prompt=prompt,
                session_id=umo,
                contexts=[],
                system_prompt="你是一名资深软件工程师,擅长为多种语言的项目编写规范文档。",
            )
        except Exception as e:
            yield event.plain_result(f"LLM 调用失败: {e}")
            return

        new_content = (getattr(llm_resp, "completion_text", "") or "").strip()
        new_content = _agentsmd_mod.strip_code_fence(new_content)
        new_content = new_content if new_content else existing_content

        try:
            await asyncio.to_thread(
                agents_md_path.write_text, new_content, encoding="utf-8"
            )
        except Exception as e:
            yield event.plain_result(f"❌ 写入 AGENTS.md 失败: {e}")
            return

        info["last_content"] = new_content
        info["mtime"] = agents_md_path.stat().st_mtime

        yield event.plain_result(
            f"AGENTS.md 已更新 ({len(new_content)} 字符)。\n"
            "后续 LLM 请求将自动使用新版本。"
        )

    @filter.on_llm_request()
    async def _agentsmd_inject_to_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """每次 LLM 请求前,若当前会话已加载 AGENTS.md,注入到 system_prompt 末尾。"""
        if not self._config.get("agentsmd_enabled", True):
            return
        umo = event.unified_msg_origin
        if umo not in self._loaded_agents:
            return

        info = self._loaded_agents[umo]
        agents_md_path = Path(info["path"])
        content = info.get("last_content", "")

        # 通过 mtime 检测变更(自动刷新缓存,无需手动 update)
        try:
            if agents_md_path.exists():
                current_mtime = agents_md_path.stat().st_mtime
                if current_mtime != info.get("mtime"):
                    content = await asyncio.to_thread(
                        agents_md_path.read_text, encoding="utf-8"
                    )
                    self._loaded_agents[umo]["last_content"] = content
                    self._loaded_agents[umo]["mtime"] = current_mtime
                    logger.debug(
                        f"[agentsmd] 检测到 AGENTS.md 已变更,已刷新缓存: {agents_md_path}"
                    )
            else:
                logger.warning(
                    f"[agentsmd] AGENTS.md 文件不存在,使用缓存内容: {agents_md_path}"
                )
        except Exception as e:
            logger.error(f"[agentsmd] 读取 AGENTS.md 失败,使用缓存: {e}")

        if not content:
            return

        # 防重复注入(同一请求)
        if _agentsmd_mod.INJECTION_MARKER in (req.system_prompt or ""):
            return

        # v2.8: 把项目目录也注入到 system_prompt(放在 AGENTS.md 之前),
        # 让 LLM 知道当前会话绑定到哪个项目。
        directory = info.get("directory", "")
        if req.system_prompt is None or req.system_prompt == "":
            req.system_prompt = _agentsmd_mod.build_injection(
                content, directory=directory
            ).lstrip("\n")
        else:
            req.system_prompt = req.system_prompt + _agentsmd_mod.build_injection(
                content, directory=directory
            )

        logger.debug(
            f"[agentsmd] 已向会话 {umo} 的 system_prompt 注入 AGENTS.md "
            f"({len(content)} 字符)"
        )

    @filter.on_llm_request()
    async def _project_inject_codegraph_guidance(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """/project load 后,把 codegraph 优先使用指引注入到 system_prompt 末尾。

        与 _agentsmd_inject_to_llm_request 平行,但走独立 marker 防重复。
        只在以下条件同时满足时注入:
        - codegraph_enabled = true
        - 当前 umo 已在 self._loaded_projects 中

        PR-4 (2026-06-23): 注入样板(防重复 + lstrip + 拼接)委托给
        tools.llm_inject.inject_guidance。
        """
        if not self._config.get("codegraph_enabled", True):
            return
        umo = event.unified_msg_origin
        if umo not in self._loaded_projects:
            return
        if inject_guidance(req, PROJECT_CODEGRAPH_GUIDANCE, PROJECT_GUIDANCE_MARKER):
            logger.debug(
                f"[project] 已向会话 {umo} 的 system_prompt 注入 codegraph 指引"
            )

    @filter.on_llm_request()
    async def _file_remove_inject_guidance(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """astrbot_file_remove 启用时,把"优先使用 file_remove"指引注入到 system_prompt 末尾。

        触发条件(全部满足):
        - `astrbot_file_remove` 在 self._tool_names 中(说明用户已启用)
        - 同 req.system_prompt 中尚未包含 marker(防重复注入)

        设计要点(对照 _project_inject_codegraph_guidance):
        1. 无 session state / 无 feature flag——_tool_names 即 gate
        2. system_prompt = None 时用 lstrip("\\n") 避免前置空行
        3. 已存在 system_prompt 时追加在末尾

        PR-4 (2026-06-23): 注入样板委托给 tools.llm_inject.inject_guidance。
        """
        # 同时接受新旧两个工具名,避免单点失败
        if not (
            "astrbot_file_remove" in self._tool_names
            or "astrbot_file_remove_tool" in self._tool_names
        ):
            return
        if inject_guidance(req, FILE_REMOVE_GUIDANCE, FILE_REMOVE_GUIDANCE_MARKER):
            logger.debug("[file_remove] 已向 system_prompt 注入优先使用指引")

    @filter.on_llm_request()
    async def _auth_guard(self, event, req: ProviderRequest):
        """L1 鉴权:非管理员从工具列表中移除本插件工具。"""
        if not req.func_tool:
            return
        # PR-3 (2026-06-23): 委托给 tools.security.check_is_admin
        if check_is_admin(event):
            return
        # 管理员可见全部；非管理员 → 全部隐藏（spcode_toolkit 是管理员工具集）
        kept = []
        removed = []
        for tool in req.func_tool.tools:
            if tool.name in self._tool_names:
                removed.append(tool.name)
                continue
            kept.append(tool)
        if removed:
            try:
                from astrbot.core.agent.tool import ToolSet

                new_set = ToolSet()
                for tool in kept:
                    new_set.add_tool(tool)
                req.func_tool = new_set
            except Exception as exc:
                logger.warning(f"spcode_toolkit 鉴权失败: {exc}")

    # ── v2.8: /plan 模式 ──────────────────────────────────────
    #
    # PR-3 (2026-06-23): 业务逻辑已全部委托到 tools.security.PlanModeController,
    # 本类只剩装饰器占位 + UI 反馈。
    # /plan /build 命令的设计要点参见 PlanModeController 的 docstring。

    @filter.on_llm_request()
    async def _plan_filter_tools(self, event, req: ProviderRequest):
        """v2.8: /plan 模式钩子入口 — 全部委托给 PlanModeController。"""
        self._plan.filter_request(event, req)

    @filter.command("plan")
    async def plan(self, event):
        """/plan — 进入 plan 模式(过滤写工具,提示 LLM 调研而非动手)

        与 opencode /plan 等价:激活后,_plan_filter_tools 钩子会从 LLM 工具列表
        过滤掉 plan_mode.blocked_tools 列出的写工具;首次 LLM 调用时自动注入
        plan 模式 reminder 到 user message。

        退出请使用 /build。
        """
        umo = event.unified_msg_origin
        was_active = self._plan.is_active(umo)
        self._plan.activate(umo)
        blocked = self._config.get("plan_mode_blocked_tools") or []
        if not blocked:
            yield event.plain_result(
                "⚠️ plan 模式已激活,但 plan_mode_blocked_tools 为空。\n"
                "将不会过滤任何工具。请在插件配置中填写要过滤的工具名。\n"
                "使用 /build 退出 plan 模式。"
            )
            return
        if was_active:
            # 已经在 plan 模式时再次输入,顺手重置 reminder 让 LLM 重新看到
            yield event.plain_result(
                f"🔄 plan 模式仍激活 (会话 {umo})\n"
                f"已过滤 {len(blocked)} 个写工具:{', '.join(blocked)}\n"
                f"reminder 已重置,下次 LLM 调用时重新注入。\n"
                f"使用 /build 退出 plan 模式。"
            )
        else:
            yield event.plain_result(
                f"✅ plan 模式已激活 (会话 {umo})\n"
                f"已过滤 {len(blocked)} 个写工具:{', '.join(blocked)}\n"
                f"LLM 仅可使用只读工具调研。使用 /build 退出。"
            )

    @filter.command("build")
    async def build(self, event):
        """/build — 退出 plan 模式,回到默认 build 模式(全部工具可用)

        与 opencode /build 等价:build 是默认状态,本命令等价于"关闭 plan 模式"。
        执行后,LLM 工具列表不再被过滤,下次 LLM 调用按完整工具集处理。
        """
        umo = event.unified_msg_origin
        was_active = self._plan.deactivate(umo)
        if was_active:
            yield event.plain_result(
                f"✅ plan 模式已关闭 (会话 {umo})。所有工具现已可用。"
            )
        else:
            yield event.plain_result(
                f"ℹ️ 已在 build 模式 (会话 {umo})。所有工具默认可用,无需切换。"
            )

    # ── file-browser 端点(v3.2)─────────────────────────────────────
    #
    # 详见 docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md
    # 入口路由在 initialize() 中注册(本方法只是 view_handler)。

