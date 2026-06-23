"""/project 命令 handler(load/unload/status 流水线,PR-7 2026-06-23)。

3 步 load 流水线:
1. agentsmd (init 条件性 + load)  (委托 self._plugin.agentsmd.init/load)
2. codegraph (init + set_project)  (委托 self._plugin.codegraph.init/set_project)
3. state.put(umo, info)  记录状态

任一失败 → 抛 ProjectLoadAbort → load_impl 捕获 → return。

业务从 main.py:382-718(_project_router + project_load + _project_load_step
+ _project_load_impl + project_unload + _project_unload_impl + project_status
+ _project_status_impl + get_loaded_project)整段搬过来。
"""
from __future__ import annotations

import datetime as _datetime
import time as _time
from pathlib import Path

from astrbot.api.event import AstrMessageEvent

from ..agentsmd import strip_surrounding_quotes
from .._path_safety import is_path_safe
from . import state as _state
from .pipeline import ProjectLoadAbort, project_load_step


class ProjectManager:
    """管理 /project 命令组的命令 handler。

    设计意图:
    - handle_subcommand:路由器,分发到 load/unload/status
    - load_impl:4 步 load 流水线(agentsmd init+load + codegraph init+set)
    - unload_impl:agentsmd unload + codegraph set default + state.pop
    - status_impl:读 state,格式化输出
    - get_loaded_project:供其他模块(webapi / dashboard)查 loaded state
    - 所有 handler 都是 async generator(用 yield 抛消息)
    - loaded_projects 状态走 tools.project.state 模块级单例
    """

    def __init__(self, plugin) -> None:
        self._plugin = plugin

    async def handle_subcommand(
        self,
        event: AstrMessageEvent,
        sub_command: str,
        *args,
    ):
        """Implementation of the ``/project`` command group.

        Dispatches ``sub_command`` to the matching ``_impl`` helper
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
            async for msg in self.load_impl(event, args[0]):
                yield msg
            return
        if sub == "unload":
            async for msg in self.unload_impl(event):
                yield msg
            return
        if sub == "status":
            async for msg in self.status_impl(event):
                yield msg
            return
        # Unknown subcommand.
        yield event.plain_result(
            f"❌ 未知子命令: {sub_command!r}。支持: load / unload / status"
        )
        return

    async def load_impl(self, event: AstrMessageEvent, directory: str):
        """Implementation of ``/project load <dir>``.

        Performs the multi-step project load: feature-flag check, duplicate
        load guard, path safety, agentsmd init+load, codegraph init+set,
        records the load into state, and finally yields a summary message.

        任一子步骤失败(yield 任何以 ``❌`` 开头的消息)→ 立即中止整个 load:
        后续子方法不会被调用,state 不会被填充,
        也不会 yield "✅ 项目已加载"。``⚠️`` 不算失败
        (见 :func:`project_load_step`)。

        Args:
            event: AstrBot 事件对象。
            directory: 用户提供的项目目录路径。

        Yields:
            Plain text messages for the user。
        """
        umo = event.unified_msg_origin
        # 1. Feature flag 校验
        agentsmd_on = self._plugin._config.get("agentsmd_enabled", True)
        codegraph_on = self._plugin._config.get("codegraph_enabled", True)
        if not (agentsmd_on and codegraph_on):
            yield event.plain_result(
                "❌ /project 命令需要先启用 codegraph 和 AGENTS.md 功能。\n"
                "请在插件配置中打开这两项后再试一次。"
            )
            return

        # 2. 重复 load 拦截
        if _state.get(umo) is not None:
            loaded = _state.get(umo)
            yield event.plain_result(
                f"❌ 当前会话已加载项目: {loaded['directory']}\n"
                f"请先执行 /project unload,再 load 新项目。"
            )
            return

        # 3. 路径解析与安全校验
        directory = strip_surrounding_quotes(directory)
        target = Path(directory).resolve()
        ok, reason = is_path_safe(
            target,
            user_blacklist=self._plugin._config.get("file_remove_blacklist"),
        )
        if not ok:
            yield event.plain_result(f"❌ 路径不允许: {reason}")
            return

        # 4. 多步加载(任一子步骤失败 → 立即中止,不再登记 state)
        try:
            # 步骤 1/3: agentsmd(init 条件性 + load)
            agents_md_path = target / "AGENTS.md"
            if not agents_md_path.exists():
                yield event.plain_result(
                    f"⏳ [1/3] AGENTS.md 不存在,正在 init: {target}"
                )
                async for msg in project_load_step(
                    event,
                    self._plugin.agentsmd.init(event, str(target)),
                    "[1/3] AGENTS.md 初始化",
                ):
                    yield msg
            else:
                yield event.plain_result(
                    f"ℹ️ [1/3] AGENTS.md 已存在,跳过 init: {agents_md_path}"
                )
            yield event.plain_result(f"⏳ [1/3] 正在 load AGENTS.md: {target}")
            async for msg in project_load_step(
                event,
                self._plugin.agentsmd.load(event, str(target)),
                "[1/3] AGENTS.md 加载",
            ):
                yield msg

            # 步骤 2/3: codegraph init + set(PR-6 委托给 manager)
            yield event.plain_result(f"⏳ [2/3] codegraph init: {target}")
            async for msg in project_load_step(
                event,
                self._plugin.codegraph.init(event, str(target)),
                "[2/3] codegraph init",
            ):
                yield msg

            yield event.plain_result(f"⏳ [2/3] codegraph set: {target}")
            async for msg in project_load_step(
                event,
                self._plugin.codegraph.set_project(event, str(target)),
                "[2/3] codegraph set",
            ):
                yield msg
        except ProjectLoadAbort:
            return

        # 5. 记录状态(仅在所有子步骤都成功后才登记)
        loaded_at_ts = _time.time()
        _state.put(
            umo,
            {
                "directory": str(target),
                "loaded_at": loaded_at_ts,
            },
        )

        yield event.plain_result(
            f"✅ 项目已加载: {target}\n"
            f"已自动进行如下步骤:\n"
            f"  - 设定工作目录\n"
            f"  - AGENTS.md 注入到 system_prompt\n"
            f"  - 载入 codegraph 索引\n"
            f"\n若要卸载，请执行`/project unload`\n"
        )

    async def unload_impl(self, event: AstrMessageEvent):
        """Implementation of ``/project unload``.

        Unloads the current session's project: feature-flag check, no-op guard,
        agentsmd unload, codegraph set to default, and finally clears state.

        Args:
            event: AstrBot 事件对象。

        Yields:
            Plain text messages for the user.
        """
        # 1. Feature flag 校验
        agentsmd_on = self._plugin._config.get("agentsmd_enabled", True)
        codegraph_on = self._plugin._config.get("codegraph_enabled", True)
        if not (agentsmd_on and codegraph_on):
            yield event.plain_result(
                "❌ /project 命令需要先启用 codegraph 和 AGENTS.md 功能。\n"
                "请在插件配置中打开这两项后再试一次。"
            )
            return

        umo = event.unified_msg_origin
        if _state.get(umo) is None:
            yield event.plain_result("ℹ️ 当前会话未加载项目,无需 unload。")
            return

        # 2. agentsmd unload(同步返回单条消息)
        yield self._plugin.agentsmd.unload(event)

        # 3. codegraph set 回默认项目
        default_project = (self._plugin._config.get("codegraph_project") or "").strip()
        if default_project:
            yield event.plain_result(f"⏳ codegraph set 回默认项目: {default_project}")
            async for msg in self._plugin.codegraph.set_project(event, default_project):
                yield msg
        else:
            yield event.plain_result(
                "ℹ️ codegraph_project 未配置,跳过 codegraph set。"
                "MCP 当前默认项目维持原状。"
            )

        # 4. 清理状态(必须在最末,即便 set 失败也清,避免用户无法重试)
        info = _state.pop(umo)
        yield event.plain_result(
            f"✅ 项目已卸载: {info['directory']}\n"
            f"  - AGENTS.md 注入已移除\n"
            f"  - codegraph 默认项目已重置\n"
        )

    async def status_impl(self, event: AstrMessageEvent):
        """Implementation of ``/project status``.

        Reads ``state[umo]`` and yields a human-readable status for the
        chat response. The authoritative state used by the dashboard's
        spcode chip is exposed separately via the webapi handler at
        ``GET /spcode/project-status`` — that endpoint is the single
        source of truth for the dashboard; the chat response is plain
        text and intentionally does NOT carry any hidden marker.

        Args:
            event: AstrBot 事件对象。

        Yields:
            Plain text messages for the user.
        """
        umo = event.unified_msg_origin
        info = _state.get(umo)
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
        """返回指定 umo 的已加载项目信息(供 webapi / dashboard 同步访问)。"""
        return _state.get(umo)
