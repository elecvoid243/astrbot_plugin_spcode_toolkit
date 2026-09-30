"""_stdio_allowlist — 确保 spcode 的 MCP 启动命令能通过 AstrBot 的 stdio 白名单。

WHY: AstrBot 的白名单解析优先级是
    ``ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS``(env, 非空)
  > ``mcp_settings.stdio_allowlist``(config, 非空)
  > 内置默认列表
两处都是"非空即整体替换",没有 union。旧实现(main.py PR-6 之前留下)无条件往
env 追加 ``{codegraph,node}`` / ``{python,pythonw,vivado_mcp}``,在"用户什么都没
设"的场景下会把内置默认(17 项)挤成 2~5 项,并把 Dashboard 上配置的名单一并遮蔽
—— 实测已导致 ``npx`` / ``uv`` / ``python3`` 启动的 stdio MCP server 被拒。

现改为探针驱动:
1. 命令已在生效名单 → 什么都不写(常见情形,零副作用);
2. 命令在黑名单 → 只告警(黑名单优先,写了也没用);
3. 缺失且 env 非空 → env 是用户显式 pin 的策略,只告警,不自作主张拓宽;
4. 缺失且 Dashboard 已配名单 → 追加该命令;
5. 缺失且两边都空 → 把「内置默认 ∪ 命令」落成 Dashboard 名单(避免替换语义
   把默认项挤掉)。

不变量:本模块**永不写 env**。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_ENV_VAR = "ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"
_CONFIG_KEY = "mcp_settings"
_CONFIG_ALLOWLIST_KEY = "stdio_allowlist"


@dataclass(frozen=True)
class _Policy:
    """core 白名单解析入口(惰性解析出的函数引用)。

    Attributes:
        normalize: 命令名归一化(basename + 去扩展名 + 小写)。
        allowlist: 当前生效白名单(env / config / 内置默认三者之一)。
        denylist: 黑名单(与白名单独立,优先级更高)。
        defaults: core 内置默认名单。
    """

    normalize: Callable[[str], str]
    allowlist: Callable[[], set[str]]
    denylist: Callable[[], set[str]]
    defaults: frozenset[str]


def _load_policy() -> _Policy | None:
    """惰性加载 core 的白名单机制。

    Returns:
        _Policy;老 core 没有这套机制(或 stub 环境)时返回 None,调用方应视作
        "无需干预"。
    """
    try:
        from astrbot.core.agent.mcp_client import (
            _DEFAULT_STDIO_COMMAND_ALLOWLIST,
            _get_stdio_command_allowlist,
            _get_stdio_command_denylist,
            _normalize_stdio_command_name,
        )
    except ImportError:
        return None
    return _Policy(
        normalize=_normalize_stdio_command_name,
        allowlist=_get_stdio_command_allowlist,
        denylist=_get_stdio_command_denylist,
        defaults=frozenset(_DEFAULT_STDIO_COMMAND_ALLOWLIST),
    )


def _read_config_allowlist() -> list[str]:
    """读 Dashboard -> 设置 -> 安全 里的 ``mcp_settings.stdio_allowlist``。

    Returns:
        当前配置的名单;读不到(老 core / stub 环境 / 类型异常)时返回空列表。
    """
    try:
        from astrbot.core import astrbot_config
    except ImportError:
        return []
    settings = astrbot_config.get(_CONFIG_KEY, {})
    if not isinstance(settings, dict):
        return []
    value = settings.get(_CONFIG_ALLOWLIST_KEY, [])
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def _write_config_allowlist(items: list[str]) -> bool:
    """把名单写回 AstrBot 配置。

    内存写入即刻对后续 ``validate_mcp_stdio_config`` 生效;落盘是 best-effort
    (失败只 warning,不影响本次启动)。

    Returns:
        True = 内存配置已更新;False = 配置面不可用(调用方应放弃 enable)。
    """
    try:
        from astrbot.core import astrbot_config
    except ImportError:
        return False

    settings = astrbot_config.get(_CONFIG_KEY, {})
    if not isinstance(settings, dict):
        settings = {}
    settings[_CONFIG_ALLOWLIST_KEY] = list(items)
    astrbot_config[_CONFIG_KEY] = settings

    save = getattr(astrbot_config, "save_config", None)
    if callable(save):
        try:
            save()
        except OSError as exc:
            logger.warning("mcp_settings.stdio_allowlist 落盘失败(内存已生效): %s", exc)
    return True


def ensure_stdio_command(command: str) -> bool:
    """确保 ``command`` 能通过 AstrBot 的 stdio 白名单校验。

    Args:
        command: MCP server 的启动命令(路径或裸命令名均可)。

    Returns:
        True = 可以继续 ``enable_mcp_server``;False = 会被 core 拒绝,调用方应
        跳过 enable 并向用户说明(具体原因本函数已 logger)。
    """
    policy = _load_policy()
    if policy is None:
        return True

    if not command or not command.strip():
        # 空命令交给 core 自己去报错,别把空串写进名单
        return True

    name = policy.normalize(command)

    if name in policy.denylist():
        logger.warning(
            "MCP stdio 命令 `%s` 命中 AstrBot 黑名单(黑名单优先),无法放行;"
            "如需使用请调整 Dashboard -> 设置 -> 安全 中的 stdio 规则。",
            name,
        )
        return False

    if name in policy.allowlist():
        return True

    return _repair(policy, name)


def _repair(policy: _Policy, name: str) -> bool:
    """命令不在生效名单时,按"用户是否表过态"分流修复。"""
    pinned = os.environ.get(_ENV_VAR, "")
    if pinned.strip():
        logger.warning(
            "%s 已被显式设置(%s),其中不含 `%s`。插件不会改写用户 pin 的策略:"
            "请在该环境变量中补上 `%s`,或清空它以改用内置默认 / Dashboard 配置。",
            _ENV_VAR,
            pinned,
            name,
            name,
        )
        return False

    configured = _read_config_allowlist()
    if configured:
        known = {x.lower() for x in configured}
        merged = [*configured, *sorted({name} - known)]
        ok = _write_config_allowlist(merged)
        if ok:
            logger.info(
                "MCP stdio 命令 `%s` 不在 Dashboard 名单中,已追加到 "
                "mcp_settings.stdio_allowlist(原值: %s)",
                name,
                configured,
            )
        return ok

    merged = sorted(policy.defaults | {name})
    ok = _write_config_allowlist(merged)
    if ok:
        logger.info(
            "内置默认名单不含 `%s`,已把「内置默认 ∪ {%s}」写入 "
            "mcp_settings.stdio_allowlist(共 %d 项,可在 Dashboard 调整)",
            name,
            name,
            len(merged),
        )
    return ok
