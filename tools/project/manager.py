"""/project 命令 handler(load/unload/status 流水线,PR-7 2026-06-23)。

3 步 load 流水线:
1. agentsmd (init 条件性 + load)  (委托 self._plugin.agentsmd.init/load)
2. codegraph (init + set_project)  (委托 self._plugin.codegraph.init/set_project)
3. state.put(umo, info)  记录状态

任一失败 → 抛 ProjectLoadAbort → load_impl 捕获 → return。

可选 flag(2026-07-25 增强):
- ``no_agentsmd``:跳过步骤 1/3(完整跳过 init+load)
- ``no_codegraph``:跳过步骤 2/3(完整跳过 init+set)
不传任何 flag → 完整执行全部 4 个子步骤。两个 flag 同时给 → 仅做
路径校验 + 状态登记(空壳 load)。flag 顺序无关,允许放在 directory 之后。

业务从 main.py:382-718(_project_router + project_load + _project_load_step
+ _project_load_impl + project_unload + _project_unload_impl + project_status
+ _project_status_impl + get_loaded_project)整段搬过来。
"""

from __future__ import annotations

import asyncio
import datetime as _datetime
import time as _time
from pathlib import Path

from astrbot.api.event import AstrMessageEvent

from .._path_safety import is_path_safe
from ..agentsmd import strip_surrounding_quotes
from ..operation_progress import ProgressList
from ..operation_progress import finish as _progress_finish
from . import state as _state
from .pipeline import ProjectLoadAbort, project_load_step


async def _ensure_project_dir(target: Path) -> tuple[bool, str]:
    """``create`` flag: ensure ``target`` exists as a directory.

    Creates the directory (with parents) when missing; no-op when it
    already exists as a directory; fails when the path exists but is a
    regular file (we must not silently clobber a file into a project
    root).

    Args:
        target: Resolved absolute project directory.

    Returns:
        ``(ok, message)`` — ``message`` is a ready-to-yield progress /
        error line (plain text, emoji-prefixed to match the load
        pipeline's other steps).
    """
    if target.exists() and not target.is_dir():
        return False, f"❌ 目标路径已存在但不是目录: {target}"
    if not target.exists():
        try:
            await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"❌ 创建目录失败: {exc}"
        return True, f"✅ 目录已创建: {target}"
    return True, f"ℹ️ 目录已存在,跳过创建: {target}"


async def _ensure_git_repo(target: Path) -> tuple[bool, str]:
    """``git_init`` flag: ensure ``target`` is a git repository.

    Runs ``git init`` when ``.git`` is absent; no-op when already a
    repo; fails when ``target`` is not a directory, ``git`` is not on
    PATH, or ``git init`` returns non-zero.

    Args:
        target: Resolved absolute project directory (must exist).

    Returns:
        ``(ok, message)`` — see :func:`_ensure_project_dir`.
    """
    if not target.is_dir():
        return False, f"❌ 无法 git init: 目标不是目录: {target}"
    if (target / ".git").exists():
        return True, f"ℹ️ 已是 Git 仓库,跳过 init: {target}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "init",
            cwd=str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
    except FileNotFoundError:
        return False, "❌ 未找到 git 可执行文件,无法初始化仓库"
    if proc.returncode != 0:
        detail = (err or b"").decode("utf-8", "replace").strip()
        return False, f"❌ git init 失败: {detail}"
    return True, f"✅ Git 仓库已初始化: {target}"


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

        ``/project load`` 支持位置参数 ``directory`` 之后跟若干 flag
        (顺序无关,多次出现取并集):
        ``no_agentsmd`` / ``no_codegraph`` / ``create`` / ``git_init`` /
        ``replace``。

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
            # 已知 flag 白名单(2026-07-30 扩展 create / git_init / replace)。
            # 白名单外的 token 一律忽略(向后兼容:未知 flag 静默丢弃,
            # 与 no_agentsmd / no_codegraph 的历史行为一致)。
            load_flags = {
                "no_agentsmd",
                "no_codegraph",
                "create",
                "git_init",
                "replace",
            }
            flags = set(args) & load_flags
            directory = next(
                (a for a in args if a not in load_flags),
                None,
            )
            if not directory:
                yield event.plain_result(
                    "❌ /project load 需要 <directory> 参数。"
                    "可选 flag: no_agentsmd / no_codegraph / create / "
                    "git_init / replace。"
                )
                return
            async for msg in self.load_impl(
                event,
                directory,
                no_agentsmd="no_agentsmd" in flags,
                no_codegraph="no_codegraph" in flags,
                create="create" in flags,
                git_init="git_init" in flags,
                replace="replace" in flags,
            ):
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

    async def load_impl(
        self,
        event: AstrMessageEvent,
        directory: str,
        *,
        no_agentsmd: bool = False,
        no_codegraph: bool = False,
        create: bool = False,
        git_init: bool = False,
        replace: bool = False,
    ):
        """Implementation of ``/project load <dir> [flags...]``.

        Performs the multi-step project load: feature-flag check, duplicate
        load guard, path safety, agentsmd init+load, codegraph init+set,
        records the load into state, and finally yields a summary message.

        任一子步骤失败(yield 任何以 ``❌`` 开头的消息)→ 立即中止整个 load:
        后续子方法不会被调用,state 不会被填充,
        也不会 yield "✅ 项目已加载"。``⚠️`` 不算失败
        (见 :func:`project_load_step`)。

        跳过参数(均默认 ``False``,即完整执行):
        - ``no_agentsmd``:跳过步骤 1/3 的 init + load(不动 agentsmd 子系统)。
          对应的 feature-flag 校验(``agentsmd_enabled``)对该步骤不再生效,
          用户显式选择跳过即跳过,与全局开关解耦。
        - ``no_codegraph``:跳过步骤 2/3 的 init + set(不动 codegraph 子系统)。
          同上,``codegraph_enabled`` 对该步骤不再生效。
        两个 flag 同时给 → 仅做路径校验 + 状态登记(空壳 load)。

        Args:
            event: AstrBot 事件对象。
            directory: 用户提供的项目目录路径。
            no_agentsmd: 是否跳过 AGENTS.md 子步骤。
            no_codegraph: 是否跳过 codegraph 子步骤。
            create: 目录不存在时是否自动创建(含父目录)。默认 False。
            git_init: 目录非 Git 仓库时是否自动 ``git init``。默认 False。
            replace: 已加载其他项目时是否先卸载再加载(原子覆盖)。默认 False。

        Yields:
            Plain text messages for the user。
        """
        umo = event.unified_msg_origin
        # 1. Feature flag 校验(仅校验"用户希望执行"的子集)
        #    当用户显式 no_xxx 时,即使全局开关关闭也不应阻拦
        #    (本步"该功能未启用"的判断就被用户主动 opt-out 取代了)。
        if not no_agentsmd:
            agentsmd_on = self._plugin._config.get("agentsmd_enabled", True)
            if not agentsmd_on:
                yield event.plain_result(
                    "❌ AGENTS.md 功能未启用,但 /project load 仍请求执行 agentsmd 步骤。\n"
                    "请在插件配置中打开 agentsmd_enabled,或加 no_agentsmd flag。"
                )
                return
        if not no_codegraph:
            codegraph_on = self._plugin._config.get("codegraph_enabled", True)
            if not codegraph_on:
                yield event.plain_result(
                    "❌ codegraph 功能未启用,但 /project load 仍请求执行 codegraph 步骤。\n"
                    "请在插件配置中打开 codegraph_enabled,或加 no_codegraph flag。"
                )
                return

        # 2. 重复 load 处理(replace=True 时原子卸载旧项目以覆盖)
        existing = _state.get(umo)
        if existing is not None:
            if not replace:
                yield event.plain_result(
                    f"❌ 当前会话已加载项目: {existing['directory']}\n"
                    f"请先执行 /project unload,再 load 新项目。"
                )
                return
            # replace: 在同一执行流里先卸载旧项目, 避免前端连发
            # unload+load 两条命令的竞态(unload 的 state 清理是异步的,
            # 紧随其后的 load 可能仍看到旧项目而触发上面的拦截)。
            old_dir = existing.get("directory", "")
            yield event.plain_result(f"⏳ 覆盖模式: 正在卸载旧项目 {old_dir} …")
            # 与 unload_impl / webapi _silent_unload 一致: agentsmd.unload
            # 是同步方法; 不调 codegraph.set_project(紧接着的 load 会重新
            # init+set 新目录, 旧 set 残留会被覆盖)。
            yield self._plugin.agentsmd.unload(event)
            _state.pop(umo)
            yield event.plain_result(f"✅ 旧项目已卸载, 继续加载新项目: {old_dir}")

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

        # 3.5 可选的目录创建 / Git 初始化(在路径安全校验之后、子步骤
        #     流水线之前)。放在 is_path_safe 之后是为了先确认 resolve 后
        #     的路径形态安全, 再 mkdir / git init, 避免对黑名单路径建目录。
        #     create / git_init 默认 False, 加载已存在项目时这两步整体跳过,
        #     行为与历史完全一致。
        if create:
            ok_create, msg_create = await _ensure_project_dir(target)
            yield event.plain_result(msg_create)
            if not ok_create:
                return
        if git_init:
            ok_git, msg_git = await _ensure_git_repo(target)
            yield event.plain_result(msg_git)
            if not ok_git:
                return

        # 4. 多步加载(任一子步骤失败 → 立即中止,不再登记 state)
        try:
            # 步骤 1/3: agentsmd(init 条件性 + load)
            if no_agentsmd:
                yield event.plain_result(
                    "⏭️ [1/3] AGENTS.md 步骤已跳过(用户指定 no_agentsmd)。"
                )
            else:
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
            if no_codegraph:
                yield event.plain_result(
                    "⏭️ [2/3] codegraph 步骤已跳过(用户指定 no_codegraph)。"
                )
            else:
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
        #    同步记录用户显式 opt-out 的子步骤(2026-07-25),
        #    on_llm_request 钩子据此决定是否注入对应子系统的提示文本。
        loaded_at_ts = _time.time()
        skipped: set[str] = set()
        if no_agentsmd:
            skipped.add("agentsmd")
        if no_codegraph:
            skipped.add("codegraph")
        _state.put(
            umo,
            {
                "directory": str(target),
                "loaded_at": loaded_at_ts,
                "skipped_substeps": skipped,
            },
        )

        # 6. 总结消息(根据跳过的步骤动态调整)
        if no_agentsmd and no_codegraph:
            summary_lines = "  - 设定工作目录(无子步骤)"
        elif no_agentsmd:
            summary_lines = (
                "  - 设定工作目录\n  - 载入 codegraph 索引\n  - (已跳过) AGENTS.md 注入"
            )
        elif no_codegraph:
            summary_lines = (
                "  - 设定工作目录\n"
                "  - AGENTS.md 注入到 system_prompt\n"
                "  - (已跳过) codegraph 索引"
            )
        else:
            summary_lines = (
                "  - 设定工作目录\n"
                "  - AGENTS.md 注入到 system_prompt\n"
                "  - 载入 codegraph 索引"
            )
        yield event.plain_result(
            f"✅ 项目已加载: {target}\n"
            f"已自动进行如下步骤:\n"
            f"{summary_lines}\n"
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
            # codegraph_project 未配置 → 跳过 set 不会记录 active_project。
            # 但 /project unload 的语义是"卸载项目",需清掉上一次成功 set 的记录,
            # 避免端点继续显示已卸载的路径(此状态与 MCP 实际 --path 可能不一致,
            # 需前端由 mcp_running 字段做二次判断)。
            from ..codegraph import state as _cg_state

            _cg_state.set_active_project_path("")
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

    async def load_impl_silent(
        self,
        event: AstrMessageEvent,
        directory: str,
        *,
        no_agentsmd: bool = False,
        no_codegraph: bool = False,
        create: bool = False,
        git_init: bool = False,
    ) -> dict:
        """``/project load`` 的静默变体,供 webapi 端点调用(2026-07-28 引入)。

        2026-08-06: 执行期间把每条子步骤消息镜像到
        :mod:`tools.operation_progress`(若端点层已 ``begin``),返回前写终态。
        未 ``begin`` 的直接调用(旧测试/旧调用方)自动 no-op。签名与返回
        schema 不变,前端 ``useSpcodeProjectAutoLoad`` 零改动。
        """
        result = await self._load_impl_silent_core(
            event,
            directory,
            no_agentsmd=no_agentsmd,
            no_codegraph=no_codegraph,
            create=create,
            git_init=git_init,
        )
        _progress_finish(
            event.unified_msg_origin,
            ok=bool(result.get("ok")),
            reason=result.get("reason"),
        )
        return result

    async def _load_impl_silent_core(
        self,
        event: AstrMessageEvent,
        directory: str,
        *,
        no_agentsmd: bool = False,
        no_codegraph: bool = False,
        create: bool = False,
        git_init: bool = False,
    ) -> dict:
        """``/project load`` 的静默变体,供 webapi 端点调用(2026-07-28 引入)。

        与 :meth:`load_impl` 行为完全一致(feature flag 校验、路径校验、
        4 步流水线、state 登记),**唯一差异**是不向 ``event`` 产出任何
        聊天框可见消息——所有步骤结果收集到返回的 ``dict`` 里,供
        dashboard / API 调用方直接读取。

        为什么需要这个方法?
            ``/project load`` 是用户通过聊天框输入的交互式命令,需要逐步
            反馈每个子步骤的进度(``⏳ [1/3] ...``、``❌ ... 失败``、
            ``✅ 项目已加载``)。但 dashboard 在加载项目时同样需要走完整
            流水线,如果直接调 ``load_impl``,会让 ``event.plain_result``
            把这些进度消息注入聊天框,造成不必要的噪音。dashboard 只需要
            最终结构化结果(成功 / 失败 + 原因 + 数据),由前端自行决定
            渲染方式(toast / 状态 chip / 错误弹窗)。

        设计:
            - **不是** ``async generator``——返回 ``dict``,调用方直接
              ``await plugin.project.load_impl_silent(...)`` 拿到结果。
            - 内部**仍然**通过 ``project_load_step`` 走 4 步流水线,
              复用 ProjectLoadAbort 行为。子步骤 yield 的每条消息被
              收集到 ``substep_messages`` 字段(供调用方排查失败用),
              **不**经过 ``event.plain_result``(走 ``MagicMock`` 化的
              silent_event 让 ``plain_result`` 退化为字符串)。

        Args:
            event: 兼容 ``AstrMessageEvent`` 接口的对象(仅需
                ``unified_msg_origin`` 属性 + ``plain_result`` 调用)。
                webapi 端点传 ``MagicMock(unified_msg_origin=umo,
                plain_result=lambda x: x)`` 即可。
            directory: 用户提供的项目目录路径。
            no_agentsmd: 是否跳过 AGENTS.md 子步骤。
            no_codegraph: 是否跳过 codegraph 子步骤。
            create: 目录不存在时是否自动创建(含父目录)。默认 False。
            git_init: 目录非 Git 仓库时是否自动 ``git init``。默认 False。

        Returns:
            结构化结果 dict,字段::

                {
                    "ok": bool,                # 整体成功与否
                    "directory": str,          # resolve 后的绝对路径(失败时为 "")
                    "loaded_at": float,        # 成功时为 time.time(),失败为 0
                    "skipped_substeps": list[str], # 跳过的子步骤名(已 sort)
                    "substep_messages": list[str], # 全部子步骤 yield 的消息(供调试)
                    "reason": str | None,      # 失败 reason 码
                    "previous_directory": str,  # 失败且因"重复 load"时,返回已加载目录
                }
        """
        umo = event.unified_msg_origin
        messages = ProgressList(umo)
        failed_reason: str | None = None
        target_str = ""
        skipped: set[str] = set()
        previous_directory = ""

        # 1. Feature flag 校验
        if not no_agentsmd:
            agentsmd_on = self._plugin._config.get("agentsmd_enabled", True)
            if not agentsmd_on:
                return {
                    "ok": False,
                    "directory": "",
                    "loaded_at": 0.0,
                    "skipped_substeps": [],
                    "substep_messages": messages,
                    "reason": "agentsmd_disabled",
                    "previous_directory": "",
                }
        if not no_codegraph:
            codegraph_on = self._plugin._config.get("codegraph_enabled", True)
            if not codegraph_on:
                return {
                    "ok": False,
                    "directory": "",
                    "loaded_at": 0.0,
                    "skipped_substeps": [],
                    "substep_messages": messages,
                    "reason": "codegraph_disabled",
                    "previous_directory": "",
                }

        # 2. 重复 load 拦截
        if _state.get(umo) is not None:
            previous_directory = _state.get(umo).get("directory", "")
            return {
                "ok": False,
                "directory": "",
                "loaded_at": 0.0,
                "skipped_substeps": [],
                "substep_messages": messages,
                "reason": "project_already_loaded",
                "previous_directory": previous_directory,
            }

        # 3. 路径解析与安全校验
        directory = strip_surrounding_quotes(directory)
        target = Path(directory).resolve()
        target_str = str(target)
        ok, reason = is_path_safe(
            target,
            user_blacklist=self._plugin._config.get("file_remove_blacklist"),
        )
        if not ok:
            return {
                "ok": False,
                "directory": target_str,
                "loaded_at": 0.0,
                "skipped_substeps": [],
                "substep_messages": messages,
                "reason": "path_unsafe",
                "previous_directory": "",
            }

        # 3.5 可选 create / git_init(与 load_impl 对齐, 复用同一 helper)。
        #     webapi 端点的"覆盖"语义由 handler 层的 force 预处理承担, 故
        #     silent 路径不需要 replace 参数。
        if create:
            ok_create, msg_create = await _ensure_project_dir(target)
            messages.append(msg_create)
            if not ok_create:
                return {
                    "ok": False,
                    "directory": target_str,
                    "loaded_at": 0.0,
                    "skipped_substeps": [],
                    "substep_messages": messages,
                    "reason": "create_dir_failed",
                    "previous_directory": "",
                }
        if git_init:
            ok_git, msg_git = await _ensure_git_repo(target)
            messages.append(msg_git)
            if not ok_git:
                return {
                    "ok": False,
                    "directory": target_str,
                    "loaded_at": 0.0,
                    "skipped_substeps": [],
                    "substep_messages": messages,
                    "reason": "git_init_failed",
                    "previous_directory": "",
                }

        # 4. 4 步流水线(子步骤 yield 全部收集,不回传 event)
        try:
            # 步骤 1/3: agentsmd
            if no_agentsmd:
                messages.append("⏭️ [1/3] AGENTS.md 步骤已跳过(用户指定 no_agentsmd)。")
            else:
                agents_md_path = target / "AGENTS.md"
                if not agents_md_path.exists():
                    messages.append(f"⏳ [1/3] AGENTS.md 不存在,正在 init: {target}")
                    try:
                        async for msg in project_load_step(
                            event,
                            self._plugin.agentsmd.init(event, str(target)),
                            "[1/3] AGENTS.md 初始化",
                        ):
                            messages.append(_msg_to_text(msg))
                    except ProjectLoadAbort:
                        pass
                    if _last_message_failed(messages):
                        failed_reason = "agentsmd_init_failed"
                else:
                    messages.append(
                        f"ℹ️ [1/3] AGENTS.md 已存在,跳过 init: {agents_md_path}"
                    )
                if failed_reason is None:
                    messages.append(f"⏳ [1/3] 正在 load AGENTS.md: {target}")
                    try:
                        async for msg in project_load_step(
                            event,
                            self._plugin.agentsmd.load(event, str(target)),
                            "[1/3] AGENTS.md 加载",
                        ):
                            messages.append(_msg_to_text(msg))
                    except ProjectLoadAbort:
                        pass
                    if _last_message_failed(messages):
                        failed_reason = "agentsmd_load_failed"

            # 步骤 2/3: codegraph
            if failed_reason is None:
                if no_codegraph:
                    messages.append(
                        "⏭️ [2/3] codegraph 步骤已跳过(用户指定 no_codegraph)。"
                    )
                else:
                    messages.append(f"⏳ [2/3] codegraph init: {target}")
                    try:
                        async for msg in project_load_step(
                            event,
                            self._plugin.codegraph.init(event, str(target)),
                            "[2/3] codegraph init",
                        ):
                            messages.append(_msg_to_text(msg))
                    except ProjectLoadAbort:
                        pass
                    if _last_message_failed(messages):
                        failed_reason = "codegraph_init_failed"
                    if failed_reason is None:
                        messages.append(f"⏳ [2/3] codegraph set: {target}")
                        try:
                            async for msg in project_load_step(
                                event,
                                self._plugin.codegraph.set_project(event, str(target)),
                                "[2/3] codegraph set",
                            ):
                                messages.append(_msg_to_text(msg))
                        except ProjectLoadAbort:
                            pass
                        if _last_message_failed(messages):
                            failed_reason = "codegraph_set_failed"
        except ProjectLoadAbort:
            # 2026-08-06: 各子步骤的 abort 已在步骤内被捕获并赋值
            # failed_reason;此分支仅做最外层防御兜底(理论上不可达)。
            if failed_reason is None:
                failed_reason = "agentsmd_load_failed"

        if failed_reason is not None:
            return {
                "ok": False,
                "directory": target_str,
                "loaded_at": 0.0,
                "skipped_substeps": sorted(skipped),
                "substep_messages": messages,
                "reason": failed_reason,
                "previous_directory": "",
            }

        # 5. 记录 state(仅在所有子步骤都成功后才登记)
        loaded_at_ts = _time.time()
        if no_agentsmd:
            skipped.add("agentsmd")
        if no_codegraph:
            skipped.add("codegraph")
        _state.put(
            umo,
            {
                "directory": target_str,
                "loaded_at": loaded_at_ts,
                "skipped_substeps": skipped,
            },
        )

        # 6. 总结消息(附加到 messages,供调用方调试,不写入聊天框)
        if no_agentsmd and no_codegraph:
            summary_lines = "  - 设定工作目录(无子步骤)"
        elif no_agentsmd:
            summary_lines = (
                "  - 设定工作目录\n  - 载入 codegraph 索引\n  - (已跳过) AGENTS.md 注入"
            )
        elif no_codegraph:
            summary_lines = (
                "  - 设定工作目录\n"
                "  - AGENTS.md 注入到 system_prompt\n"
                "  - (已跳过) codegraph 索引"
            )
        else:
            summary_lines = (
                "  - 设定工作目录\n"
                "  - AGENTS.md 注入到 system_prompt\n"
                "  - 载入 codegraph 索引"
            )
        messages.append(
            f"✅ 项目已加载: {target}\n已自动进行如下步骤:\n{summary_lines}\n"
        )

        return {
            "ok": True,
            "directory": target_str,
            "loaded_at": loaded_at_ts,
            "skipped_substeps": sorted(skipped),
            "substep_messages": messages,
            "reason": None,
            "previous_directory": "",
        }


def _msg_to_text(msg) -> str:
    """把 ``event.plain_result`` 返回值 / 测试 mock 字符串统一规整为 str。

    生产路径:``plain_result`` 返回 ``MessageEventResult``(``chain[0].text`` 是
    渲染后的纯文本)。测试 mock 路径:``plain_result = lambda x: x`` 直接
    返回 str。本 helper 用与 :func:`project_load_step` 同样的"防御式抽取"
    逻辑,保证 silent 路径收集到的 messages 与 yield 出去的字符串内容一致。
    """
    if isinstance(msg, str):
        return msg
    chain = getattr(msg, "chain", None)
    if chain:
        first = chain[0]
        text = getattr(first, "text", None)
        if isinstance(text, str):
            return text
    return str(msg)


def _last_message_failed(messages: list[str]) -> bool:
    """检查 :func:`project_load_step` 是否刚 yield 过 ``❌`` 开头的失败消息。

    ``project_load_step`` 失败时会 yield 一条 ``❌ <step_label> 失败,/project
    load 中止。`` 的总结消息,然后抛 ``ProjectLoadAbort``。silent 路径要复用
    这条约定——把"上一条消息是否以 ❌ 开头"作为子步骤成败信号,避免在
    silent 路径里重写一遍 step 流程。
    """
    if not messages:
        return False
    return messages[-1].startswith("❌")
