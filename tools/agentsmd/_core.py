"""agentsmd — AGENTS.md 项目指令文件管理(v2.4 合并自独立插件)。

提供:
- DEFAULT_INIT_TEMPLATE: 代码项目目录的 AGENTS.md 生成 prompt 模板
- DEFAULT_INIT_TEMPLATE_GENERAL: 非代码目录(文档/资料库)的生成 prompt 模板
- pick_init_template(config, dir_path) -> str
  自适应模板选择:用户自定义 init_template 优先,否则按目录是否含代码文件选默认
- DEFAULT_AGENTS_MD: 兜底内容(LLM 不可用时)
- generate_agents_md_via_llm(provider, dir_path, *, umo, init_template) -> str
  通过 AstrBot Provider 调 LLM 生成 AGENTS.md 内容
- scan_project_context(dir_path, *, max_depth=2) -> str
  扫描项目目录,收集目录结构、文件类型统计和关键文件/文档摘录作为 LLM 上下文
- strip_code_fence(text) -> str
  剥离 LLM 返回内容外层的 ```lang ... ``` 包裹
- INJECTION_MARKER: 用于 on_llm_request 钩子检测是否已注入(防重复)
- DEFAULT_INJECTION_HEADER: 注入到 system_prompt 时的标题行
"""

from __future__ import annotations

import asyncio
import logging
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
from .._helpers import _NO_WINDOW_KWARGS, safe_decode_bytes

logger = logging.getLogger(__name__)


# 默认的 AGENTS.md 生成 prompt 模板(代码项目目录)。
# 2026-10-17: 按 Codex `/init` 风格重写 —— 章节清单 + 反编造 + 篇幅约束。
# 有意不抄 Codex 的"检查文件是否已存在":handler 已硬性拒绝覆盖,写进 prompt 只会浪费一次判断。
DEFAULT_INIT_TEMPLATE = """请分析当前代码库，并生成一份 AGENTS.md，作为在此仓库中工作的贡献者指南。

写作要求：
- 首行使用一级标题「# 项目指南」
- 使用 Markdown 标题分层，全文控制在 1200–2500 字
- 每条说明都要具体、可执行，避免"保持代码整洁"这类空泛表述
- 只写你在仓库中实际看到的事实；不要编造不存在的命令、脚本或目录
- 文件使用与用户相同的语言，默认中文
- 直接输出文件内容，不要额外说明，也不要用 markdown 代码块包裹全文

建议章节（按实际情况增删，不适用的直接省略）：
## 项目结构与模块组织
## 构建、测试与本地开发
- 构建、运行、跑测试的命令；特别是"只跑单个测试"的命令
## 代码风格与命名约定
- 缩进、导入顺序、格式化与 lint 工具、命名习惯、错误处理约定
## 测试规范
- 测试框架、用例命名、新增代码的测试要求
## 提交与 PR 规范
- 依据下方提供的提交历史，总结真实的提交信息约定（例如是否有 type(scope) 前缀）
- 若历史中看不出约定，写"无明确约定"，不要虚构
## 其他（可选）
- 安全与配置注意事项、架构要点、容易踩的坑
"""

# 非代码目录(文档库/资料库/笔记库等)的生成 prompt 模板。
# WHY: init 不再要求目录含代码文件,纯文档目录走代码模板会让 LLM
# 编造不存在的构建命令;改用面向内容组织的指令集。
# 2026-10-17: 同上按 Codex 风格重写,篇幅上限低于代码模板。
DEFAULT_INIT_TEMPLATE_GENERAL = """请分析当前目录，并生成一份 AGENTS.md，作为在此目录中工作的代理指南。

写作要求：
- 首行使用一级标题「# 目录指南」
- 使用 Markdown 标题分层，全文控制在 800–2000 字
- 只描述目录中真实存在的内容；不要编造不存在的构建/测试命令
- 文件使用与用户相同的语言，默认中文
- 直接输出文件内容，不要额外说明，也不要用 markdown 代码块包裹全文

建议章节（按实际情况增删）：
## 目录用途
## 内容组织
- 各子目录与关键文件分别承担什么职责、命名有何规律
## 维护约定
- 写作语言、格式规范、新增内容应放在哪里
## 工具与脚本（若有）
## 其他（可选）
"""

# AGENTS.md 默认内容(当目录为空或无法调用 LLM 时的兜底内容)。
# 措辞保持目录类型中立(代码/文档目录都适用),条目按"若适用"理解。
# 2026-10-17: 首行与生成模板的一级标题统一为「# 项目指南」。
DEFAULT_AGENTS_MD = """# 项目指南

## 项目上下文

本项目使用 AGENTS.md 来定义 AI 代理在这个目录中工作时应遵循的规范和上下文。

## 构建/测试命令

- 若本项目包含可构建/可测试的代码,请根据实际情况补充命令
- 纯文档/资料目录可跳过本节

## 代码风格指南

- 遵循项目已有的代码规范
- 保持代码简洁、可读
- 添加必要的注释

## 架构说明

- 请根据实际情况补充目录的组织结构与各部分职责

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
    - 文件类型统计(按扩展名计数,帮助 LLM 快速判断目录性质)
    - 非代码目录:额外摘录文档文件(README 之外的 .md/.rst/.txt/.adoc)内容,
      使纯文档目录也能生成有依据的 AGENTS.md

    这是纯函数,无副作用;供 generate_agents_md_via_llm 调用。
    """
    lines: list[str] = []
    lines.append(f"项目根目录: {dir_path}")
    lines.append("")
    lines.append("## 目录结构")

    ext_counts: dict[str, int] = {}
    listed_any = False
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
                    listed_any = True
                    # 文件类型统计(与目录树同深度口径)
                    if "." in f:
                        ext = f.rsplit(".", 1)[-1].lower()
                    else:
                        ext = "(无后缀)"
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1

            # 限制显示的子目录数量,避免 LLM 上下文爆炸
            if len(dirs) > 10:
                extra = len(dirs) - 10
                dirs[:] = dirs[:10]
                lines.append(f"{sub_indent}... (还有 {extra} 个子目录被忽略)")
    except Exception as e:
        lines.append(f"(扫描目录结构失败: {e})")

    if not listed_any:
        lines.append("(目录为空或未发现可见文件)")
    else:
        lines.append("")
        lines.append("## 文件类型统计")
        for ext, count in sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]:
            label = ext if ext == "(无后缀)" else f".{ext}"
            lines.append(f"- {label}: {count} 个文件")
        if len(ext_counts) > 10:
            lines.append(f"- (其余 {len(ext_counts) - 10} 种扩展名从略)")

    lines.append("")
    lines.append("## 关键文件内容")

    consumed_names: set[str] = set()
    found_any = False
    for filename in KEY_PROJECT_FILES:
        file_path = dir_path / filename
        if file_path.exists() and file_path.is_file():
            found_any = True
            consumed_names.add(filename)
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

    # 非代码目录:代码项目上下文(配置文件)信息量不足,额外摘录文档内容,
    # 让 LLM 依据真实材料描述目录用途与内容组织,而非凭空编造。
    # 深度口径与目录树一致(max_depth),避免出现树里看不到的路径。
    if not has_code_files(dir_path):
        lines.extend(_doc_excerpts_section(dir_path, consumed_names, max_depth))

    return "\n".join(lines)


# 文档摘录节常量:覆盖的文档后缀 / 最多摘录篇数 / 每篇行数上限
_DOC_EXCERPT_EXTENSIONS: frozenset[str] = frozenset({".md", ".rst", ".txt", ".adoc"})
_DOC_EXCERPT_MAX_FILES = 4
_DOC_EXCERPT_MAX_LINES = 40


def _doc_excerpts_section(
    dir_path: Path, consumed_names: set[str], max_depth: int
) -> list[str]:
    """构造"## 文档文件摘录"小节(仅供非代码目录)。

    从目录树(跳过隐藏目录与 _SKIP_DIRS,深度上限与目录树同口径)
    收集 .md/.rst/.txt/.adoc 文件,排除 KEY_PROJECT_FILES 已读取过的
    文件名(如 README.md),按路径名排序后取前 _DOC_EXCERPT_MAX_FILES 篇,
    每篇截取前 _DOC_EXCERPT_MAX_LINES 行。
    """
    lines: list[str] = ["", "## 文档文件摘录"]
    candidates: list[Path] = []
    try:
        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
            depth = root[len(str(dir_path)) :].count(os.sep)
            if depth > max_depth:
                del dirs[:]
                continue
            for f in sorted(files):
                if f.startswith(".") or f in consumed_names:
                    continue
                p = Path(root) / f
                if p.suffix.lower() in _DOC_EXCERPT_EXTENSIONS:
                    candidates.append(p)
    except OSError:
        lines.append("(扫描文档文件失败)")
        return lines

    if not candidates:
        lines.append(f"(未发现 {'/'.join(sorted(_DOC_EXCERPT_EXTENSIONS))} 文档)")
        return lines

    for p in sorted(candidates)[:_DOC_EXCERPT_MAX_FILES]:
        rel = p.relative_to(dir_path)
        lines.append(f"\n### {rel}")
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            text_lines = text.splitlines()
            if len(text_lines) > _DOC_EXCERPT_MAX_LINES:
                text = "\n".join(text_lines[:_DOC_EXCERPT_MAX_LINES])
                text += f"\n... (共 {len(text_lines)} 行,已截断)"
            lines.append(f"```\n{text}\n```")
        except OSError as e:
            lines.append(f"(读取失败: {e})")
    if len(candidates) > _DOC_EXCERPT_MAX_FILES:
        lines.append(f"\n(其余 {len(candidates) - _DOC_EXCERPT_MAX_FILES} 篇文档从略)")
    return lines


# ── git 历史采集(2026-10-17) ─────────────────────────
# 提交历史是"提交信息约定"的唯一事实依据;没有它,LLM 只能编造
# `feat(scope): ...` 之类看起来合理但仓库里并不存在的约定。
_GIT_HISTORY_MAX_SUBJECT = 200
_GIT_HISTORY_MAX_CHARS = 4096


async def collect_git_history(
    dir_path: Path,
    *,
    git_bin: str = "git",
    limit: int = 20,
    timeout: float = 3.0,
) -> str:
    """采集目录所属仓库的最近提交(短哈希 + 主题),失败返回空串。

    WHY 全静默: 生成 AGENTS.md 是主流程,git 只是"锦上添花"的事实来源。
    目录不是仓库、机器没装 git、仓库损坏、磁盘慢 —— 任一情况都不该让
    `/agentsmd init` 变成报错。

    ``--no-merges``: 合并提交(`Merge pull request #123 from ...`)对推断
    提交约定只有噪声。

    Note:
        采集范围是 ``git -C <dir> log`` 的语义,即该目录**所属仓库**。
        目录是子目录时拿到的是整仓历史 —— Git 的固有行为,不是缺陷。

    Args:
        dir_path: 目标目录(不必是仓库根)。
        git_bin: git 可执行文件路径(优先配置 ``git_path``)。
        limit: 最多取多少条提交。
        timeout: 子进程超时(秒),超时即 kill 并返回空串。

    Returns:
        逐行 ``"<短哈希> <主题>"``;无历史或失败时 ``""``。
    """
    cmd = [
        git_bin,
        "-C",
        str(dir_path),
        "log",
        "--no-merges",
        "--pretty=format:%h %s",
        "-n",
        str(limit),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
    except OSError as e:
        # FileNotFoundError(git 未安装/路径错) / WinError 2 / 权限问题
        logger.debug(f"[agentsmd] git 不可用,跳过历史采集: {e}")
        return ""

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        await proc.wait()
        logger.debug(f"[agentsmd] git log 超时({timeout}s),跳过历史采集")
        return ""

    if proc.returncode != 0:
        logger.debug(f"[agentsmd] git log 退出码 {proc.returncode},跳过历史采集")
        return ""

    text = safe_decode_bytes(stdout, preferred="utf-8")
    lines: list[str] = []
    total = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 截的是"主题"(保留短哈希,否则哈希会被吃掉,详见 _GIT_HISTORY_MAX_SUBJECT 语义)
        sha, sep, subject = line.partition(" ")
        if sep and len(subject) > _GIT_HISTORY_MAX_SUBJECT:
            line = f"{sha} {subject[:_GIT_HISTORY_MAX_SUBJECT]}"
        # 按行边界截断,绝不切断半行
        if total + len(line) + 1 > _GIT_HISTORY_MAX_CHARS:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def build_output_constraints(max_chars: int) -> str:
    """构造 prompt 尾部的输出约束(init / update 共用,消除两份尾巴漂移)。

    ``max_chars <= 0`` 时只保留"直接输出 + 不要代码块包裹"两条,
    不出现任何数字 —— 这是"不限制"的显式语义。
    """
    parts = [
        "请直接输出 AGENTS.md 的完整内容(Markdown 格式),不要添加任何额外说明,"
        "也不要使用 markdown 代码块包裹整个内容。",
    ]
    if max_chars > 0:
        parts.append(f"全文硬性上限 {max_chars} 字,超出部分会被截断。")
    return "\n".join(parts)


async def build_project_context(
    dir_path: Path,
    *,
    git_bin: str = "git",
    history_limit: int = 20,
) -> str:
    """构造完整的生成上下文:目录扫描 + (可选)最近提交历史。

    WHY 单独一层包装: ``scan_project_context`` 是纯函数,既有 6 个 scan
    用例与第三方调用都依赖它的输出不变;历史采集是异步 / 有副作用(起子进程),
    只在包装层追加,不污染扫描函数。
    """
    context = scan_project_context(dir_path)
    history = await collect_git_history(dir_path, git_bin=git_bin, limit=history_limit)
    if not history:
        return context
    header = f"## 最近提交历史（最多 {history_limit} 条，用于推断提交信息约定）"
    return f"{context}\n\n{header}\n\n{history}"


async def generate_agents_md_via_llm(
    provider,
    dir_path: Path,
    *,
    umo: str,
    init_template: str = "",
    max_chars: int = 0,
    git_bin: str = "git",
) -> str:
    """通过 AstrBot LLM Provider 自动生成 AGENTS.md 内容。

    Args:
        provider: 通过 self.context.get_using_provider(umo=...) 获取
        dir_path: 目标项目目录
        umo: unified_msg_origin(用于 session_id 隔离)
        init_template: 用户自定义模板;为空则用默认
        max_chars: 全文硬性上限(字符数);``<= 0`` 表示不追加数字约束。
            注意默认值是 0(不限制),由 handler 传入配置解析结果 ——
            这样旧调用方(含测试)不传该参数时行为完全不变。
        git_bin: git 可执行文件路径(配置 ``git_path`` > ``"git"``)

    Returns:
        生成的 AGENTS.md 文本;若 LLM 调用失败,返回 DEFAULT_AGENTS_MD
    """
    if provider is None:
        # WHY 排在 git 采集之前: provider 缺失时无需付子进程代价,
        # 也让既有用例可以安全传入不存在的目录。
        return DEFAULT_AGENTS_MD

    template = (
        init_template.strip()
        if init_template and init_template.strip()
        else DEFAULT_INIT_TEMPLATE
    )
    project_context = await build_project_context(dir_path, git_bin=git_bin)
    prompt = (
        f"{template}\n\n"
        f"以下是该目录的文件结构、文件类型统计和关键文件内容摘要:\n\n"
        f"{project_context}\n\n"
        f"{build_output_constraints(max_chars)}"
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


# v2.22 (2026-07-27): PROJECT_PATH_PREFIX_TEMPLATE 已移除。
# 路径注入与 agentsmd 解耦,改由 project 子系统负责
# (tools/_guidance_text.py: PROJECT_PATH_GUIDANCE_TEMPLATE +
# tools/project/inject.py: inject_project_path)。
# 动机: /project load <dir> no_agentsmd 时也要能注入项目路径。


def build_injection(content: str) -> str:
    """构造注入到 system_prompt 末尾的文本(marker 头 + AGENTS.md 内容)。

    on_llm_request 钩子调用,marker 检测防重复注入(由调用方配合)。

    v2.22 (2026-07-27): 删除 ``directory`` 参数 — 项目路径注入
    已解耦到 project 子系统(tools/project/inject.py),本函数
    只负责 AGENTS.md 内容本身。

    输出格式:
        <INJECTION_MARKER>
        <content>
    """
    return f"{DEFAULT_INJECTION_HEADER}{content}"


def resolve_init_template(config: dict | None, default: str = "") -> str:
    """从插件 config 中取出 init_template,空时回退到默认。

    .. deprecated:: 非代码目录支持(2026-09-09)起,handler 侧应改用
        :func:`pick_init_template`(能按目录类型选模板)。本函数保留
        供向后兼容与需要"无条件用代码模板"的调用方使用。
    """
    custom = (config or {}).get("init_template", "")
    if custom and custom.strip():
        return custom.strip()
    return default or DEFAULT_INIT_TEMPLATE


def pick_init_template(config: dict | None, dir_path: Path) -> str:
    """自适应 init 模板选择:用户自定义优先,否则按目录类型选默认。

    选择顺序:
    1. config 中的 ``init_template`` 非空 → 原样使用(用户明确覆盖,
       对代码/非代码目录一视同仁,不做二次加工)
    2. 目录含代码文件(递归判定,跳过垃圾/隐藏目录)→
       :data:`DEFAULT_INIT_TEMPLATE`(代码项目模板)
    3. 否则 → :data:`DEFAULT_INIT_TEMPLATE_GENERAL`(通用目录模板,
       面向文档/资料库的内容组织说明)

    Args:
        config: 插件配置 dict(可为 None)。
        dir_path: 目标目录(须已存在;不存在时按非代码目录处理)。

    Returns:
        strip 后的模板文本。
    """
    custom = (config or {}).get("init_template", "")
    if custom and custom.strip():
        return custom.strip()
    if has_code_files(dir_path):
        return DEFAULT_INIT_TEMPLATE
    return DEFAULT_INIT_TEMPLATE_GENERAL


# v2.9: CODE_FILE_EXTENSIONS 与 has_code_files 已抽到 tools/_code_detect。
# 本模块顶部 import 引入并 re-export,保持向后兼容(老代码 `from tools.agentsmd
# import has_code_files, CODE_FILE_EXTENSIONS` 仍然可用)。
