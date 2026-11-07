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

from ..operation_progress import begin as _progress_begin
from ..operation_progress import finish as _progress_finish
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
# 总结 + ProjectLoadAbort)统一映射为 step 自身的 reason 码,
# 前端用 ``substep_messages`` 末尾的"❌"行显示具体错误。
_SILENT_REASON_TO_ENVELOPE: dict[str, str] = {
    "project_already_loaded": ReasonCode.NO_PROJECT_LOADED,
    "path_unsafe": ReasonCode.PATH_UNSAFE,
    "directory_missing": ReasonCode.DIRECTORY_MISSING,
    "agentsmd_load_failed": ReasonCode.GIT_ERROR,
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
        no_agentsmd (bool, optional): 跳过 AGENTS.md 注入。默认 False。
        no_codegraph (bool, optional): 兼容旧客户端保留;codegraph 已不在
            load 流水线中,该字段不再产生任何行为。
        force (bool, optional): (2026-07-28 引入) 若已加载项目,允许覆盖为
            新项目;2026-11-07 起为原子替换(新路径校验 + AGENTS.md 注入
            成功后才替换旧项目)。默认 False — 拒绝重复 load。
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
                        "agents_md": bool,            # AGENTS.md 是否已注入
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

    # ── 2.5 并发门:同 umo 只允许一个静默操作(2026-08-06)──
    #    必须在 force unload 之前——否则并发拒绝时已 unload 却不 load,
    #    留下"卸载了但没装上"的中间态。
    if not _progress_begin(umo, "project_load"):
        return _make_envelope(
            success=False,
            reason=ReasonCode.OPERATION_IN_PROGRESS,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            umo=umo,
        )

    # ── 3. 构造 dummy event + 调 silent load ──
    #    silent 路径的子方法(``agentsmd.load``)
    #    需要 ``event.unified_msg_origin`` 和 ``event.plain_result``。
    #    ``plain_result`` 用 ``lambda x: x`` 让 MagicMock 在
    #    ``project_load_step`` 内的 ``event.plain_result(...)`` 退化为
    #    字符串,被我们的 ``async for`` 接住进入 messages 列表。
    from unittest.mock import MagicMock  # 延迟到 runtime,避免 import-time 重

    silent_event = MagicMock()
    silent_event.unified_msg_origin = umo
    silent_event.plain_result = lambda text: text  # noqa: ARG005  # 兼容接口

    try:
        result = await plugin.project.load_impl_silent(
            silent_event,
            directory,
            no_agentsmd=no_agentsmd,
            no_codegraph=no_codegraph,
            create=create,
            git_init=git_init,
            replace=force,
        )
    except Exception as exc:
        # 2026-08-06: silent 方法内部已写终态;此兜底仅防未预期异常把
        # 进度记录卡在 running(finish 幂等,不覆盖已有终态)。
        logger.exception("project-load: 未捕获异常 (umo=%s)", umo)
        _progress_finish(umo, ok=False, reason="internal_error")
        return _make_envelope(
            success=False,
            reason=ReasonCode.INTERNAL_ERROR,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            umo=umo,
            substep_messages=[str(exc)],
        )

    # ── 4. 把 silent 内部 result 翻译为 envelope ──
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
        agents_md=bool(result.get("agents_md", False)),
    )
