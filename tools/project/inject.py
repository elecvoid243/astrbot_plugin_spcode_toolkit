"""/project load 后的项目路径注入(v2.22 2026-07-27,解耦自 agentsmd)。

背景:
    v2.8~v2.21 期间,"你正在处理的项目工作路径为: ..." 前缀由 agentsmd
    子系统的 ``build_injection(content, directory=...)`` 附带注入,
    依附于 ``/agentsmd load`` 的 per-umo state。这导致
    ``/project load <dir> no_agentsmd`` 时路径无法注入。

    v2.22 解耦:路径注入改由 project 子系统独立负责,数据源为
    ``tools.project.state`` 模块级单例(/project load 第 5 步登记)。

设计:
- 数据源: ``tools.project.state.get(umo)``
- 注入器: ``tools.llm_inject.inject_guidance``(marker 防重复)
- 文本: ``tools._guidance_text.PROJECT_PATH_GUIDANCE_TEMPLATE``
- 门控: 无配置开关(2026-07-27 用户决策) — 只要 state 中有已加载
  项目且 ``directory`` 非空就注入;``/project unload`` 清空 state
  后自然停止。

与 codegraph 指引注入(main.py ``_project_inject_codegraph_guidance``)
的区别:
- codegraph 指引受 ``codegraph_enabled`` + ``skipped_substeps`` 门控
- 路径注入**不看** ``skipped_substeps`` — ``no_agentsmd`` /
  ``no_codegraph`` 的空壳 load 也要注入路径(这是解耦的核心动机)

Author: elecvoid243, 2026-07-27
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .._guidance_text import PROJECT_PATH_GUIDANCE_TEMPLATE, PROJECT_PATH_MARKER
from ..llm_inject import inject_guidance
from . import state as _state

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.provider import ProviderRequest

logger = logging.getLogger(__name__)


def inject_project_path(event: AstrMessageEvent, req: ProviderRequest) -> bool:
    """若当前会话已 ``/project load``,把项目工作路径注入 system_prompt 末尾。

    行为:
    - 未加载项目(state 无记录) → no-op,返回 False
    - state 中 ``directory`` 缺失/为空(防御旧状态) → no-op,返回 False
    - 已加载 → ``inject_guidance`` 追加路径声明文本(marker 防重复)

    不看 ``skipped_substeps`` — 即使 ``/project load`` 时传了
    ``no_agentsmd`` / ``no_codegraph``,路径仍注入。

    Args:
        event: AstrBot 消息事件对象(取 ``unified_msg_origin`` 查 state)。
        req: LLM 请求对象,直接修改 ``req.system_prompt`` 字段。

    Returns:
        ``True`` 表示本次注入了路径;``False`` 表示跳过
        (未加载 / directory 为空 / marker 已存在)。
    """
    umo = event.unified_msg_origin
    info = _state.get(umo)
    if info is None:
        return False
    directory = (info.get("directory") or "").strip()
    if not directory:
        return False
    text = PROJECT_PATH_GUIDANCE_TEMPLATE.format(directory=directory)
    if inject_guidance(req, text, PROJECT_PATH_MARKER):
        logger.debug(
            f"[project] 已向会话 {umo} 的 system_prompt 注入项目路径: {directory}"
        )
        return True
    return False
