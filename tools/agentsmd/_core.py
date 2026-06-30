"""agentsmd — AGENTS.md 项目指令文件管理(v2.4 合并自独立插件)。

提供:
- DEFAULT_INIT_TEMPLATE: 默认的 AGENTS.md 生成 prompt 模板
- DEFAULT_AGENTS_MD: 兜底内容(LLM 不可用时)
- generate_agents_md_via_llm(provider, dir_path, *, umo, init_template) -> str
  通过 AstrBot Provider 调 LLM 生成 AGENTS.md 内容
- scan_project_context(dir_path, *, max_depth=2) -> str
  扫描项目目录,收集目录结构和关键文件内容作为 LLM 上下文
- strip_code_fence(text) -> str
  剥离 LLM 返回内容外层的 ```lang ... ``` 包裹
- INJECTION_MARKER: 用于 on_llm_request 钩子检测是否已注入(防重复)
- DEFAULT_INJECTION_HEADER: 注入到 system_prompt 时的标题行
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# v2.9: 代码文件检测逻辑已抽到 tools/_code_detect(共享给 agentsmd 与 codegraph)
# 本模块仅 re-export,保持向后兼容(老 import 路径仍可用)。
# PR-5 (2026-06-23): _core.py 从 tools/agentsmd.py 移到 tools/agentsmd/_core.py
# 后,相对路径多一层: 从 ._code_detect 变为 .._code_detect。
from .._code_detect import (  # noqa: F401  (re-exports)
    CODE_FILE_EXTENSIONS,
    _SKIP_DIRS,
    has_code_files,
)

# 默认的 AGENTS.md 生成 prompt 模板
DEFAULT_INIT_TEMPLATE = """请分析此代码库并创建一个 AGENTS.md 文件,包含:
- 构建/lint/测试命令——特别是运行单个测试的命令。如果使用msbuild进行构建,只输出错误信息和警告信息
- 代码风格指南,包括导入、格式化、类型、命名约定、错误处理等
- 该文件将被提供给在此仓库中工作的编程代理
– 文件使用与用户相同的语言，默认中文
"""

# AGENTS.md 默认内容(当目录为空或无法调用 LLM 时的兜底内容)
DEFAULT_AGENTS_MD = """# AGENTS.md

## 项目上下文

本项目使用 AGENTS.md 来定义 AI 代理在项目中工作时应遵循的规范和上下文。

## 构建/测试命令

- 请根据项目实际情况补充构建和测试命令

## 代码风格指南

- 遵循项目已有的代码规范
- 保持代码简洁、可读
- 添加必要的注释

## 架构说明

- 请根据项目实际情况补充架构说明

## 操作约定

- 在进行重大修改前,请先与项目维护者确认
- 遵循安全最佳实践
"""

# 注入到 system_prompt 时的 marker(防重复注入)
INJECTION_MARKER = "# AGENTS.md 项目规范"
DEFAULT_INJECTION_HEADER = f"\n\n{INJECTION_MARKER}\n\n"


def strip_code_fence(text: str) -> str:
    """剥离 LLM 返回内容外层的 ```lang ... ``` 代码块包裹(如果有)。"""
    text = text.strip()
    m = re.match(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n```$", text, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def strip_surrounding_quotes(s: str) -> str:
    """去除字符串首尾的引号(支持双引号和单引号)。

    AstrBot 框架在解析带空格的目录参数时,可能自动用引号包裹。
    """
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


# ── 关键文件清单(项目配置/构建/质量相关) ────────────

KEY_PROJECT_FILES: tuple[str, ...] = (
    "package.json",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "CMakeLists.txt",
    "Makefile",
    "README.md",
    "README.rst",
    "tsconfig.json",
    "vite.config.ts",
    "vite.config.js",
    "webpack.config.js",
    ".eslintrc",
    ".prettierrc",
    "docker-compose.yml",
    "Dockerfile",
)

# v2.9: _SKIP_DIRS 已抽到 tools/_code_detect(共享给 agentsmd.scan_project_context
# 与 _code_detect.has_code_files),顶部 import 引入,此处不再定义。


def _max_subdirs_at_depth(dirs: list[str], cap: int = 10) -> None:
    """把 dirs 截断到 cap 个,并在调用方负责打印截断提示。返回被截掉的数量。"""
    if len(dirs) > cap:
        extra = len(dirs) - cap
        del dirs[cap:]
        return extra  # type: ignore[return-value]
    return 0  # type: ignore[return-value]  # noqa: E501


def scan_project_context(dir_path: Path, *, max_depth: int = 2) -> str:
    """扫描项目目录,收集关键文件信息作为 LLM 上下文。

    返回包含:
    - 项目根目录
    - 目录结构(最多 max_depth 层,每个父目录最多 10 个子目录)
    - 常见配置文件的内容(package.json, pyproject.toml, README.md 等)

    这是纯函数,无副作用;供 generate_agents_md_via_llm 调用。
    """
    lines: list[str] = []
    lines.append(f"项目根目录: {dir_path}")
    lines.append("")
    lines.append("## 目录结构")
    try:
        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
            depth = root[len(str(dir_path)) :].count(os.sep)
            if depth > max_depth:
                del dirs[:]
                continue

            indent = "  " * depth
            rel_root = Path(root).relative_to(dir_path)
            rel_root_str = "." if str(rel_root) == "." else str(rel_root)
            lines.append(f"{indent}{rel_root_str}/")

            sub_indent = "  " * (depth + 1)
            for f in sorted(files):
                if not f.startswith("."):
                    lines.append(f"{sub_indent}{f}")

            # 限制显示的子目录数量,避免 LLM 上下文爆炸
            if len(dirs) > 10:
                extra = len(dirs) - 10
                dirs[:] = dirs[:10]
                lines.append(f"{sub_indent}... (还有 {extra} 个子目录被忽略)")
    except Exception as e:
        lines.append(f"(扫描目录结构失败: {e})")

    lines.append("")
    lines.append("## 关键文件内容")

    found_any = False
    for filename in KEY_PROJECT_FILES:
        file_path = dir_path / filename
        if file_path.exists() and file_path.is_file():
            found_any = True
            lines.append(f"\n### {filename}")
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                max_lines = 50
                text_lines = text.splitlines()
                if len(text_lines) > max_lines:
                    text = "\n".join(text_lines[:max_lines])
                    text += f"\n... (共 {len(text_lines)} 行,已截断)"
                lines.append(f"```\n{text}\n```")
            except Exception as e:
                lines.append(f"(读取失败: {e})")

    if not found_any:
        lines.append("(未找到常见的项目配置文件)")

    return "\n".join(lines)


async def generate_agents_md_via_llm(
    provider,
    dir_path: Path,
    *,
    umo: str,
    init_template: str = "",
) -> str:
    """通过 AstrBot LLM Provider 自动生成 AGENTS.md 内容。

    Args:
        provider: 通过 self.context.get_using_provider(umo=...) 获取
        dir_path: 目标项目目录
        umo: unified_msg_origin(用于 session_id 隔离)
        init_template: 用户自定义模板;为空则用默认

    Returns:
        生成的 AGENTS.md 文本;若 LLM 调用失败,返回 DEFAULT_AGENTS_MD
    """
    if provider is None:
        return DEFAULT_AGENTS_MD

    template = (
        init_template.strip()
        if init_template and init_template.strip()
        else DEFAULT_INIT_TEMPLATE
    )
    project_context = scan_project_context(dir_path)
    prompt = (
        f"{template}\n\n"
        f"以下是该项目的文件结构和关键文件内容摘要:\n\n"
        f"{project_context}\n\n"
        f"请直接输出 AGENTS.md 的完整内容(Markdown 格式),不要添加任何额外说明,"
        f"也不要使用 ```markdown 等代码块包裹整个内容。"
    )

    try:
        llm_resp = await provider.text_chat(
            prompt=prompt,
            session_id=umo,
            contexts=[],
            system_prompt="你是一名资深软件工程师,擅长为多种语言的项目编写规范文档。",
        )
    except Exception:
        return DEFAULT_AGENTS_MD

    content = (getattr(llm_resp, "completion_text", "") or "").strip()
    content = strip_code_fence(content)
    return content if content else DEFAULT_AGENTS_MD


# v2.8 新增: 注入路径前缀模板,放在 AGENTS.md 注入位置之前
# 让 LLM 知道当前会话绑定到哪个项目目录(配合 /agentsmd load 使用)
# 不带尾随换行:DEFAULT_INJECTION_HEADER 自带 \n\n 前缀,正好形成空行分隔
PROJECT_PATH_PREFIX_TEMPLATE = "你正在处理的项目工作路径为: {directory}。\n在对项目进行修改、写入等操作时，优先使用git worktree（如果可用）"


def build_injection(content: str, *, directory: str = "") -> str:
    """构造注入到 system_prompt 末尾的文本。

    当提供 directory 时,会在 AGENTS.md 内容前注入一行
    "项目路径: <directory>",便于 LLM 知道当前会话绑定到哪个项目目录。

    on_llm_request 钩子调用,marker 检测防重复注入(由调用方配合)。

    输出格式(有 directory):
        项目路径: <directory>

        <INJECTION_MARKER>
        <content>

    输出格式(无 directory,向后兼容):
        <INJECTION_MARKER>
        <content>
    """
    path_prefix = (
        PROJECT_PATH_PREFIX_TEMPLATE.format(directory=directory) if directory else ""
    )
    return f"{path_prefix}{DEFAULT_INJECTION_HEADER}{content}"


def resolve_init_template(config: dict | None, default: str = "") -> str:
    """从插件 config 中取出 init_template,空时回退到默认。"""
    custom = (config or {}).get("init_template", "")
    if custom and custom.strip():
        return custom.strip()
    return default or DEFAULT_INIT_TEMPLATE


# v2.9: CODE_FILE_EXTENSIONS 与 has_code_files 已抽到 tools/_code_detect。
# 本模块顶部 import 引入并 re-export,保持向后兼容(老代码 `from tools.agentsmd
# import has_code_files, CODE_FILE_EXTENSIONS` 仍然可用)。
