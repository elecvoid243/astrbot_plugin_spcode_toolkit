"""分支会话状态惰性继承(2026-09-13)。

背景:
    ChatUI「分支会话」只复制 LLM 历史 / persona / 项目归属,不复制本插件
    的 per-umo 运行状态(已加载项目、agentsmd、worktree 激活)。分支会话
    的 ``on_llm_request`` 钩子照常触发,但状态查询为空 → 注入静默跳过,
    system_prompt 与源会话从注入点分叉 → provider 前缀缓存近全量 miss。

机制:
    钩子查状态前调用 :func:`inherit_state` —— 本 umo 无状态时,沿核心
    分支关系(``db.get_branch_relations``,child session id → source
    session id,分支创建时由 dashboard 写入)向上找到首个拥有状态的
    祖先,深拷贝其状态写入本 umo 键(惰性、一次性、写穿)。克隆后两边
    独立演化,互不回传。

边界:
    - 非分支 / 关系不可用 / 开关关闭 → 返回 None,行为与不调用一致。
    - 传递链:A→B→C 且 B 从未使用状态时,C 沿关系链上溯到 A 克隆
      (深度上限 ``_RELATION_DEPTH_LIMIT`` 防环)。
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from astrbot import logger

_RELATION_DEPTH_LIMIT = 8


def _session_id_of(umo: str) -> str:
    """umo 的最后一段即 session id(webchat umo 内不含 ':')。"""
    return umo.rsplit(":", 1)[-1]


def _with_session_id(umo: str, session_id: str) -> str:
    """把 umo 的 session id 段替换为 ``session_id``(平台/消息类型前缀不变)。"""
    return f"{umo.rsplit(':', 1)[0]}:{session_id}"


async def _branch_relations() -> dict[str, dict]:
    from astrbot.core import db_helper

    return await db_helper.get_branch_relations()


async def inherit_state(
    umo: str,
    *,
    subsystem: str,
    get_state: Callable[[str], Any | None],
    set_state: Callable[[str, Any], None],
    copy_state: Callable[[Any], Any] = copy.deepcopy,
    enabled: bool = True,
) -> Any | None:
    """返回 ``umo`` 的状态;缺失时沿分支关系惰性克隆祖先状态。

    Args:
        umo: 会话 unified_msg_origin。
        subsystem: 子系统名(日志用,如 "project" / "agentsmd")。
        get_state: 同步状态读取函数(以 umo 为 key)。
        set_state: 同步状态写入函数(以 umo 为 key)。
        copy_state: 状态克隆函数,默认 deepcopy。
        enabled: 总开关(插件配置),False 时等价于直接返回现有状态。

    Returns:
        本 umo 的状态(可能是刚克隆的);无法继承时 None。
    """
    state = get_state(umo)
    if state is not None:
        return state
    if not enabled:
        return None

    try:
        relations = await _branch_relations()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[branch-inherit] {subsystem}: 分支关系不可用: {e}")
        return None

    current = umo
    for _ in range(_RELATION_DEPTH_LIMIT):
        relation = relations.get(_session_id_of(current))
        source_session_id = (relation or {}).get("source_session_id")
        if not source_session_id:
            return None
        parent = _with_session_id(current, source_session_id)
        if parent == umo:
            return None
        parent_state = get_state(parent)
        if parent_state is not None:
            clone = copy_state(parent_state)
            set_state(umo, clone)
            logger.info(
                f"[branch-inherit] {subsystem}: 分支会话 {umo} "
                f"已从源会话 {parent} 继承状态"
            )
            return clone
        current = parent
    logger.debug(f"[branch-inherit] {subsystem}: 超出关系链深度上限 ({umo})")
    return None
