"""AGENTS.md 命令与 LLM 注入 hook 实现。

PR-5 (2026-06-23): 从 main.py 的 4 个 ``_agentsmd_*`` 方法 +
``_agentsmd_inject_to_llm_request`` 抽出,集中到 ``AgentsmdHandlers`` 类。

职责:
- ``init``: 通过 LLM 生成 AGENTS.md 并写入
- ``load``: 读取 AGENTS.md 并注册到 per-umo state
- ``unload``: 注销当前 umo 的 state
- ``update``: 用 LLM 重新生成已加载的 AGENTS.md
- ``on_llm_request``: 每次 LLM 请求前注入 AGENTS.md 到 system_prompt 末尾

依赖:
- self._state: AgentsStateManager(per-umo state)
- self._plugin: 反向引用到主插件(用于访问 context / config)
- self._is_path_safe: ``is_path_safe`` callable
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ._core import (
    CODE_FILE_EXTENSIONS,
    INJECTION_MARKER,
    build_injection,
    generate_agents_md_via_llm,
    has_code_files,
    resolve_init_template,
    scan_project_context,
    strip_code_fence,
    strip_surrounding_quotes,
)
from ._state import AgentsState, AgentsStateManager

logger = logging.getLogger(__name__)


class AgentsmdHandlers:
    """/agentsmd 子命令实现 + on_llm_request 钩子。

    构造时只注入 state manager + 反向引用到 plugin;不直接订阅
    AstrBot filter(由 plugin 在 startup 时把命令 / 钩子委托到本类的
    ``init``/``load``/``unload``/``update``/``on_llm_request`` 方法)。
    """

    def __init__(
        self,
        state: AgentsStateManager,
        plugin_getter,
        is_path_safe,
    ) -> None:
        self._state = state
        # plugin_getter: callable -> plugin instance(避免循环引用)
        self._plugin = plugin_getter
        # 路径安全校验 callable(避免硬编码 _is_path_safe)
        self._is_path_safe = is_path_safe

    # ── /agentsmd init <directory> ──────────────────

    async def init(self, event, directory: str):
        """/agentsmd init <directory> 实现。

        行为:
        1. 路径校验(黑名单 + resolve)
        2. 必须存在且是目录,且含代码文件
        3. 不允许覆盖既有 AGENTS.md(用户须先删)
        4. 调用 LLM 生成内容,写入 <target>/AGENTS.md
        """
        directory = strip_surrounding_quotes(directory)
        target = Path(directory).resolve()

        plugin = self._plugin()
        ok, reason = self._is_path_safe(
            target, user_blacklist=plugin._config.get("file_remove_blacklist")
        )
        if not ok:
            yield event.plain_result(f"❌ 路径不允许: {reason}")
            return

        if not target.exists():
            yield event.plain_result(
                f"❌ 目录 `{directory}` 不存在。\n请先创建该目录,或确认路径是否正确。"
            )
            return
        if not target.is_dir():
            yield event.plain_result(f"❌ `{directory}` 不是一个有效的目录。")
            return

        if not has_code_files(target):
            supported = ", ".join(f".{ext}" for ext in sorted(CODE_FILE_EXTENSIONS))
            yield event.plain_result(
                f"❌ 目录 `{directory}` 下未找到代码文件。\n"
                f"AGENTS.md 仅用于代码项目,支持的后缀: {supported}\n"
                "请确认目录是否正确,或选择包含源代码的目录。"
            )
            return

        agents_md_path = target / "AGENTS.md"
        if agents_md_path.exists():
            yield event.plain_result(
                f"⚠️ 目录 `{directory}` 下已存在 AGENTS.md。\n"
                "如需重新生成,请先删除该文件后再执行 init。"
            )
            return

        yield event.plain_result(f"🔄 正在为 `{directory}` 生成 AGENTS.md,请稍候…")

        umo = event.unified_msg_origin
        provider = plugin.context.get_using_provider(umo=umo)
        init_template = resolve_init_template(plugin._config)

        content = await generate_agents_md_via_llm(
            provider, target, umo=umo, init_template=init_template
        )

        try:
            await asyncio.to_thread(
                agents_md_path.write_text, content, encoding="utf-8"
            )
            yield event.plain_result(
                f"✅ 已在 `{directory}` 下创建 AGENTS.md({len(content)} 字符)。\n"
                f"路径: {agents_md_path}\n"
                f"您可以使用 `/agentsmd load {directory}` "
                "将其加载到当前会话的系统提示词中。"
            )
        except Exception as e:
            yield event.plain_result(f"❌ 写入 AGENTS.md 失败: {e}")

    # ── /agentsmd load <directory> ─────────────────

    async def load(self, event, directory: str):
        """/agentsmd load <directory> 实现。"""
        directory = strip_surrounding_quotes(directory)
        target = Path(directory).resolve()

        plugin = self._plugin()
        ok, reason = self._is_path_safe(
            target, user_blacklist=plugin._config.get("file_remove_blacklist")
        )
        if not ok:
            yield event.plain_result(f"❌ 路径不允许: {reason}")
            return

        if not target.exists():
            yield event.plain_result(
                f"❌ 目录 `{directory}` 不存在。\n请先创建该目录,或确认路径是否正确。"
            )
            return
        if not target.is_dir():
            yield event.plain_result(f"❌ `{directory}` 不是一个目录。")
            return

        # v2.9: 代码文件检测(与 init 对齐)
        if not has_code_files(target):
            supported = ", ".join(f".{ext}" for ext in sorted(CODE_FILE_EXTENSIONS))
            yield event.plain_result(
                f"❌ 目录 `{directory}` 下未找到代码文件。\n"
                f"AGENTS.md 仅用于代码项目,支持的后缀: {supported}\n"
                "请确认目录是否正确,或选择包含源代码的目录。"
            )
            return

        agents_md_path = target / "AGENTS.md"
        if not agents_md_path.exists():
            yield event.plain_result(
                f"❌ 目录 `{directory}` 下未找到 AGENTS.md 文件。\n"
                f"请先使用 `/agentsmd init {directory}` 创建。"
            )
            return

        try:
            content = await asyncio.to_thread(
                agents_md_path.read_text, encoding="utf-8"
            )
            mtime = agents_md_path.stat().st_mtime
        except Exception as e:
            yield event.plain_result(f"❌ 读取 AGENTS.md 失败: {e}")
            return

        umo = event.unified_msg_origin
        self._state.set(
            umo,
            AgentsState(
                path=str(agents_md_path),
                directory=str(target),
                last_content=content,
                mtime=mtime,
            ),
        )

        yield event.plain_result(
            f"✅ 已加载 `{directory}/AGENTS.md` 到当前会话的系统提示词。\n"
            f"内容长度: {len(content)} 字符\n"
            "后续每次 LLM 请求都会自动注入该内容。\n"
            "使用 `/agentsmd unload` 可卸载,使用 `/agentsmd update` 可手动更新。"
        )

    # ── /agentsmd unload ───────────────────────────

    def unload(self, event):
        """/agentsmd unload 实现。"""
        umo = event.unified_msg_origin
        state = self._state.pop(umo)
        if state is None:
            return event.plain_result("ℹ️ 当前会话未加载任何 AGENTS.md。")
        return event.plain_result(f"✅ 已卸载 AGENTS.md 注入。\n原文件: `{state.path}`")

    # ── /agentsmd update ───────────────────────────

    async def update(self, event):
        """/agentsmd update 实现:用 LLM 重新生成已加载的 AGENTS.md。"""
        umo = event.unified_msg_origin
        state = self._state.get(umo)
        if state is None:
            yield event.plain_result(
                "当前会话未加载 AGENTS.md。请先使用 `/agentsmd load <路径>` "
                "加载一个 AGENTS.md。"
            )
            return

        agents_md_path = Path(state.path)
        dir_path = Path(state.directory)
        existing_content = state.last_content

        if not agents_md_path.exists():
            yield event.plain_result(
                f"AGENTS.md 文件不存在: {agents_md_path}\n"
                "请检查文件是否被删除,或重新使用 `/agentsmd init` 初始化。"
            )
            return

        yield event.plain_result(
            "正在使用 LLM 重新生成 AGENTS.md (基于现有内容 + 最新目录结构) . .."
        )

        plugin = self._plugin()
        provider = plugin.context.get_using_provider(umo=umo)
        if provider is None:
            yield event.plain_result("当前会话未配置 LLM Provider,无法更新。")
            return

        init_template = resolve_init_template(plugin._config)
        prompt = (
            f"{init_template}\n\n"
            "以下是该项目的文件结构和关键文件内容摘要:\n\n"
            f"{scan_project_context(dir_path)}\n\n"
            "## 现有 AGENTS.md 内容\n\n"
            f"{existing_content}\n\n"
            "请比较现有实现和目录结构与 AGENTS.md 是否有差异,并更新 AGENTS.md\n\n"
            "请直接输出 AGENTS.md 的完整内容(Markdown 格式),不要添加任何额外说明,"
            "也不要使用 ```markdown 等代码块包裹整个内容。"
        )

        try:
            llm_resp = await provider.text_chat(
                prompt=prompt,
                session_id=umo,
                contexts=[],
                system_prompt=(
                    "你是一名资深软件工程师,擅长为多种语言的项目编写规范文档。"
                ),
            )
        except Exception as e:
            yield event.plain_result(f"LLM 调用失败: {e}")
            return

        new_content = (getattr(llm_resp, "completion_text", "") or "").strip()
        new_content = strip_code_fence(new_content)
        new_content = new_content if new_content else existing_content

        try:
            await asyncio.to_thread(
                agents_md_path.write_text, new_content, encoding="utf-8"
            )
        except Exception as e:
            yield event.plain_result(f"❌ 写入 AGENTS.md 失败: {e}")
            return

        state.update_content(new_content, agents_md_path.stat().st_mtime)

        yield event.plain_result(
            f"AGENTS.md 已更新 ({len(new_content)} 字符)。\n"
            "后续 LLM 请求将自动使用新版本。"
        )

    # ── on_llm_request 钩子 ─────────────────────────

    async def on_llm_request(self, event, req) -> None:
        """每次 LLM 请求前,若当前会话已加载 AGENTS.md,注入到 system_prompt 末尾。

        行为:
        - 若当前 umo 未加载 AGENTS.md → return
        - 检测文件 mtime 变化,如有变化则刷新 last_content
        - 通过 INJECTION_MARKER 防重复注入
        """
        plugin = self._plugin()
        if not plugin._config.get("agentsmd_enabled", True):
            return
        umo = event.unified_msg_origin
        state = self._state.get(umo)
        if state is None:
            return

        agents_md_path = Path(state.path)
        content = state.last_content

        # mtime 检测(自动刷新缓存)
        try:
            if agents_md_path.exists():
                current_mtime = agents_md_path.stat().st_mtime
                if current_mtime != state.mtime:
                    content = await asyncio.to_thread(
                        agents_md_path.read_text, encoding="utf-8"
                    )
                    state.update_content(content, current_mtime)
                    logger.debug(
                        f"[agentsmd] 检测到 AGENTS.md 已变更,已刷新缓存: "
                        f"{agents_md_path}"
                    )
            else:
                logger.warning(
                    f"[agentsmd] AGENTS.md 文件不存在,使用缓存内容: {agents_md_path}"
                )
        except Exception as e:
            logger.error(f"[agentsmd] 读取 AGENTS.md 失败,使用缓存: {e}")

        if not content:
            return

        # 防重复注入
        if INJECTION_MARKER in (req.system_prompt or ""):
            return

        # v2.8: 注入项目目录路径(在 AGENTS.md 内容之前)
        directory = state.directory
        if req.system_prompt is None or req.system_prompt == "":
            req.system_prompt = build_injection(content, directory=directory).lstrip(
                "\n"
            )
        else:
            req.system_prompt = req.system_prompt + build_injection(
                content, directory=directory
            )

        logger.debug(
            f"[agentsmd] 已向会话 {umo} 的 system_prompt 注入 AGENTS.md "
            f"({len(content)} 字符)"
        )
