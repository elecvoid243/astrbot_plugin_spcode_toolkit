"""
astrbot_plugin_spcode_toolkit — spcode 精简开发工具箱

为 LLM Agent 提供 7 个核心开发工具：
  - code_check（Python / C·C++ 语法+风格合并检查）
  - code_format（源代码自动格式化，写工具）
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

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import StarTools, register  # noqa: F401  (re-export for test compat: tests/test_todo_list.py uses main_mod.StarTools)

# 业务子系统 import — 详见 docs/superpowers/specs/2026-06-23-main-py-refactor-design.md
# main.py 在 PR-0~PR-7 拆分后只保留插件入口职责; 业务逻辑全部下沉到 tools/* 子包。
from .tools._config_filter import ALL_TOOL_NAMES, filter_enabled_tools
from .tools._external_tools import merge_external_tool_defaults
from .tools.codegraph import (
    CodegraphManager,
    bootstrap_mcp,
    shutdown_mcp,
)
from .tools.codegraph import state as _codegraph_state
from .tools.vivado import VivadoSubsystem, check_vivado_available
from .tools.project import ProjectManager
from .tools.webapi import register_webapi_routes
from .tools.webapi.git_diff import _GIT_DIFF_ENCODING
from .tools.inta_shell.component import LocalInteractiveShellComponent
from .tools._path_safety import is_path_safe as _is_path_safe
from .tools.security import PlanModeController, check_is_admin
from .tools.llm_inject import inject_guidance
from .tools.agentsmd import AgentsmdSubsystem
from .tools.project.inject import inject_project_path
from .tools._guidance_text import (
    PROJECT_GUIDANCE_MARKER,
    PROJECT_CODEGRAPH_GUIDANCE,
    FILE_REMOVE_GUIDANCE_MARKER,
    FILE_REMOVE_GUIDANCE,
    TODO_GUIDANCE_MARKER,
    TODO_GUIDANCE,
    CODE_CHECK_GUIDANCE_MARKER,
    CODE_CHECK_GUIDANCE,
    CODE_FORMAT_GUIDANCE_MARKER,
    CODE_FORMAT_GUIDANCE,
)

# re-export FunctionTool 类供 tests/test_*.py 旧用法 (main_mod.TodoCreateTool 等)
# v2.12 (PR-split-modify): 用 todo_add / todo_update / todo_delete 取代 todo_modify
from .tools.function_tools import (  # noqa: F401  (re-export for test compat)
    ALL_TOOL_CLASSES,
    CodeCheckTool,
    CodeFormatTool,  # v2.14
    EsSearchTool,
    FileDiffTool,
    FileRemoveTool,
    IntaShellListTool,
    IntaShellReadTool,
    IntaShellSendTool,
    IntaShellStartTool,
    IntaShellStopTool,
    TodoAddTool,
    TodoClearTool,
    TodoCreateTool,
    TodoDeleteTool,
    TodoQueryTool,
    TodoUpdateTool,
)


_DEFAULT_CONFIG = {
    "es_path": "",  # Everything es.exe 路径（空值由 AstrBot 内置 external_tools 默认路径填充）
    "git_path": "",  # git 可执行文件绝对路径;留空走系统 PATH
    "cppcheck_path": "",  # cppcheck.exe 路径（空值由 AstrBot 内置 external_tools 默认路径填充）
    "cppcheck_shortcircuit": "error",  # cppcheck 短路策略：error/warning/never（仅 auto 模式生效）
    "cppcheck_enable": [],  # cppcheck 启用的额外检查类目（空=只报 error）:warning/style/performance/portability
    "codegraph_enabled": True,  # 是否启用 codegraph MCP 集成
    "codegraph_install_dir": "",  # codegraph 安装目录（空值由 AstrBot 内置 external_tools 默认路径填充）
    "codegraph_project": "",  # codegraph daemon 默认操作的工程根目录
    "agentsmd_enabled": True,  # 是否启用 AGENTS.md 管理
    "allowed_ids": "",  # 逗号分隔额外允许的用户 ID
    "enabled_tools": [],  # 启用的工具名列表；空 = 全部禁用（安全默认）
    "file_remove_blacklist": [],  # file_remove 用户自定义黑名单：绝对路径前缀列表
    # v2.14.1: code_format 配置(从 code_format 分组拍平;LLM 不可见,
    # 注入到 CodeFormatTool 实例属性 default_style / default_indent)
    "default_style": "allman",  # astyle 默认风格
    "default_indent": 4,  # astyle 默认缩进空格数
    # v2.9.x (2026-07-27): /plan /build 命令的模式切换提示文本开关。
    # True = 保持原行为(yield 提示到消息页面);False = 静默切换。
    "plan_mode_command_feedback": True,
}


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

        # 合并配置(拍平嵌套分组,详见 _flatten_config)
        _config = merge_external_tool_defaults(_DEFAULT_CONFIG)
        if config:
            config = self._flatten_config(config)
            for k, v in config.items():
                if v not in (None, "", []):
                    _config[k] = v
        self._config = _config

        # inta_shell 配置快照(initialize 前暂存)
        # v2.23 (2026-08-11): inta_shell 配置项已从 _conf_schema.json 隐藏 —
        # AstrBot 官方核心已内置等价交互式 shell 工具(astrbot_execute_shell /
        # astrbot_shell_session)。此处固定使用默认值,不再读取用户配置。
        self._inta_shell_cfg = {
            "max_sessions": 10,
            "session_timeout": 1800,
            "block_unsafe": True,
            "default_cwd": "",
        }
        # 注入环境变量供子模块读取
        if _config.get("es_path"):
            os.environ["ES_PATH"] = _config["es_path"]
        if _config.get("cppcheck_path"):
            os.environ["CPPCHECK_PATH"] = _config["cppcheck_path"]
        os.environ["CPPCHECK_SHORTCIRCUIT"] = _config.get(
            "cppcheck_shortcircuit", "all"
        )

        # 子系统管理器句柄 — 详见 tools/*/ 子包
        self.agentsmd = AgentsmdSubsystem(plugin=self, is_path_safe=_is_path_safe)
        self.codegraph = CodegraphManager(self)
        self._vivado = VivadoSubsystem(plugin=self)
        self.project = ProjectManager(self)
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

        # file_remove_blacklist 注入(必须在 enabled_tools 过滤之后)
        for t in tools_to_register:
            if isinstance(t, FileRemoveTool):
                t.custom_blacklist = list(_config.get("file_remove_blacklist") or [])
            # v2.14.1: code_format 的 default_style/default_indent 注入(LLM 不可见,
            # 走实例属性。_conf_schema.json 的 code_format 分组已定义默认值;
            # _flatten_config 会把 code_format.{default_style, default_indent}
            # 拍平为顶层的 default_style / default_indent,与其他 flat key 风格一致。
            # 缺失时用 dataclass 默认("allman" / 4)。)
            elif isinstance(t, CodeFormatTool):
                t.default_style = str(_config.get("default_style") or "allman")
                try:
                    t.default_indent = int(_config.get("default_indent") or 4)
                except (TypeError, ValueError):
                    t.default_indent = 4

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

        # 异步启动 codegraph MCP(task 登记到 tools.codegraph.state)
        if _config.get("codegraph_enabled", True):
            _codegraph_state.set_task(asyncio.create_task(bootstrap_mcp(self)))

        # 异步启动 vivado MCP(PR-2 2026-07-23, task 登记到 tools.vivado.state)
        from .tools.vivado import state as _vivado_state_module

        if _config.get("vivado_enabled", True):
            _vivado_state_module.get_state().set_task(
                asyncio.create_task(self._vivado.bootstrap())
            )

        # git 可用性探测(失败仅记 WARNING,不阻塞插件加载)
        try:
            import subprocess as _sp
            import sys as _sys

            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            # CREATE_NO_WINDOW 仅 win32 有定义(由 subprocess 模块按平台条件导出)。
            _NO_WINDOW: dict = (
                {"creationflags": _sp.CREATE_NO_WINDOW}
                if _sys.platform == "win32"
                else {}
            )

            _git_probe = _sp.run(
                [self._git_binary(), "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                encoding=_GIT_DIFF_ENCODING,
                errors="replace",
                **_NO_WINDOW,
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

        # 注:v2.15.0 (2026-07-02) 起 /spcode/file-search 端点改用
        # ``python_ripgrep.search``(AstrBot 自带依赖,见
        # ``astrbot/core/computer/booters/local.py:13``),无需再做 ripgrep
        # 启动期探测。原先这里的 ``_rg_available`` / ``_rg_path`` 探测块与
        # ``_conf_schema.json`` 的 ``search.rg_path`` 字段均已删除。

    async def initialize(self) -> None:
        """插件激活(AstrBot 框架在 __init__ 后调用)。"""
        register_webapi_routes(self)

        # 构造 inta_shell 组件并写入 tools.inta_shell.runtime 模块级单例
        cfg = self._inta_shell_cfg
        component = LocalInteractiveShellComponent(
            max_sessions=cfg["max_sessions"],
            session_timeout_seconds=cfg["session_timeout"],
            enable_block=cfg["block_unsafe"],
        )
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
        """拍平嵌套分组: {"codegraph": {"install_dir": "..."}} → {"codegraph_install_dir": "..."}。"""
        flat: dict = {}
        for key, value in config.items():
            if isinstance(value, dict):
                flat.update(value)
            else:
                flat[key] = value
        return flat

    def _git_binary(self) -> str:
        """解析 git 二进制路径(优先级: 配置 git_path > 默认 "git")。"""
        raw = self._config.get("git_path") or "git"
        return raw.strip() or "git"

    # ── /codegraph /project /agentsmd 命令组(薄壳委托,详见 tools/*/) ──

    @filter.command_group("codegraph", alias={"cg"})
    def codegraph(self):
        """codegraph 项目管理指令组。"""
        pass

    @filter.command_group("project")
    def project(self):
        """``/project`` 指令组(无 args)。

        实际分发由子指令 :meth:`project_load` / :meth:`project_unload` /
        :meth:`project_status` 各自完成。它们从 ``event.get_message_str()``
        自己剥前缀、解析剩余 tokens、转发到 ``ProjectManager`` 的 *_impl。

        WHY (2026-07-26): AstrBot 当前版本(``astrbot.core.star.filter.command``)
        对 ``*args`` / ``*flags`` (VAR_POSITIONAL) 行为不一致 —— 它们进
        handler_params 但 validate 阶段抛 ``TypeError: _empty() takes no arguments``,
        导致带 flag 的 ``/project load <dir> no_agentsmd`` 被 framework 抛
        「参数 flags 类型错误」而不是分发到 handler。参考
        ``astrbot_plugin_compact`` 的 ``_extract_command_body`` 方案:
        子指令 handler 仅接受 ``(self, event)``,完全跳过 framework 的
        参数解析,内部从 ``event.get_message_str()`` 拿原文自己分词。
        """
        pass

    @staticmethod
    def _extract_project_command_tokens(event, sub_command: str) -> list[str]:
        """从 AstrMessageEvent 中提取 ``/project <sub_command>`` 之后的 tokens。

        不依赖 AstrBot 的 ``GreedyStr``(该类型当前版本在
        ``CommandFilter.validate_and_convert_params`` 中存在
        ``pv is GreedyStr`` 身份比较 bug,对实例恒判 False;且
        ``*args``/``*flags`` 完全不识别)。

        流程:
        1. ``event.get_message_str()`` 取原始字符串
        2. 剥掉 wake_prefix(通常为 ``/``)
        3. 剥掉 ``project <sub_command>`` 前缀(大小写不敏感)
        4. ``.split()`` → tokens(空字符串过滤)

        Args:
            event: AstrBot 消息事件。
            sub_command: 子命令名(load / unload / status)。

        Returns:
            tokens 列表。无参子命令(unload / status)返回 ``[]``。
        """
        raw = (event.get_message_str() or "").strip()
        # 1. 剥掉首字符 wake_prefix(通常为 "/")—— AstrBot 的 ``filter()``
        #    内部已经把它和命令前缀剥掉了,但 ``get_message_str()`` 仍保留
        #    原消息,这里再做一次清理保证幂等。
        if raw.startswith("/"):
            raw = raw[1:].lstrip()
        # 2. 剥掉 "project <sub_command>"
        cmd_prefix = f"project {sub_command}".lower()
        if raw.lower().startswith(cmd_prefix):
            raw = raw[len(cmd_prefix) :].lstrip()
        return [tok for tok in raw.split(" ") if tok]

    @project.command("load")
    async def project_load(self, event):
        """/project load <directory> [no_agentsmd] [no_codegraph]

        可选 flags(均放在 directory 之后,顺序无关):
        - ``no_agentsmd``: 跳过 AGENTS.md 全部子步骤(步骤 1/3 的 init + load)
        - ``no_codegraph``: 跳过 codegraph 全部子步骤(步骤 2/3 的 init + set)

        不传任何 flag 时,完整执行 4 个子步骤 + 状态登记。

        实现说明 (2026-07-26):
        - 仅签名 ``(self, event)``,**不**在 args 列表里声明
          ``directory: str`` 或 ``*flags``。原因:AstrBot framework 的
          ``CommandFilter.validate_and_convert_params`` 对
          VAR_POSITIONAL 行为不友好,会对 ``/project load <dir> no_agentsmd``
          抛 ``TypeError`` 并把整个 handler 拒绝。
        - 从 ``event.get_message_str()`` 拿全部 tokens,内部 ``.split()``,
          第一个非 flag 元素作为 directory,其余 in ``{"no_agentsmd",
          "no_codegraph"}`` 作为 flag。
        - 委托给 :meth:`tools.project.ProjectManager.handle_subcommand`,
          保持 ``load_impl`` 的单入口语义。

        Failure modes:
        - 无 tokens → yield 错误消息并 return(由 handle_subcommand 处理)。
        - tokens 全是 flag(如 ``/project load no_agentsmd no_codegraph``)→
          yield 错误消息并 return(directory 缺失)。
        """
        tokens = self._extract_project_command_tokens(event, "load")
        async for msg in self.project.handle_subcommand(event, "load", *tokens):
            yield msg
        return

    @project.command("unload")
    async def project_unload(self, event):
        """/project unload(委托给 ``ProjectManager.unload_impl``)。

        同样仅签名 ``(self, event)``(见 :meth:`project_load` 的实现说明)。
        """
        async for msg in self.project.unload_impl(event):
            yield msg
        return

    @project.command("status")
    async def project_status(self, event):
        """/project status(委托给 ``ProjectManager.status_impl``)。

        同样仅签名 ``(self, event)``(见 :meth:`project_load` 的实现说明)。
        """
        async for msg in self.project.status_impl(event):
            yield msg
        return

    def get_loaded_project(self, umo: str) -> dict | None:
        """返回指定 umo 的已加载项目信息(供 webapi / dashboard 同步访问)。"""
        return self.project.get_loaded_project(umo)

    @codegraph.command("init")
    async def codegraph_init(self, event, directory: str):
        """/codegraph init <directory>(委托给 ``CodegraphManager.init``)。"""
        async for msg in self.codegraph.init(event, directory):
            yield msg

    @codegraph.command("uninit")
    async def codegraph_uninit(self, event, directory: str):
        """/codegraph uninit <directory>(委托给 ``CodegraphManager.uninit``)。"""
        async for msg in self.codegraph.uninit(event, directory):
            yield msg

    @codegraph.command("set")
    async def codegraph_set(self, event, directory: str):
        """/codegraph set <directory>(委托给 ``CodegraphManager.set_project``)。"""
        async for msg in self.codegraph.set_project(event, directory):
            yield msg

    @filter.command_group("vivado")
    def vivado(self):
        """vivado 会话管理指令组。"""
        pass

    @staticmethod
    def _vivado_check(plugin: "SPCodeToolkit") -> tuple[bool, str | None]:
        """/vivado 子命令统一前置检查 (PR 2026-07-24)。

        三个条件全满足返回 (True, None); 任一不满足返回 (False, 错误消息)。
        handler 拿到结果后 if not ok: yield msg; return。

        与 /project 模式对齐 (tools/project/manager.py:108-115)。
        抽到独立方法避免 4 个 handler 内联重复。
        用 @staticmethod 因为不需要访问 self 任何状态 (只读 plugin 的 _config 和 context)。
        """
        ok, reason = check_vivado_available(plugin)
        if ok:
            return True, None
        messages = {
            "disabled": (
                "❌ vivado 功能已被禁用 (vivado_enabled=false)。\n"
                "   请在插件配置中启用后重启 AstrBot。"
            ),
            "not_installed": (
                "❌ vivado_mcp 未安装, /vivado 命令不可用。\n"
                "   请运行 `pip install vivado-mcp` 后重启 AstrBot。"
            ),
            "not_running": (
                "❌ vivado MCP 未运行, /vivado 命令不可用。\n"
                "   请检查 AstrBot 日志中 [vivado] bootstrap 相关错误。"
            ),
        }
        return False, messages[reason]

    @vivado.command("status")
    async def vivado_status(self, event):
        """/vivado status - 列出活跃 vivado sessions。"""
        ok, msg = self._vivado_check(self)
        if not ok:
            yield event.plain_result(msg)
            return
        async for msg in self._vivado.cmd_status(event):
            yield msg

    @vivado.command("start")
    async def vivado_start(self, event, name: str = "default"):
        """/vivado start [name] - 引导启动 vivado session（默认 name=default）。"""
        ok, msg = self._vivado_check(self)
        if not ok:
            yield event.plain_result(msg)
            return
        async for msg in self._vivado.cmd_start(event, name):
            yield msg

    @vivado.command("stop")
    async def vivado_stop(self, event, name: str):
        """/vivado stop <name> - 引导停止指定 vivado session。"""
        ok, msg = self._vivado_check(self)
        if not ok:
            yield event.plain_result(msg)
            return
        async for msg in self._vivado.cmd_stop(event, name):
            yield msg

    @vivado.command("path")
    async def vivado_path(self, event):
        """/vivado path - 显示当前 VIVADO_PATH。"""
        ok, msg = self._vivado_check(self)
        if not ok:
            yield event.plain_result(msg)
            return
        async for msg in self._vivado.cmd_path(event):
            yield msg

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
        async for msg in self.agentsmd.init(event, directory):
            yield msg

    @agentsmd.command("load")
    async def agentsmd_load(self, event, directory: str):
        """/agentsmd load <directory>"""
        if not self._config.get("agentsmd_enabled", True):
            yield event.plain_result(
                "AGENTS.md 管理功能已被禁用(agentsmd_enabled=false)。"
            )
            return
        async for msg in self.agentsmd.load(event, directory):
            yield msg

    @agentsmd.command("unload")
    async def agentsmd_unload(self, event):
        """/agentsmd unload"""
        if not self._config.get("agentsmd_enabled", True):
            yield event.plain_result(
                "AGENTS.md 管理功能已被禁用(agentsmd_enabled=false)。"
            )
            return
        yield self.agentsmd.unload(event)

    @agentsmd.command("update")
    async def agentsmd_update(self, event):
        """/agentsmd update"""
        if not self._config.get("agentsmd_enabled", True):
            yield event.plain_result(
                "AGENTS.md 管理功能已被禁用(agentsmd_enabled=false)。"
            )
            return
        async for msg in self.agentsmd.update(event):
            yield msg

    @filter.on_llm_request()
    async def _project_inject_path(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """/project load 后,每次 LLM 请求前把项目工作路径注入 system_prompt 末尾。

        委托给 ``tools/project/inject.py:inject_project_path``。
        与 ``_agentsmd_inject_to_llm_request`` 解耦(v2.22 2026-07-27):
        路径注入只看 ``tools.project.state``,不看 agentsmd state,
        也不看 ``skipped_substeps`` — ``/project load no_agentsmd``
        的空壳 load 同样注入路径。

        WHY 定义在 ``_agentsmd_inject_to_llm_request`` 之前:
            AstrBot 按注册(定义)顺序执行 OnLLMRequestEvent 钩子。
            路径钩子先执行 → 路径行追加在前,AGENTS.md 内容追加在后,
            与 v2.8~v2.21 的注入顺序(路径前缀在 AGENTS.md 内容前)一致。

        Args:
            event: AstrBot 消息事件对象。
            req: LLM 请求对象,直接修改 ``req.system_prompt`` 字段。

        Author: elecvoid243, 2026-07-27
        """
        inject_project_path(event, req)

    @filter.on_llm_request()
    async def _agentsmd_inject_to_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """/agentsmd load 后,每次 LLM 请求前把 AGENTS.md 注入到 system_prompt 末尾。

        委托给 ``self.agentsmd.on_llm_request``,业务实现在
        ``tools/agentsmd/_handlers.py:AgentsmdHandlers.on_llm_request``。

        WHY 必须挂在 ``SPCodeToolkit`` 上:
            ``@filter.on_llm_request()`` 是 AstrBot 装饰器,仅识别 ``Star``
            子类的方法作为 hook;``AgentsmdSubsystem`` 是普通类,其上的
            ``on_llm_request`` 方法框架不会调用,必须经由本方法转发。
            (PR-5 2026-06-23 拆分时漏接,导致所有 system_prompt 注入失效)

        Args:
            event: AstrBot 消息事件对象。
            req: LLM 请求对象,直接修改 ``req.system_prompt`` 字段。

        Author: elecvoid243, 2026-06-23
        """
        if not self._config.get("agentsmd_enabled", True):
            return
        await self.agentsmd.on_llm_request(event, req)

    async def terminate(self) -> None:
        """Star 框架在插件卸载/重载时调用。"""
        from .tools.inta_shell import runtime as _inta_runtime

        # 清空 agentsmd 子系统 per-umo state
        try:
            self.agentsmd.clear()
        except Exception as e:  # pragma: no cover — 防御性
            logger.warning("[agentsmd] clear state on terminate failed: %s", e)

        # 停 inta_shell 交互式 Shell 组件
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

        # vivado shutdown (PR-2 2026-07-23, before codegraph)
        await self._vivado.shutdown()

        # 取消 codegraph MCP task + 停 MCP(详见 tools/codegraph.shutdown_mcp)
        await shutdown_mcp(self)

    @filter.on_llm_request()
    async def _project_inject_codegraph_guidance(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """/project load 后,把 codegraph 优先使用指引注入到 system_prompt 末尾。

        跳过条件(任一满足即不注入):
        1. ``codegraph_enabled=False`` 全局禁用
        2. 当前 umo 未加载任何 project
        3. (2026-07-25) 用户在 ``/project load`` 时显式传 ``no_codegraph`` —
           codegraph 工具实际未启动,不应让 LLM 拿到「请用 codegraph_*」
           提示而尝试调用不存在的工具。读取 ``state.skipped_substeps``
           字段判断。

        PR-7 (2026-06-23): 已加载项目的存储已从 ``self._loaded_projects`` 迁到
        ``tools.project.state`` 模块级单例。沿用 ``get_loaded_project()``
        公开接口,避免直接触达内部 state。
        """
        if not self._config.get("codegraph_enabled", True):
            return
        umo = event.unified_msg_origin
        loaded = self.get_loaded_project(umo)
        if loaded is None:
            return
        # 2026-07-25: 用户在 /project load 时显式 no_codegraph → 跳过注入
        if "codegraph" in loaded.get("skipped_substeps", set()):
            return
        if inject_guidance(req, PROJECT_CODEGRAPH_GUIDANCE, PROJECT_GUIDANCE_MARKER):
            logger.debug(
                f"[project] 已向会话 {umo} 的 system_prompt 注入 codegraph 指引"
            )

    @filter.on_llm_request()
    async def _file_remove_inject_guidance(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """astrbot_file_remove 启用时,把"优先使用 file_remove"指引注入到 system_prompt 末尾。"""
        if not (
            "astrbot_file_remove" in self._tool_names
            or "astrbot_file_remove_tool" in self._tool_names
        ):
            return
        if inject_guidance(req, FILE_REMOVE_GUIDANCE, FILE_REMOVE_GUIDANCE_MARKER):
            logger.debug("[file_remove] 已向 system_prompt 注入优先使用指引")

    @filter.on_llm_request()
    async def _todo_inject_guidance(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """任一 todo_* 工具启用时,把"及时更新 todo 列表"约束注入到 system_prompt 末尾。

        - 触发条件:任一 todo_* 工具在 self._tool_names 中
        - 注入位置:req.system_prompt 末尾
        - 防重复:由 inject_guidance 的 marker 机制保证

        """
        _TODO_TOOL_NAMES = (
            "todo_create",
            "todo_query",
            "todo_add",
            "todo_update",
            "todo_delete",
            "todo_clear",
        )
        if not any(name in self._tool_names for name in _TODO_TOOL_NAMES):
            return
        if inject_guidance(req, TODO_GUIDANCE, TODO_GUIDANCE_MARKER):
            logger.debug("[todo] 已向 system_prompt 注入及时更新约束")

    @filter.on_llm_request()
    async def _code_check_inject_guidance(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """code_check 工具启用时,把"优先使用 code_check"指引注入到 system_prompt 末尾。

        - 触发条件:code_check 在 self._tool_names 中(独立 gate)
        - 注入位置:req.system_prompt 末尾
        - 防重复:由 inject_guidance 的 marker 机制保证
        """
        if "code_check" not in self._tool_names:
            return
        if inject_guidance(req, CODE_CHECK_GUIDANCE, CODE_CHECK_GUIDANCE_MARKER):
            logger.debug("[code_check] 已向 system_prompt 注入优先使用指引")

    @filter.on_llm_request()
    async def _code_format_inject_guidance(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """code_format 工具启用时,把"优先使用 code_format"指引注入到 system_prompt 末尾。

        - 触发条件:code_format 在 self._tool_names 中(独立 gate)
        - 注入位置:req.system_prompt 末尾
        - 防重复:由 inject_guidance 的 marker 机制保证
        """
        if "code_format" not in self._tool_names:
            return
        if inject_guidance(req, CODE_FORMAT_GUIDANCE, CODE_FORMAT_GUIDANCE_MARKER):
            logger.debug("[code_format] 已向 system_prompt 注入优先使用指引")

    @filter.on_llm_request()
    async def _vivado_inject(self, event, req: ProviderRequest):
        """向 system_prompt 注入 vivado-mcp 工具使用说明。"""
        await self._vivado.on_llm_request(event, req)

    @filter.on_llm_request()
    async def _auth_guard(self, event, req: ProviderRequest):
        """L1 鉴权:非管理员从工具列表中移除本插件工具。"""
        if not req.func_tool:
            return
        if check_is_admin(event):
            return
        # 管理员可见全部；非管理员 → 全部隐藏（spcode_toolkit 是管理员工具集）
        kept = []
        removed = []
        admin_only_mcp_servers = set(self._config.get("admin_only_mcp_servers") or [])
        for tool in req.func_tool.tools:
            if tool.name in self._tool_names:
                removed.append(tool.name)
                continue
            # PR-4 2026-07-23: mcp_<X>__* 工具, 当 X 在 admin_only_mcp_servers 列表时也隐藏
            if tool.name.startswith("mcp_"):
                rest = tool.name[4:]
                if "__" in rest:
                    server = rest.split("__", 1)[0]
                    if server in admin_only_mcp_servers:
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

    # ── /plan /build 模式(v2.8) — 业务委托 tools.security.PlanModeController ──

    @filter.on_llm_request()
    async def _plan_filter_tools(self, event, req: ProviderRequest):
        """/plan 模式钩子入口 — 委托给 PlanModeController。"""
        self._plan.filter_request(event, req)

    @filter.command("plan")
    async def plan(self, event):
        """/plan — 进入 plan 模式(过滤写工具,提示 LLM 调研而非动手)。

        v2.9.x (2026-07-27): 常规切换提示受 plan_mode_command_feedback
        配置门控(False = 静默切换,不 yield 任何消息)。配置异常警告
        (blocked 为空)不受门控,始终提示。
        """
        umo = event.unified_msg_origin
        was_active = self._plan.is_active(umo)
        self._plan.activate(umo)
        blocked = self._config.get("plan_mode_blocked_tools") or []
        show_feedback = bool(self._config.get("plan_mode_command_feedback", True))
        if not blocked:
            # 配置异常警告:即使关闭了常规反馈也要提示,否则用户无从
            # 得知 plan 模式实际没有过滤任何工具。
            yield event.plain_result(
                "⚠️ plan 模式已激活,但 plan_mode_blocked_tools 为空。\n"
                "将不会过滤任何工具。请在插件配置中填写要过滤的工具名。\n"
                "使用 /build 退出 plan 模式。"
            )
            return
        if not show_feedback:
            # 2026-07-27 fix: 静默模式也必须终止事件 — AstrBot 的
            # ProcessStage 在命令 handler 没有任何产出(无 yield /
            # 无 result)时,会把 "/plan" 当普通文本回落给 LLM,
            # 导致命令看似失效。stop_event() 标记"已处理"后,
            # ResultDecorateStage 直接 return,不发送任何消息。
            event.stop_event()
            return
        if was_active:
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
        """/build — 退出 plan 模式(回到默认 build 模式,全部工具可用)。

        v2.9.x (2026-07-27): 提示文本受 plan_mode_command_feedback
        配置门控(False = 静默切换,不 yield 任何消息)。
        """
        umo = event.unified_msg_origin
        was_active = self._plan.deactivate(umo)
        if not bool(self._config.get("plan_mode_command_feedback", True)):
            # 2026-07-27 fix: 同 plan 命令 — 静默也要 stop_event(),
            # 否则 "/build" 会被框架当普通文本发给 LLM。
            event.stop_event()
            return
        if was_active:
            yield event.plain_result(
                f"✅ plan 模式已关闭 (会话 {umo})。所有工具现已可用。"
            )
        else:
            yield event.plain_result(
                f"ℹ️ 已在 build 模式 (会话 {umo})。所有工具默认可用,无需切换。"
            )

    # ── file-browser 端点(v3.2) — 入口路由在 initialize() 中注册 ──
