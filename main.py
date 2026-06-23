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
# PR-7 (2026-06-23): pathlib.Path 移到 tools/project/manager.py(load_impl 用)

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import StarTools, register  # noqa: F401  (re-export for test compat: tests/test_todo_list.py uses main_mod.StarTools)

from .tools._config_filter import ALL_TOOL_NAMES, filter_enabled_tools
# PR-6 (2026-06-23): 移除 _codegraph_mcp 5 个 import — 业务已迁到
# tools/codegraph/{bootstrap,manager}.py;main.py 只保留插件入口职责。
from .tools.codegraph import (
    CodegraphManager,
    bootstrap_mcp,
    shutdown_mcp,
)
from .tools.codegraph import state as _codegraph_state
# PR-7 (2026-06-23): /project 命令组搬到 tools/project/ 子包。
# main.py 只剩薄壳委托 + get_loaded_project 查询接口。
from .tools.project import ProjectManager
# PR-6 (2026-06-23): shutdown_mcp 在 terminate() 中使用,
# 上面 import block 需保留(避免 ruff F401 误报)。
# PR-7 (2026-06-23): 验证 — terminate() 仍调 shutdown_mcp,保留 import。
# spcode webapi handlers all live in tools/webapi/*; main.py no longer
# re-exports them.  The 6 endpoints are registered in one shot via
# ``register_webapi_routes`` below.  We still import the git-diff
# encoding constant here for the startup-time ``git --version`` probe.
from tools.webapi import register_webapi_routes
from tools.webapi.git_diff import _GIT_DIFF_ENCODING
from .tools.inta_shell.component import LocalInteractiveShellComponent
from .tools._path_safety import is_path_safe as _is_path_safe
# PR-3 (2026-06-23): L1 鉴权 + plan 模式控制器已提取到 tools/security/
from .tools.security import PlanModeController, check_is_admin
# PR-4 (2026-06-23): LLM system_prompt 注入样板集中到 tools/llm_inject.inject_guidance
from .tools.llm_inject import inject_guidance
# PR-5 (2026-06-23): AGENTS.md 子系统(4 个 /agentsmd 命令 + on_llm_request 钩子 +
# per-umo state manager)集中到 tools/agentsmd/ 子包。tools/agentsmd.py 已
# git mv 为 tools/agentsmd/_core.py,所有老符号在 tools/agentsmd/__init__.py
# re-export,老 import 路径继续可用。
from .tools.agentsmd import AgentsmdSubsystem
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
# PR-7 (2026-06-23): time / datetime 移到 tools/project/manager.py(状态格式化用)









# inta_shell 组件单例 + 默认 cwd 已迁移到 tools/inta_shell/runtime.py(PR-2 2026-06-23)
# 5 个 IntaShell*Tool 类从 tools.function_tools.* 引用

# PR-6 (2026-06-23): MCP 异常类(MCPShutdownTimeoutError / MCPInitTimeoutError) +
# _HAS_MCP_EXCEPTIONS 标志全部搬到 tools/codegraph/bootstrap.py。main.py
# 不再直接处理 MCP shutdown 超时。

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
# PR-7 (2026-06-23): _ProjectLoadAbort 异常类已搬到 tools/project/pipeline.py
# (公开 API,跨函数传播),不再在 main.py 中定义。


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

        # AGENTS.md 子系统(PR-5 2026-06-23):
        # 持有 per-umo AgentsState(替代原 main.py 的 self._loaded_agents dict),
        # 暴露 init/load/unload/update/on_llm_request 方法供 main.py 命令薄壳委托。
        self.agentsmd = AgentsmdSubsystem(
            plugin=self,
            is_path_safe=_is_path_safe,
        )

        # codegraph 子系统(PR-6 2026-06-23):
        # 业务逻辑(/codegraph init/uninit/set + MCP bootstrap/shutdown)搬到
        # tools/codegraph/ 子包;main.py 只持有 manager 实例做薄壳委托。
        # task 引用 + per-dir lock 走 tools.codegraph.state 模块级单例。
        self.codegraph = CodegraphManager(self)

        # project 子系统(PR-7 2026-06-23):
        # 业务逻辑(/project load/unload/status + 4 步流水线)搬到
        # tools/project/ 子包;main.py 只持有 manager 实例做薄壳委托。
        # loaded_projects 状态走 tools.project.state 模块级单例。
        self.project = ProjectManager(self)

        # 已加载项目(per-umo)。/project load 时填充,/project unload 时清空。
        # 与 self.agentsmd.state 平行——一个跟踪 AGENTS.md,一个跟踪整个项目组合状态。
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

        # 异步启动 codegraph MCP(不阻塞插件加载,PR-6 2026-06-23)
        # task 引用登记到 tools.codegraph.state 模块级单例,
        # shutdown_mcp 在 terminate() 取消。
        if _config.get("codegraph_enabled", True):
            _codegraph_state.set_task(asyncio.create_task(bootstrap_mcp(self)))

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
        :meth:`ProjectManager.handle_subcommand` 处理,以便单元测试
        可以直接调用。

        PR-7 (2026-06-23): 薄壳委托给 self.project (ProjectManager)。
        """
        return None

    @project.command("load")
    async def project_load(self, event, directory: str):
        """/project load <directory>

        一键加载项目到当前会话(委托给 ``ProjectManager.load_impl``)。
        PR-7 (2026-06-23): 薄壳委托给 self.project.load_impl(manager)。
        """
        async for msg in self.project.load_impl(event, directory):
            yield msg
        return

    @project.command("unload")
    async def project_unload(self, event):
        """/project unload(委托给 ``ProjectManager.unload_impl``)。
        PR-7 (2026-06-23): 薄壳委托给 self.project.unload_impl(manager)。
        """
        async for msg in self.project.unload_impl(event):
            yield msg
        return

    @project.command("status")
    async def project_status(self, event):
        """/project status(委托给 ``ProjectManager.status_impl``)。
        PR-7 (2026-06-23): 薄壳委托给 self.project.status_impl(manager)。
        """
        async for msg in self.project.status_impl(event):
            yield msg
        return

    def get_loaded_project(self, umo: str) -> dict | None:
        """返回指定 umo 的已加载项目信息(供 webapi / dashboard 同步访问)。

        PR-7 (2026-06-23): 委托给 self.project.get_loaded_project(manager)。
        """
        return self.project.get_loaded_project(umo)
    # ── /project 业务方法已提取到 tools/project/ 子包(PR-7 2026-06-23) ───
    # 4 个 _project_* 业务方法(load_step/load_impl/unload_impl/status_impl)
    # + _project_router + 原 get_loaded_project body 都搬到 tools/project/manager.py。
    # main.py 只剩 thin shell command + get_loaded_project 查询接口。

    @codegraph.command("init")
    async def codegraph_init(self, event, directory: str):
        """/codegraph init <directory>

        初始化指定目录为 codegraph 项目(创建 .codegraph/ 索引)。
        PR-6 (2026-06-23): 薄壳委托给 self.codegraph.init(manager)。
        """
        async for msg in self.codegraph.init(event, directory):
            yield msg

    @codegraph.command("uninit")
    async def codegraph_uninit(self, event, directory: str):
        """/codegraph uninit <directory>

        反初始化指定目录(删除 .codegraph/ 索引)。
        PR-6 (2026-06-23): 薄壳委托给 self.codegraph.uninit(manager)。
        """
        async for msg in self.codegraph.uninit(event, directory):
            yield msg

    @codegraph.command("set")
    async def codegraph_set(self, event, directory: str):
        """/codegraph set <directory>

        修改 codegraph 的默认执行目录。后续 LLM 调用的 codegraph_* 工具
        会以新目录为根。
        PR-6 (2026-06-23): 薄壳委托给 self.codegraph.set_project(manager)。
        """
        async for msg in self.codegraph.set_project(event, directory):
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
    async def terminate(self):
        """Star 框架在插件卸载/重载时调用。

        PR-6 (2026-06-23):
        - 业务代理给 tools.codegraph.shutdown_mcp(取消 task + 停 MCP)
        - 保留 inta_shell 关闭段(与 codegraph 无关,业务在 tools.inta_shell.runtime)
        """
        # PR-2 (2026-06-23): 组件状态从 main.py 模块级变量改为
        # tools.inta_shell.runtime 单例。这里延迟 import 避免循环依赖。
        from .tools.inta_shell import runtime as _inta_runtime

        # PR-5 (2026-06-23): 清空 agentsmd 子系统 per-umo state。
        # 避免插件重载后持有已不存在会话的 state。
        try:
            self.agentsmd.clear()
        except Exception as e:  # pragma: no cover — 防御性
            logger.warning("[agentsmd] clear state on terminate failed: %s", e)

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

        # PR-6 (2026-06-23): 取消 task + 停 MCP 都搬到 tools.codegraph.shutdown_mcp
        await shutdown_mcp(self)

    # ── /codegraph 业务方法已提取到 tools/codegraph/ 子包(PR-6 2026-06-23) ───
    # 4 个 /codegraph 命令(init/uninit/set)的实现 + MCP bootstrap/shutdown
    # 都委托给 self.codegraph (CodegraphManager 实例),详见 tools/codegraph/。

    # ── /agentsmd 业务方法已提取到 tools/agentsmd/ 子包(PR-5 2026-06-23) ───
    # 4 个 /agentsmd 命令(init/load/unload/update)的实现 + on_llm_request 钩子

    # ── /codegraph 业务方法已提取到 tools/codegraph/ 子包(PR-6 2026-06-23) ───
    # 4 个 /codegraph 命令(init/uninit/set)的实现 + MCP bootstrap/shutdown
    # 都委托给 self.codegraph (CodegraphManager 实例),详见 tools/codegraph/。

    # ── /agentsmd 业务方法已提取到 tools/agentsmd/ 子包(PR-5 2026-06-23) ───
    # 4 个 /agentsmd 命令(init/load/unload/update)的实现 + on_llm_request 钩子
    # 都委托给 self.agentsmd (AgentsmdSubsystem 实例),详见 tools/agentsmd/。

    @filter.on_llm_request()
    async def _project_inject_codegraph_guidance(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """/project load 后,把 codegraph 优先使用指引注入到 system_prompt 末尾。

        与 self.agentsmd.on_llm_request 平行,但走独立 marker 防重复。
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

