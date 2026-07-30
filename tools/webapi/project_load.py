"""POST /spcode/project-load — 静默加载项目(dashboard 调用,不在聊天框 yield)。

仿照 ``/spcode/plan-mode`` 的 POST handler(2026-07-27 引入):dashboard 直接
调本端点切换会话状态,而不是向聊天框注入 ``/plan`` / ``/build`` 用户消息。
本端点是该模式的"项目加载"版本——dashboard 在用户点 "Load Project" 按钮
时,直接调本端点,而不是向聊天框发 ``/project load <dir>``。

设计要点:
- 不调 :meth:`ProjectManager.load_impl`(async generator,yield 用户消息)
- 调 :meth:`ProjectManager.load_impl_silent`(async def,返回结构化 dict)
- 整个流程不向 ``event`` 写任何用户可见消息——**关键需求:静默**。
- body 含 ``directory`` 必传;``no_agentsmd`` / ``no_codegraph`` 可选;
  ``umo`` 必传(无 umo 等同 dashboard 没指明会话,拒绝)。
- 失败时返回 ``{success: False, reason: <code>}``,``reason`` 码
  与 :class:`ReasonCode` 风格一致;**不**引入 ``ReasonCode`` literal
  以保持单一来源,handler 内部手动映射 ``load_impl_silent`` 的 reason 串。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import ReasonCode, _make_envelope

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


# ``load_impl_silent`` 内部 reason 串 → webapi envelope reason 码 映射表。
# 设计意图:``load_impl_silent`` 用业务可读串(便于调试/日志),envelope
# 沿用 :class:`ReasonCode` 字面量以保持前端消费一致。这样 webapi 端点
# 不会被 silent 层的 reason 串变化影响(新增 silent 码时只更新本表)。
#
# 失败路径(messages 已有"❌ <step_label> 失败, /project load 中止。"
# 总结 + ProjectLoadAbort)统一映射为 step 自身的 reason 码
# (agentsmd_init_failed / agentsmd_load_failed / codegraph_init_failed /
# codegraph_set_failed),前端用 ``substep_messages`` 末尾的"❌"行
# 显示具体错误。
_SILENT_REASON_TO_ENVELOPE: dict[str, str] = {
    "agentsmd_disabled": ReasonCode.FEATURE_DISABLED,
    "codegraph_disabled": ReasonCode.FEATURE_DISABLED,
    "project_already_loaded": ReasonCode.NO_PROJECT_LOADED,
    "path_unsafe": ReasonCode.PATH_UNSAFE,
    "agentsmd_init_failed": ReasonCode.GIT_ERROR,
    "agentsmd_load_failed": ReasonCode.GIT_ERROR,
    "codegraph_init_failed": ReasonCode.GIT_ERROR,
    "codegraph_set_failed": ReasonCode.GIT_ERROR,
    # 2026-07-30: create / git_init 步骤失败映射。create 是路径/文件系统
    # 操作失败,归 PATH_UNSAFE(最接近的现有码);git_init 是 git 子进程失败,
    # 归 GIT_ERROR。前端用 substep_messages 末尾行查看具体错误。
    "create_dir_failed": ReasonCode.PATH_UNSAFE,
    "git_init_failed": ReasonCode.GIT_ERROR,
}


def _elapsed(t0: float) -> int:
    """端到端耗时(毫秒),与 webapi 其他端点一致。"""
    return int((_time.time() - t0) * 1000)


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,  # noqa: ARG001  # 保留以对齐 _wrap 注入
    body: dict | None = None,
) -> dict:
    """POST /spcode/project-load handler — 静默加载项目。

    Body (JSON):
        directory (str, required): 项目绝对路径。与 ``/project load`` 命令
            的位置参数一致;支持带/不带尾部 ``/``、单/双引号包裹。
        no_agentsmd (bool, optional): 跳过 AGENTS.md init+load。默认 False。
        no_codegraph (bool, optional): 跳过 codegraph init+set。默认 False。
        force (bool, optional): (2026-07-28 引入) 若已加载项目,允许强制
            覆盖为新项目。默认 False — 拒绝重复 load。
        create (bool, optional): (2026-07-30 引入) 目录不存在时自动创建
            (含父目录)。默认 False。
        git_init (bool, optional): (2026-07-30 引入) 目录非 Git 仓库时
            自动 ``git init``。默认 False。

    协议:
        - umo 必传(无 umo → invalid_body)。dashboard 必须告诉端点
          "这是哪个会话的 load"。这与 ``/spcode/plan-mode`` 的 umo
          必传策略一致(plan/build 切换是 per-session)。
        - ``worktree`` 字段被 _wrap 适配器注入,但本端点不使用
          (project load 总是作用于 primary directory,不针对 worktree)。
        - 成功返回 ``success=True`` + 完整 state 字段;失败返回
          ``success=False`` + reason + 失败细节。

    Returns:
        统一 envelope::

            成功:
                {
                    "success": True,
                    "reason": None,
                    "elapsed_ms": int,
                    "data": {
                        "loaded": True,
                        "directory": str,             # resolve 后的绝对路径
                        "loaded_at": float,           # time.time() 时间戳
                        "umo": str,                   # 请求方传过来的 umo
                        "skipped_substeps": list[str],  # 用户 opt-out 的子步骤
                        "substep_messages": list[str], # 子步骤日志(供前端排查)
                    }
                }

            失败:
                {
                    "success": False,
                    "reason": <ReasonCode 字符串>,
                    "elapsed_ms": int,
                    "data": {
                        "loaded": False,
                        "directory": str,             # resolve 后的目标(失败也可能填)
                        "umo": str,
                        "skipped_substeps": list[str],
                        "substep_messages": list[str], # 含失败子步骤的"❌"日志
                        "previous_directory": str,    # 仅 project_already_loaded 时填
                    }
                }
    """
    t0 = _time.time()

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
            loaded=False,
        )
    if not umo:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
            loaded=False,
        )

    directory = body.get("directory")
    no_agentsmd = body.get("no_agentsmd", False)
    no_codegraph = body.get("no_codegraph", False)
    force = body.get("force", False)
    create = body.get("create", False)
    git_init = body.get("git_init", False)

    # ── 2. 参数类型校验 ──
    if not isinstance(directory, str) or not directory.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            umo=umo,
        )
    if not isinstance(no_agentsmd, bool):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            umo=umo,
        )
    if not isinstance(no_codegraph, bool):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            umo=umo,
        )
    if not isinstance(force, bool):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            umo=umo,
        )
    if not isinstance(create, bool):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            umo=umo,
        )
    if not isinstance(git_init, bool):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            umo=umo,
        )

    # ── 3. force 时若已加载,先 unload ──
    #    WHY: dashboard 在 "切换项目" 场景下需要支持无缝覆盖。``load_impl``
    #    默认拒绝重复 load;但 webapi 端点的 ``force`` 标志允许 dashboard
    #    显式表达"我知道当前已加载,就是要换"。卸载时**也**走静默路径——
    #    不向聊天框 yield unload 进度。
    if force:
        _silent_unload(plugin, umo)

    # ── 4. 构造 dummy event + 调 silent load ──
    #    silent 路径的子方法(``agentsmd.init/load``、``codegraph.init/set``)
    #    需要 ``event.unified_msg_origin`` 和 ``event.plain_result``。
    #    ``plain_result`` 用 ``lambda x: x`` 让 MagicMock 在
    #    ``project_load_step`` 内的 ``event.plain_result(...)`` 退化为
    #    字符串,被我们的 ``async for`` 接住进入 messages 列表。
    from unittest.mock import MagicMock  # 延迟到 runtime,避免 import-time 重

    silent_event = MagicMock()
    silent_event.unified_msg_origin = umo
    silent_event.plain_result = lambda text: text  # noqa: ARG005  # 兼容接口

    result = await plugin.project.load_impl_silent(
        silent_event,
        directory,
        no_agentsmd=no_agentsmd,
        no_codegraph=no_codegraph,
        create=create,
        git_init=git_init,
    )

    # ── 5. 把 silent 内部 result 翻译为 envelope ──
    silent_reason = result.get("reason")
    envelope_reason: str | None = None
    if not result.get("ok"):
        envelope_reason = _SILENT_REASON_TO_ENVELOPE.get(
            silent_reason or "", ReasonCode.GIT_ERROR
        )
        logger.info(
            "project-load: 失败 (umo=%s, dir=%s, reason=%s): %s",
            umo,
            result.get("directory", ""),
            silent_reason,
            (result.get("substep_messages") or [""])[-1][:200],
        )
        return _make_envelope(
            success=False,
            reason=envelope_reason,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            directory=result.get("directory", ""),
            umo=umo,
            skipped_substeps=result.get("skipped_substeps", []),
            substep_messages=result.get("substep_messages", []),
            previous_directory=result.get("previous_directory", ""),
            silent_reason=silent_reason,  # 保留 silent 层业务 reason 串供调试
        )

    logger.info(
        "project-load: 成功 (umo=%s, dir=%s, skipped=%s)",
        umo,
        result.get("directory", ""),
        result.get("skipped_substeps", []),
    )
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(t0),
        loaded=True,
        directory=result.get("directory", ""),
        loaded_at=result.get("loaded_at", 0.0),
        umo=umo,
        skipped_substeps=result.get("skipped_substeps", []),
        substep_messages=result.get("substep_messages", []),
    )


def _silent_unload(plugin: SPCodeToolkit, umo: str) -> None:
    """force=true 时静默卸载当前会话已加载的项目。

    不走 :meth:`ProjectManager.unload_impl`(async generator,yield 用户消息),
    直接同步执行最简的卸载逻辑:
      1. 若 state[umo] 不存在 → no-op
      2. 调 ``agentsmd.unload(event)``(同步方法,返回字符串,丢弃)
      3. 若 ``codegraph_project`` 已配置 → 调 ``codegraph.set_project`` 切回默认
         (走 async,但 silent_unload 是 sync helper——见下方说明)
      4. ``state.pop(umo)``

    设计权衡:
        - 这是 sync 函数,不像 ``load_impl_silent`` 那样是 async def。
          原因:dashboard 的 force-load 流程是"先清后装",整个流程
          在 ``handle`` 一个 async 函数里串行执行,cleanest 写法是
          把 unload 也 async 化。但 :meth:`ProjectManager.unload_impl`
          已经是 generator,不能直接 await。最简方案:绕开 unload_impl,
          直接调底层 ``agentsmd.unload``(同步)+ ``state.pop``,
          并跳过 codegraph.set_project(因为 force-load 后会立刻
          重新 init+set 新目录,旧 set_project 残留会被覆盖)。
        - 这是"webapi force 路径"的特殊妥协,不污染主 ``/project unload``
          路径(后者仍走原 unload_impl,带 yield 反馈)。
    """
    from ..project import state as _state

    if _state.get(umo) is None:
        return

    # agentsmd.unload 是同步方法(返回字符串,直接丢弃)
    try:
        from unittest.mock import MagicMock

        silent_event = MagicMock()
        silent_event.unified_msg_origin = umo
        silent_event.plain_result = lambda text: text
        # 不 await 也不 collect — 直接调同步方法
        unload_msg = plugin.agentsmd.unload(silent_event)
        del unload_msg  # 显式不消费,避免 lint
    except Exception as exc:  # pragma: no cover - 防御
        logger.warning("project-load: force unload 阶段 agentsmd.unload 失败: %s", exc)

    # 不调 codegraph.set_project — 紧接着的 load 会重新 init+set 新目录,
    # 旧路径会自然被覆盖。跳过 set_project 既避免 await 嵌套,又
    # 避免旧 MCP 进程的残留 path 触发"找不到目录"错误。

    _state.pop(umo)
    logger.info("project-load: force unload 完成 (umo=%s)", umo)
