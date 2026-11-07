"""/project 命令 handler(load/unload/status 流水线,PR-7 2026-06-23)。

2026-11-07 重设计后的 load 流水线只有两步:
1. 路径校验 + 目录登记;
2. 若目录内已存在 ``AGENTS.md``,注入到 system_prompt。

不再自动生成 ``AGENTS.md``(不调用 ``agentsmd.init``),也不再初始化
codegraph 索引 —— 两者都是用户的显式动作(聊天命令或 WebUI 的
Codegraph 管理页)。这条单一流水线同时服务 ``/project load`` 聊天命令
与 ``POST /spcode/project-load`` webapi,避免两端语义漂移。

`no_agentsmd` 仍作为显式 opt-out 保留;`no_codegraph` 仅保留参数以兼容
旧调用方,实际不再有对应步骤。`replace=True` 时旧项目只在新路径完成
校验与 AGENTS.md 注入后才被替换,失败的加载不会丢掉已加载项目。
"""

from __future__ import annotations

import asyncio
import datetime as _datetime
import logging
import time as _time
from pathlib import Path

from astrbot.api.event import AstrMessageEvent

from .._helpers import _NO_WINDOW_KWARGS
from .._path_safety import is_path_safe
from ..agentsmd import strip_surrounding_quotes
from ..operation_progress import ProgressList
from ..operation_progress import finish as _progress_finish
from . import state as _state
from .pipeline import ProjectLoadAbort, project_load_step

logger = logging.getLogger(__name__)


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
            **_NO_WINDOW_KWARGS,
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
    - load_impl:3 步 load 流水线(agentsmd init+load + codegraph init)
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

        Project load only registers the working directory and, when the
        directory already contains an ``AGENTS.md``, injects that file into
        the session's system prompt. It never generates ``AGENTS.md`` and
        never initializes the codegraph index; both are explicit user
        actions.

        The chat command and the silent webapi path share
        :meth:`_load_impl_silent_core`, so there is exactly one pipeline.
        Results are collected first and yielded as a batch because the
        remaining work is a single file read (no multi-minute steps to
        stream).

        Args:
            event: AstrBot event object.
            directory: Project directory supplied by the user.
            no_agentsmd: Skip AGENTS.md injection (legacy explicit opt-out).
            no_codegraph: Accepted for backward compatibility; codegraph is
                no longer part of the project-load pipeline.
            create: Create the directory (with parents) when missing.
            git_init: Run ``git init`` when the directory is not a repository.
            replace: Replace an already-loaded project once the new path has
                been validated and its AGENTS.md loaded.

        Yields:
            Plain text messages for the user.
        """
        result = await self._load_impl_silent_core(
            event,
            directory,
            no_agentsmd=no_agentsmd,
            no_codegraph=no_codegraph,
            create=create,
            git_init=git_init,
            replace=replace,
        )
        for message in result["substep_messages"]:
            yield event.plain_result(message)

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

    async def unload_impl_silent(self, event: AstrMessageEvent) -> dict:
        """``/project unload`` 的静默变体,供 webapi 端点调用(2026-08-06 引入)。

        与 :meth:`unload_impl` 行为一致(feature flag 校验、no-op 守卫、
        agentsmd unload、codegraph 回默认、state 清理),但不向聊天框产出
        任何消息——yield 全部收集到返回 dict。每条消息经
        :class:`ProgressList` 镜像到进度存储(若端点已 begin)。

        Args:
            event: 仅需 ``unified_msg_origin`` 属性 + ``plain_result``
                可调(传 ``plain_result=lambda x: x`` 的 MagicMock)。

        Returns:
            ``{ok, directory, substep_messages, reason}``;
            reason ∈ ``None | "feature_disabled" | "no_project_loaded"``。
        """
        umo = event.unified_msg_origin
        agentsmd_on = self._plugin._config.get("agentsmd_enabled", True)
        codegraph_on = self._plugin._config.get("codegraph_enabled", True)
        if not (agentsmd_on and codegraph_on):
            _progress_finish(umo, ok=False, reason="feature_disabled")
            return {
                "ok": False,
                "directory": "",
                "substep_messages": [],
                "reason": "feature_disabled",
            }
        info = _state.get(umo)
        if info is None:
            # 2026-08-07:未加载时的卸载是幂等空转(目标状态=未加载已达成),
            # 进度写 done 而非 failed,避免 chip 误报失败。
            _progress_finish(umo, ok=True)
            return {
                "ok": False,
                "directory": "",
                "substep_messages": [],
                "reason": "no_project_loaded",
            }
        directory = info.get("directory", "")
        messages = ProgressList(umo)
        async for msg in self.unload_impl(event):
            messages.append(_msg_to_text(msg))
        _progress_finish(umo, ok=True)
        return {
            "ok": True,
            "directory": directory,
            "substep_messages": list(messages),
            "reason": None,
        }

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
        replace: bool = False,
    ) -> dict:
        """``/project load`` 的静默变体,供 webapi 端点调用(2026-07-28 引入)。

        2026-08-06: 执行期间把每条子步骤消息镜像到
        :mod:`tools.operation_progress`(若端点层已 ``begin``),返回前写终态。
        未 ``begin`` 的直接调用(旧测试/旧调用方)自动 no-op。签名与返回
        schema 只做增量扩展:``replace`` 控制原子覆盖,``agents_md`` 回报
        AGENTS.md 是否注入;旧字段保持不变。
        """
        result = await self._load_impl_silent_core(
            event,
            directory,
            no_agentsmd=no_agentsmd,
            no_codegraph=no_codegraph,
            create=create,
            git_init=git_init,
            replace=replace,
        )
        # 2026-08-07 bugfix:project_already_loaded 是幂等良性结果(目标状态
        # 已达成),进度记录必须写 done 而非 failed——否则 dashboard 轮询会把
        # "重复进入已挂载会话"显示成"加载失败",且 messages 为空(早退在任何
        # append 之前)导致失败详情弹窗无内容。端点返回契约不变。
        ok = bool(result.get("ok"))
        reason = result.get("reason")
        if not ok and reason == "project_already_loaded":
            ok = True
            reason = None
        _progress_finish(
            event.unified_msg_origin,
            ok=ok,
            reason=reason,
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
        replace: bool = False,
    ) -> dict:
        """Shared project-load pipeline for chat and webapi callers.

        Behaviour (2026-11-07 redesign):
        - Register the working directory for the umo.
        - Inject ``<directory>/AGENTS.md`` only when the file already exists.
        - Never generate ``AGENTS.md`` and never initialize codegraph.
        - ``replace=True`` swaps projects only after the new path has been
          validated and its AGENTS.md (if any) loaded, so a failed load keeps
          the previously loaded project intact.

        Args:
            event: Event-like object exposing ``unified_msg_origin`` and
                ``plain_result``.
            directory: Project directory supplied by the caller.
            no_agentsmd: Skip AGENTS.md injection.
            no_codegraph: Legacy flag kept for API compatibility; ignored.
            create: Create the directory when missing.
            git_init: Run ``git init`` when the directory is not a repository.
            replace: Replace an already-loaded project on success.

        Returns:
            Structured result dict with ``ok``, ``directory``, ``loaded_at``,
            ``skipped_substeps``, ``substep_messages``, ``reason``,
            ``previous_directory`` and ``agents_md``.
        """
        umo = event.unified_msg_origin
        messages = ProgressList(umo)
        target_str = ""
        skipped: set[str] = set()

        def _result(
            ok: bool,
            reason: str | None,
            *,
            directory: str | None = None,
            loaded_at: float = 0.0,
            previous_directory: str = "",
            agents_md: bool = False,
        ) -> dict:
            """Build the common result envelope for every exit path."""
            return {
                "ok": ok,
                "directory": target_str if directory is None else directory,
                "loaded_at": loaded_at,
                "skipped_substeps": sorted(skipped),
                "substep_messages": messages,
                "reason": reason,
                "previous_directory": previous_directory,
                "agents_md": agents_md,
            }

        # 1. Path resolution + safety. Everything after this point may touch
        #    the filesystem, so the blacklist check must come first.
        directory = strip_surrounding_quotes(directory)
        target = Path(directory).resolve()
        target_str = str(target)
        ok, reason = is_path_safe(
            target,
            user_blacklist=self._plugin._config.get("file_remove_blacklist"),
        )
        if not ok:
            messages.append(f"❌ 路径不允许: {reason}")
            return _result(False, "path_unsafe")

        # 2. Duplicate load. With replace=True the old entry stays in place
        #    until the new path has been fully validated and injected.
        existing = _state.get(umo)
        if existing is not None and not replace:
            previous_directory = existing.get("directory", "")
            messages.append(
                f"❌ 当前会话已加载项目: {previous_directory}\n"
                "请先执行 /project unload,再 load 新项目。"
            )
            return _result(
                False,
                "project_already_loaded",
                previous_directory=previous_directory,
            )

        # 3. Optional create / git_init. `no_codegraph` is intentionally a
        #    no-op now: codegraph is a manual action and never part of load.
        if create:
            ok_create, msg_create = await _ensure_project_dir(target)
            messages.append(msg_create)
            if not ok_create:
                return _result(False, "create_dir_failed")
        if git_init:
            ok_git, msg_git = await _ensure_git_repo(target)
            messages.append(msg_git)
            if not ok_git:
                return _result(False, "git_init_failed")

        if not target.exists() or not target.is_dir():
            messages.append(f"❌ 目录不存在或不是目录: {target}")
            return _result(False, "directory_missing")

        # 4. AGENTS.md: load only when it already exists. Never call init —
        #    generating a project description behind the user's back is the
        #    exact behaviour this redesign removes.
        agents_md_path = target / "AGENTS.md"
        agents_md_loaded = False
        if no_agentsmd:
            messages.append("⏭️ AGENTS.md 注入已跳过(用户指定 no_agentsmd)。")
            skipped.add("agentsmd")
        elif not self._plugin._config.get("agentsmd_enabled", True):
            messages.append("⏭️ AGENTS.md 功能未启用,已跳过注入。")
            skipped.add("agentsmd")
        elif not agents_md_path.exists():
            messages.append(f"ℹ️ 未找到 AGENTS.md,已跳过注入(不会自动生成): {target}")
            skipped.add("agentsmd")
        else:
            messages.append(f"⏳ 正在加载 AGENTS.md: {agents_md_path}")
            agentsmd_failed = False
            try:
                async for msg in project_load_step(
                    event,
                    self._plugin.agentsmd.load(event, target_str),
                    "AGENTS.md 加载",
                ):
                    messages.append(_msg_to_text(msg))
            except ProjectLoadAbort:
                # ProjectLoadAbort is the authoritative failure signal; do
                # not infer it from the rendered message prefix, which may be
                # decorated by the caller's event implementation.
                agentsmd_failed = True
            if agentsmd_failed:
                return _result(False, "agentsmd_load_failed")
            agents_md_loaded = True

        # 5. Atomic swap: drop the old project only now. If the new project
        #    has no AGENTS.md, also clear an injection that belonged to the
        #    old project's directory (a manual /agentsmd load elsewhere is
        #    left untouched).
        if existing is not None:
            previous_directory = existing.get("directory", "")
            old_agents_state = self._plugin.agentsmd.state.get(umo)
            if (
                not agents_md_loaded
                and old_agents_state is not None
                and old_agents_state.directory == previous_directory
            ):
                try:
                    self._plugin.agentsmd.unload(event)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "project-load: 清理旧 AGENTS.md 注入失败 (umo=%s): %s",
                        umo,
                        exc,
                    )
            _state.pop(umo)
            messages.append(f"✅ 已替换旧项目: {previous_directory}")

        # 6. Register the project state only after every step succeeded.
        loaded_at_ts = _time.time()
        _state.put(
            umo,
            {
                "directory": target_str,
                "loaded_at": loaded_at_ts,
                "skipped_substeps": skipped,
                "agents_md": agents_md_loaded,
            },
        )

        summary_lines = ["  - 设定工作目录"]
        if agents_md_loaded:
            summary_lines.append("  - AGENTS.md 注入到 system_prompt")
        else:
            summary_lines.append(
                "  - 未注入 AGENTS.md(文件不存在 / 功能未启用 / 已跳过)"
            )
        summary_lines.append("  - codegraph 索引未自动初始化(由用户手动执行)")
        messages.append(
            f"✅ 项目已加载: {target}\n"
            f"已自动进行如下步骤:\n"
            f"{chr(10).join(summary_lines)}\n"
        )

        return _result(
            True,
            None,
            loaded_at=loaded_at_ts,
            agents_md=agents_md_loaded,
        )


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
