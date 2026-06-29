"""codegraph 命令 handler(/codegraph init/uninit/set)(从 main.py 提取,PR-6 2026-06-23)。

公开 API:
    CodegraphManager(plugin) — 业务 manager,提供 4 个 async-gen handler:
        - init(event, directory)         /codegraph init <dir>
        - uninit(event, directory)       /codegraph uninit <dir>
        - set_project(event, directory)  /codegraph set <dir>
        - _init_or_uninit(...)           init/uninit 共享实现(内部 helper)

对应原 main.py:
    init/uninit  <-> _codegraph_init_or_uninit  (main.py:1015-1165)
    set_project  <-> _codegraph_set_project      (main.py:941-1013)
"""
from __future__ import annotations

import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .._codegraph_mcp import (
    build_cli_launcher,
    detect_codegraph_launcher,
    ensure_stdio_allowlist,
    resolve_project_path,
)
from .._helpers import _NO_WINDOW_KWARGS
from . import state as _state
from .bootstrap import build_mcp_cfg


class CodegraphManager:
    """codegraph 命令业务 manager。

    设计意图:
    - init / uninit 走同一个 _init_or_uninit(init flag 区分)
      (与 main.py 原版保持一致 — 避免拆分两个近似方法)
    - set_project 单独实现(逻辑与 init/uninit 完全不同)
    - 所有 handler 都是 async generator(用 yield 抛消息)
    - 状态(per-dir lock)走 tools.codegraph.state,不再回写 plugin 实例属性
    """

    def __init__(self, plugin) -> None:
        self._plugin = plugin

    async def init(self, event: AstrMessageEvent, directory: str):
        """/codegraph init <dir> 命令 handler。"""
        async for msg in self._init_or_uninit(event, directory, init=True):
            yield msg

    async def uninit(self, event: AstrMessageEvent, directory: str):
        """/codegraph uninit <dir> 命令 handler。"""
        async for msg in self._init_or_uninit(event, directory, init=False):
            yield msg

    async def set_project(self, event: AstrMessageEvent, directory: str):
        """`/codegraph set <dir>` 命令实现:修改 codegraph 的默认项目根目录。

        行为:
        1. 校验目录(必须存在)
        2. 更新 plugin._config["codegraph_project"]
        3. 如果 MCP server 正在跑,重启它以应用新的 --path 参数
        4. 后续 LLM 调用的 codegraph_* 工具会以新项目为默认根
        """
        # 1. 路径校验(必须存在的目录)
        target = resolve_project_path(
            directory,
            init=True,
            user_blacklist=self._plugin._config.get("file_remove_blacklist") or [],
        )
        if isinstance(target, str):  # 错误消息
            yield event.plain_result(target)
            return

        target_str = str(target)
        # 2. 更新配置
        old = (self._plugin._config.get("codegraph_project") or "").strip()
        self._plugin._config["codegraph_project"] = target_str
        logger.info(f"codegraph_project: {old!r} → {target_str!r}")

        mgr = self._plugin.context.get_llm_tool_manager()
        mcp_running = "codegraph" in mgr.mcp_server_runtime

        # 3a. 如果 MCP 没在跑(可能 install_dir 未配置 / 验证失败),
        # 不重启,只更新 config;bootstrap 或下次重启会生效
        if not mcp_running:
            yield event.plain_result(
                f"✅ codegraph_project 已更新为: {target_str}\n"
                "   (MCP 当前未运行,新项目将在下次启动 codegraph MCP 时生效)"
            )
            return

        # 3b. MCP 在跑 → 重启以应用新 --path
        try:
            yield event.plain_result(
                f"🔄 正在重启 codegraph MCP 以应用新项目: {target_str}..."
            )
            await mgr.disable_mcp_server("codegraph", timeout=15)
        except Exception as e:
            logger.warning(f"codegraph MCP 关闭失败(可能 zombie 进程): {e}")
            # 继续尝试启动新实例

        try:
            ensure_stdio_allowlist()
            cfg = build_mcp_cfg(self._plugin)
            if not cfg:
                yield event.plain_result(
                    f"⚠️ 已更新 codegraph_project,但无法构造新 MCP 配置"
                    f"(install_dir 可能已失效)。新值: {target_str}"
                )
                return
            await mgr.enable_mcp_server(
                name="codegraph",
                config=cfg,
                timeout=180,
            )
            _state.set_active_project_path(target_str)
            yield event.plain_result(
                f"✅ codegraph 已切换到新项目: {target_str}\n"
                "   后续 LLM 调用的 codegraph_* 工具默认在此目录下操作"
            )
            logger.info(f"codegraph MCP 已重启,新 --path: {target_str}")
        except Exception as e:
            logger.warning(f"codegraph MCP 重启失败: {e}")
            yield event.plain_result(
                f"❌ codegraph MCP 重启失败: {e}\n"
                f"   (codegraph_project 已更新为 {target_str},"
                f"重启 AstrBot 后生效)"
            )

    async def _init_or_uninit(
        self,
        event: AstrMessageEvent,
        directory: str,
        *,
        init: bool,
    ):
        """init/uninit 共享实现(从 main.py:1015-1165 复制)。

        行为:
        1. 路径校验(init 要求目录下有代码文件,uninit 不要求)
        2. 找 codegraph CLI 启动器
        3. per-dir lock(防并发 init/uninit 写坏 .codegraph/)
        4. 异步执行 codegraph {init|uninit} 命令
        5. 退出码处理 + 错误消息分类
        """
        # 1. 路径校验
        #    v2.9: init 时要求目录下至少存在一个代码文件(对齐 /agentsmd init);
        #    uninit 故意跳过此检查(允许对空目录 uninit,语义上无害)。
        target = resolve_project_path(
            directory,
            init=init,
            user_blacklist=self._plugin._config.get("file_remove_blacklist") or [],
            require_code_files=init,
        )
        if isinstance(target, str):  # 错误消息
            yield event.plain_result(target)
            return

        # 2. 找 codegraph CLI 启动器
        # 优先用用户配置的 install_dir;若未配置(单次命令场景),fallback 到 auto-detect
        install_dir = (
            (self._plugin._config.get("codegraph_install_dir") or "").strip() or None
        )
        mcp_cfg = detect_codegraph_launcher(install_dir=install_dir)
        cli_launcher = build_cli_launcher(mcp_cfg)
        if not cli_launcher:
            yield event.plain_result(
                "❌ 找不到 codegraph CLI,请先 `npm install -g @colbymchenry/codegraph`"
            )
            return

        # 3. 并发锁(防止同目录并发 init/uninit 把 .codegraph/ 写坏)
        target_str = str(target)
        lock = _state.get_dir_lock(target_str)
        if lock.locked():
            yield event.plain_result(
                f"⏳ 目录 {target_str} 已有 codegraph 操作在跑,请等待完成"
            )
            return
        async with lock:
            # 4. 立即回应
            action = "初始化" if init else "反初始化"
            yield event.plain_result(
                f"⏳ 正在 {action} codegraph 项目 {target_str}...\n"
                f"   (大型项目可能耗时数分钟,期间请勿重复执行)"
            )

            # 5. 异步执行
            sub = "init" if init else "uninit"
            cmd_args = cli_launcher["args"] + [sub, target_str]
            if not init:
                cmd_args.append("--force")

            try:
                proc = await asyncio.create_subprocess_exec(
                    cli_launcher["command"],
                    *cmd_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
                    **_NO_WINDOW_KWARGS,
                )
            except FileNotFoundError as e:
                yield event.plain_result(f"❌ 启动 codegraph 失败: {e}")
                return

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                yield event.plain_result(f"❌ codegraph {sub} 超时(300s),已终止")
                return

            if proc.returncode == 0:
                if init:
                    yield event.plain_result(
                        f"✅ codegraph 初始化完成: {target_str}\n"
                        f"   下一步:在对话中用 codegraph_status 验证索引,"
                        f"或直接用 codegraph_explore 触发懒加载建索引"
                    )
                else:
                    yield event.plain_result(
                        f"✅ codegraph 反初始化完成: {target_str}\n"
                        f"   (.codegraph/ 目录已删除)"
                    )
            else:
                err = (
                    (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
                )
                err_lower = err.lower()
                if init and ("not found" in err_lower or "no such file" in err_lower):
                    yield event.plain_result(
                        f"❌ 目录不存在: {target_str}\n   请先创建或检查路径"
                    )
                elif init and (
                    "already initialized" in err_lower or "exists" in err_lower
                ):
                    # 自动用 --force 重试一次
                    yield event.plain_result(
                        "⚠️ 目标目录已初始化 codegraph,自动用 --force 重试..."
                    )
                    retry_args = cli_launcher["args"] + [
                        sub,
                        target_str,
                        "--force",
                    ]
                    proc2 = await asyncio.create_subprocess_exec(
                        cli_launcher["command"],
                        *retry_args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
                        **_NO_WINDOW_KWARGS,
                    )
                    try:
                        stdout2, stderr2 = await asyncio.wait_for(
                            proc2.communicate(), timeout=180
                        )
                        if proc2.returncode == 0:
                            yield event.plain_result(
                                f"✅ codegraph 重新初始化完成: {target_str}"
                            )
                        else:
                            err2 = (
                                (stderr2 or stdout2 or b"")
                                .decode("utf-8", errors="replace")
                                .strip()
                            )
                            yield event.plain_result(
                                f"❌ --force 重试也失败:\n{err2[:1500]}"
                            )
                    except asyncio.TimeoutError:
                        proc2.kill()
                        yield event.plain_result("❌ --force 重试超时")
                elif (not init) and (
                    "not initialized" in err_lower or "no .codegraph" in err_lower
                ):
                    yield event.plain_result(
                        "ℹ️ 目标目录未初始化 codegraph(没有 .codegraph/),无需 uninit"
                    )
                else:
                    yield event.plain_result(
                        f"❌ codegraph {sub} 失败(退出码 {proc.returncode}):\n{err[:1500]}"
                    )
