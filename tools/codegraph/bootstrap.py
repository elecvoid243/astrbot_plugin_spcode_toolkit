"""MCP bootstrap:codegraph daemon 启动 + 关闭(从 main.py 提取,PR-6 2026-06-23)。

公开 API:
    build_mcp_cfg(plugin) -> dict | None
        构造 codegraph MCP client 配置(供 AstrBot func_tool_manager 启动)
    bootstrap_mcp(plugin) -> None
        启动 codegraph MCP(fire-and-forget;失败仅 logger.warning 不抛)
    shutdown_mcp(plugin) -> None
        关闭 codegraph MCP(从 main.py:terminate() 提取,处理 shutdown timeout)

对应原 main.py:
    build_mcp_cfg       <-> _build_mcp_cfg  (main.py:825-841)
    bootstrap_mcp       <-> _bootstrap_codegraph_mcp  (main.py:844-885)
    shutdown_mcp        <-> terminate() 中 codegraph 关闭段  (main.py:919-938)
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api import logger

from .._codegraph_mcp import (
    SHELL_META_RE,
    detect_codegraph_launcher,
    ensure_stdio_allowlist,
)
from . import state as _state

# 兼容老的 MCP 关闭超时异常类型(从 main.py:90-101 import 复制)
try:  # pragma: no cover — 视 AstrBot 版本而定
    from astrbot.core.provider.func_tool_manager import (  # type: ignore
        MCPShutdownTimeoutError,
    )

    _HAS_MCP_EXCEPTIONS = True
except ImportError:
    _HAS_MCP_EXCEPTIONS = False
    MCPShutdownTimeoutError = None  # type: ignore


def build_mcp_cfg(plugin) -> dict | None:
    """根据 plugin._config 构造 codegraph MCP 启动配置。

    供 bootstrap_mcp 与 CodegraphManager.set_project 重启复用。
    Returns None 如果 install_dir 未配置或验证失败(详细原因已在
    _detect_from_install_dir 内 logger.warning)。
    """
    install_dir = (plugin._config.get("codegraph_install_dir") or "").strip()
    if not install_dir:
        return None
    cfg = detect_codegraph_launcher(install_dir=install_dir)
    if not cfg:
        return None
    # 若用户在插件配置了 codegraph_project,作为默认项目注入
    project = (plugin._config.get("codegraph_project") or "").strip()
    if project and not SHELL_META_RE.search(project):
        cfg = dict(cfg)  # 浅拷贝避免污染 detect_codegraph_launcher 的输出
        cfg["args"] = list(cfg["args"]) + ["--path", project]
    return cfg


async def bootstrap_mcp(plugin) -> None:
    """插件加载后异步拉起 codegraph MCP server。失败不抛异常。

    行为:
    1. 双重 gate(防御性):__init__ 已 gate codegraph_enabled,函数本身也再 check
    2. install_dir 未配置 → logger.info 跳过
    3. build_mcp_cfg() 返回 None → logger.warning 跳过(install_dir 验证失败)
    4. 若 mcp_server.json 已注册 codegraph,先 disable 再 enable(覆盖用户手写)
    5. 启动失败仅 logger.warning,不影响 spcode 其它工具
    """
    # 防御性:即便 __init__ 已 gate,函数本身也再 check 一次,便于单测
    if not plugin._config.get("codegraph_enabled", True):
        return
    try:
        # v2.1: 必须显式配置 install_dir 才会启动 MCP(去除 auto-detect)
        install_dir = (plugin._config.get("codegraph_install_dir") or "").strip()
        if not install_dir:
            logger.info(
                "codegraph_install_dir 未配置,跳过 MCP 集成"
                "(spcode 其它工具照常工作;如需启用请配置 codegraph_install_dir)"
            )
            return

        ensure_stdio_allowlist()
        cfg = build_mcp_cfg(plugin)
        if not cfg:
            # install_dir 已配置但 _detect_from_install_dir 验证失败,
            # 详细原因已在 _detect_from_install_dir 内 logger.warning
            logger.warning(
                f"codegraph_install_dir 验证失败,MCP 不启动: {install_dir!r}"
            )
            return

        mgr = plugin.context.get_llm_tool_manager()
        # 兼容用户在 mcp_server.json 手写过 codegraph 的情况
        if "codegraph" in mgr.mcp_server_runtime:
            logger.info("检测到已注册的 codegraph MCP,先停掉再用插件配置重启")
            await mgr.disable_mcp_server("codegraph")

        await mgr.enable_mcp_server(
            name="codegraph",
            config=cfg,
            timeout=180,  # 与 AstrBot DEFAULT_ENABLE_MCP_TIMEOUT_SECONDS 一致
        )
        logger.info(
            f"codegraph MCP 已启动: {Path(cfg['command']).name} "
            f"{' '.join(cfg['args'])}"
        )
    except Exception as e:
        logger.warning(f"codegraph MCP 启动失败,spcode 其它工具不受影响: {e}")


async def shutdown_mcp(plugin) -> None:
    """关闭 codegraph MCP(terminate 时调用)。

    行为:
    1. 取消还在跑的 bootstrap task(若未完成)
    2. 停 codegraph MCP server(只杀 serve --mcp 代理子进程)
    3. shutdown 超时 → logger.warning 即可,不抛(可能留 zombie 但下次启动会清理)
    """
    # 1. 取消还在跑的 bootstrap 任务
    task = _state.get_task()
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _state.set_task(None)

    # 2. 停 codegraph MCP server
    mgr = plugin.context.get_llm_tool_manager()
    if "codegraph" in mgr.mcp_server_runtime:
        try:
            await mgr.disable_mcp_server("codegraph", timeout=15)
        except Exception as e:
            if (
                _HAS_MCP_EXCEPTIONS
                and MCPShutdownTimeoutError
                and isinstance(e, MCPShutdownTimeoutError)
            ):
                logger.warning("codegraph MCP 关闭超时,可能留有僵尸代理子进程")
            else:
                raise
