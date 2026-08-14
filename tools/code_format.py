"""code_format — auto-format a single source file (Python / C·C++ / Java / JS·TS / C#).

与 code_check 的关系:code_check 是**只读**检查,code_format 是**写**工具
(可能修改文件)。LLM 在 plan 模式下不应调用本工具。

设计要点(2026-06-25, v2.14 引入;2026-08-14 astyle → clang-format 迁移):

1. **formatter = "auto" 路由**
   - .py → ruff format
   - .c/.cpp/.cc/.cxx/.h/.hpp/.hxx/.hh/.java/.js/.jsx/.mjs/.cjs/.cs → clang-format

2. **clang-format 调用(2026-08-14 替换 astyle)**
   Spec: docs/superpowers/specs/2026-08-14-clang-format-unify-design.md
   - format 与 code_check 同源:同一参数链,保证"格式化后必通过格式检查"。
   - 永远 **stdin/stdout 二进制模式** 调用:
     - 读原文件字节 → stdin 喂给 clang-format(字节级保真,GBK/BOM 不被强转)
     - ``--assume-filename=<绝对路径>`` → 语言检测 + ``.clang-format`` 向上发现
     - 项目内 ``.clang-format`` 优先(自实现向上发现,找到 → ``--style=file``);
       找不到时用插件配置 default_style/default_indent 拼内联
       ``--style={BasedOnStyle: X, IndentWidth: N}`` 兜底
       (clang-format 17 的 --fallback-style 只接受预设名,拒绝内联 YAML,
       故不用它)
     - 拿 stdout 字节与原文件比较
     - check=False 且 changed 才写回(写回 clang-format 原始输出字节)
     - check=True 时,**永远不写回**
   - clang-format 原生处理 CRLF(DeriveLineEnding 保留主导行尾),无需
     astyle 时代的 CRLF→LF 归一化 workaround。

3. **ruff 调用**
   - check=False: `ruff format <file>`(直接写回)
   - check=True:  `ruff format --check --diff <file>(不写,只报告)
   ruff 返回码 0 = 无需改,非 0 = 有改动;同时读 stdout 的 diff 摘要。

4. **idempotent 语义**
   第二次格式化同一文件 → changed=False(我们用 stdlib difflib 比对,
   不依赖工具本身的"未变"信号)。

5. **错误分类**
   - 工具未安装 → ok=False + error="X 未安装" + proposal(参考 code_check)
   - 不支持扩展名 → ok=False + supported_extensions
   - 文件不存在 → ok=False + 错误信息
   - 解析失败 → ok=False + 原始 stderr 摘要
   - 超时 → ok=False + error="X 超时"

Author: elecvoid243, 2026-06-25; clang-format 迁移 2026-08-14
"""

from __future__ import annotations

import difflib
import json
import logging
import shutil
import subprocess
from pathlib import Path

from ._helpers import (
    _NO_WINDOW_KWARGS,
    _decode_text_bytes,
    _get_console_python,
    proposal_reply,
)

logger = logging.getLogger(__name__)

# ── 扩展名 → formatter 路由表 ─────────────────────────
# 顺序敏感:.py 必须先于 CLANG_FORMAT_SUFFIXES 集合。
PY_SUFFIXES = {".py"}

# clang-format 官方支持的语言: C, C++, Objective-C, C#, Java, JavaScript,
# TypeScript, ProtoBuf 等。本集合与 astyle 时代保持一致。
CLANG_FORMAT_SUFFIXES: set[str] = {
    # C / C++
    ".c",
    ".cpp",
    ".cc",
    ".cxx",
    ".h",
    ".hpp",
    ".hxx",
    ".hh",
    # Java
    ".java",
    # JavaScript
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    # C#
    ".cs",
}

# clang-format --fallback-style BasedOnStyle 的合法预设值
VALID_CLANG_FORMAT_STYLES: frozenset[str] = frozenset(
    {
        "llvm",
        "google",
        "chromium",
        "microsoft",
        "webkit",
        "gnu",
    }
)

# legacy astyle --style= 值 → clang-format 预设/内联 style 串映射。
# 向后兼容:_conf_schema.json 的 default_style 历史上是 astyle 风格名,
# 既有用户配置里可能仍存着这些值(AGENTS.md 规则 9:不改字段名,迁移配置)。
# 映射为 "{...}" 完整 style 串时,default_indent 不再叠加(串内已自带缩进语义)。
LEGACY_ASTYLE_STYLE_MAP: dict[str, str] = {
    "allman": "{BasedOnStyle: llvm, BreakBeforeBraces: Allman}",
    "kr": "llvm",
    "stroustrup": "llvm",
    "linux": "{BasedOnStyle: llvm, IndentWidth: 8, UseTab: Always}",
    "java": "llvm",
    "whitesmith": "llvm",
    "horstmann": "llvm",
    "ratliff": "llvm",
    "vtk": "llvm",
    "none": "llvm",
}

# 格式化文件大小上限(10 MB)。超过则拒绝,防止 LLM 误把巨型文件喂进来。
_MAX_FILE_SIZE = 10 * 1024 * 1024

# subprocess 超时(秒)。30s 对 10MB 文件已足够,astyle / ruff 都很快。
_FORMAT_TIMEOUT = 30

# diff 摘要最多展示多少行(给 LLM 看,避免上下文爆炸)
_DIFF_PREVIEW_LINES = 20


# ── 公开 API ────────────────────────────────────────


def format(
    filepath: str,
    formatter: str = "auto",
    *,
    check: bool = False,
    style: str = "llvm",
    indent: int = 4,
) -> dict:
    """对单个源文件运行代码格式化。

    Args:
        filepath: 源文件绝对路径。
        formatter: auto / ruff / clang-format。
        check: True=dry-run,只检测不写入。
        style: clang-format 预设(llvm/google/chromium/microsoft/webkit/gnu);
            兼容 legacy astyle 风格名(自动映射,见 LEGACY_ASTYLE_STYLE_MAP)。
            仅 clang-format 生效,且仅在项目内无 .clang-format 文件时作为
            fallback-style 生效。
        indent: 缩进空格数(clang-format → fallback-style 的 IndentWidth;
            ruff 不支持,作为 metadata)。

    Returns:
        ok=True  → {
            "ok": True, "formatter": "...",
            "formatter_options": {"style": "..", "indent": N},
            "check": bool,
            "changed": bool,
            "file_size_before": int, "file_size_after": int,
            "diff_summary": "..." (changed=True 时),
            "proposal": "..."
        }
        ok=False → {
            "ok": False, "error": "...", "proposal": "..."(可能),
            "options": [...], "supported_extensions": [...](可能)
        }
    """
    p = Path(filepath)

    # ── 1. 文件级前置校验 ──
    if not p.exists():
        return {"ok": False, "error": f"文件不存在: {filepath}"}
    if not p.is_file():
        return {"ok": False, "error": f"不是普通文件: {filepath}"}
    try:
        file_size = p.stat().st_size
    except OSError as e:
        return {"ok": False, "error": f"无法 stat 文件: {e}"}
    if file_size > _MAX_FILE_SIZE:
        return {
            "ok": False,
            "error": (
                f"文件过大 ({file_size} > {_MAX_FILE_SIZE} 字节),"
                f"为防止误操作,code_format 拒绝处理 >10MB 的文件"
            ),
        }

    # ── 2. formatter 路由 ──
    if formatter == "auto":
        formatter = _detect_formatter(p)
        if formatter is None:
            return {
                "ok": False,
                "error": (
                    f"不支持的扩展名: {p.suffix}。"
                    f"code_format 仅支持 Python 和 clang-format 支持的语言。"
                ),
                "supported_extensions": _supported_extensions(),
            }
    elif formatter not in ("ruff", "clang-format"):
        return {
            "ok": False,
            "error": f"不支持的 formatter: {formatter}",
            "supported": ["auto", "ruff", "clang-format"],
        }

    # ── 3. 风格参数校验(clang-format) ──
    formatter_options = {"style": style, "indent": indent}
    if (
        formatter == "clang-format"
        and style not in VALID_CLANG_FORMAT_STYLES
        and style not in LEGACY_ASTYLE_STYLE_MAP
    ):
        return {
            "ok": False,
            "error": f"不支持的 clang-format 风格: {style}",
            "supported_styles": sorted(VALID_CLANG_FORMAT_STYLES),
            "legacy_styles": sorted(LEGACY_ASTYLE_STYLE_MAP),
        }
    if not isinstance(indent, int) or indent < 1 or indent > 16:
        return {
            "ok": False,
            "error": f"indent 必须是 1-16 之间的整数,收到: {indent}",
        }

    # ── 4. 分派 ──
    if formatter == "ruff":
        result = _format_with_ruff(p, check=check, indent=indent)
    else:
        result = _format_with_clang_format(
            p,
            check=check,
            style=style,
            indent=indent,
        )

    # 统一附加 check / formatter_options 字段
    result["check"] = check
    result["formatter_options"] = formatter_options
    return result


# ── 路由辅助 ─────────────────────────────────────────


def _detect_formatter(p: Path) -> str | None:
    """根据文件扩展名选择 formatter。返回 None 表示不支持。"""
    suffix = p.suffix.lower()
    if suffix in PY_SUFFIXES:
        return "ruff"
    if suffix in CLANG_FORMAT_SUFFIXES:
        return "clang-format"
    return None


def _supported_extensions() -> list[str]:
    return sorted(PY_SUFFIXES | CLANG_FORMAT_SUFFIXES)


# ── ruff 路径 ────────────────────────────────────────
#
# 设计变更(v2.15,2026-07-01):不再查找 ruff 二进制路径,直接
# ``[sys.executable, "-m", "ruff", ...]``。这样:
#   1. 无需 ``shutil.which`` 走 PATHEXT 解析,彻底避免 .bat/.cmd wrapper 顶替
#      (原因 A:pythonw 模式下弹 cmd 黑窗的根因之一)
#   2. 无需 ``_find_ruff`` 路径检测函数
#   3. ruff 必须以 pip 包形式安装(``pip install ruff``),这是项目
#      requirements.txt 里已声明的依赖
#
# 仍保留 ``**_NO_WINDOW_KWARGS`` 双保险,防止 ruff 内部 helper 进程
# 触发 cmd 黑窗(原因 B/E 的兜底)。


def _format_with_ruff(p: Path, *, check: bool, indent: int) -> dict:
    """Python: 直接调 ``python -m ruff format``。

    设计要点:
      - **不**查找 ruff 二进制路径。ruff 必须是 pip 包
        (``pip install ruff``),通过 ``python -m ruff`` 走 Python 解释器,
        避免 ``shutil.which`` 的 PATHEXT 风险(.bat 顶替 → cmd.exe 黑窗)。
      - indent 仅作 metadata(ruff 用自身默认配置,不读我们的 indent 参数)。

    Args:
        p: 待格式化 .py 文件路径
        check: True = dry-run,不写回
        indent: 缩进空格数(本函数忽略,仅 ruff 内部风格用)

    Returns:
        标准 format() 返回 dict
    """
    before_bytes = p.read_bytes()
    file_size_before = len(before_bytes)

    if check:
        # --check 不写回;--diff 输出 diff 文本(写到 stdout)
        # WHY _get_console_python() 而非 sys.executable: pythonw.exe 是 GUI
        # subsystem,启动 ruff 后 Rust runtime 会主动 AllocConsole() 弹黑框,
        # CREATE_NO_WINDOW 无法阻止子进程主动行为;切到 CUI python.exe 即可。
        args = [
            _get_console_python(),
            "-m",
            "ruff",
            "format",
            "--check",
            "--diff",
            str(p),
        ]
    else:
        # 直接格式化(写回原文件)
        args = [_get_console_python(), "-m", "ruff", "format", str(p)]

    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_FORMAT_TIMEOUT,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ruff 超时"}
    except Exception as e:
        # FileNotFoundError(ruff 未装)/其他 → 一律"未安装"提示
        return proposal_reply(
            False,
            "ruff 未安装,无法格式化 Python 文件。请运行: pip install ruff",
            error="ruff 未安装",
            evidence={"python_file": str(p), "exception": str(e)},
            options=[
                "pip install ruff",
                "切换到 formatter=clang-format(不适用,仅 C/C++/Java/JS/TS/C#)",
            ],
        )

    # ── 解析结果 ──
    # ruff format 返回码语义:
    #   0  = 无需修改(或 --check 下完全合规)
    #   1  = 有差异(--check 下报告)
    #   2  = 错误
    # 注意:check 模式下文件未被改写,file_size 必然不变;
    # 此时唯一判断 changed 的依据是 ruff 的 returncode。
    file_size_after = p.stat().st_size
    if check:
        changed = r.returncode != 0
    else:
        changed = file_size_after != file_size_before

    if r.returncode == 2:
        # 错误:读取 stderr 第一行作为错误描述
        err = (r.stderr or "").strip().splitlines()
        err_msg = err[0] if err else "ruff 返回错误"
        return {
            "ok": False,
            "error": f"ruff 格式化失败: {err_msg}",
            "evidence": {"stderr": (r.stderr or "")[:500]},
        }

    result: dict = {
        "ok": True,
        "formatter": "ruff",
        "changed": changed,
        "file_size_before": file_size_before,
        "file_size_after": file_size_after,
    }

    if check and changed:
        # --check --diff 模式下,stdout 是 diff 文本
        diff_text = (r.stdout or "").rstrip()
        result["diff_summary"] = _summarize_diff(diff_text)

    if not changed:
        result["proposal"] = f"{p.name} 已符合 ruff 格式规范"
    else:
        delta = file_size_after - file_size_before
        sign = "+" if delta >= 0 else ""
        result["proposal"] = (
            f"ruff 重新格式化了 {p.name}({sign}{delta} 字节, "
            f"{file_size_before} → {file_size_after})"
        )
    return result


# ── clang-format 路径 ────────────────────────────────
#
# 2026-08-14 迁移:astyle → clang-format。
# Spec: docs/superpowers/specs/2026-08-14-clang-format-unify-design.md
#
# 设计要点:
#   1. clang-format 以 pip 包形式安装(``pip install clang-format``,
#      requirements.txt 已声明),通过 ``shutil.which("clang-format")`` 定位;
#      pip 包安装的是 .exe 二进制(非 .bat/.cmd),CREATE_NO_WINDOW 即可抑制黑框。
#   2. 永远 **二进制 stdin/stdout** 调用:读原文件 bytes → stdin → stdout bytes
#      直接写回。clang-format 对 stdin 字节流透明传递(不重编码),GBK 源文件、
#      UTF-8 BOM 都保持字节级保真(astyle 时代的编码 workaround 全部移除)。
#   3. ``--assume-filename=<绝对路径>``:clang-format 用它做语言检测,并从
#      该路径向上递归查找 ``.clang-format`` / ``_clang-format``。
#   4. ``--style=file --fallback-style=<兜底>``:项目内 .clang-format 优先;
#      没有时 fallback-style 生效(配置 default_style/default_indent)。
#   5. clang-format 原生保留主导行尾(DeriveLineEnding),CRLF 源文件不会
#      出现 astyle 时代的双空行 bug,无需 CRLF→LF 归一化。
#
# Author: elecvoid243, 2026-08-14


def _find_clang_format() -> list[str]:
    """查找 clang-format 可执行路径(pip 包 clang-format 安装到 PATH/Scripts)。"""
    found = shutil.which("clang-format")
    return [found] if found else []


def _resolve_clang_format_style(style: str, indent: int) -> str:
    """把配置的 style(+indent) 解析为 clang-format 内联 ``--style={...}`` 字符串。

    - clang-format 预设(llvm/google/...)→ ``{BasedOnStyle: <预设>, IndentWidth: <N>}``
    - legacy astyle 风格名 → 查 LEGACY_ASTYLE_STYLE_MAP;
      映射为完整 ``{...}`` 串时原样返回(串内已含缩进语义,indent 不再叠加);
      映射为预设名时按预设路径叠加 IndentWidth。

    本函数同时被 tools/code_check.py 复用,保证 format 与 check 的
    style 参数链完全一致(format/check 同源)。

    实现备注(2026-08-14):最初设计用 ``--style=file --fallback-style={...}``,
    但 clang-format 17 的 ``--fallback-style`` 只接受预设名(none/llvm/...),
    拒绝内联 ``{...}`` YAML,IndentWidth 无法随行。因此改为自实现
    ``.clang-format`` 向上发现(见 _clang_format_flags):找到 → ``--style=file``;
    找不到 → ``--style=<本函数返回的内联串>``,语义与 fallback-style 等价。
    """
    base = style
    if style in LEGACY_ASTYLE_STYLE_MAP:
        mapped = LEGACY_ASTYLE_STYLE_MAP[style]
        logger.warning(
            "[code_format] default_style=%r 是 legacy astyle 风格名,"
            "已映射为 clang-format %r;建议在插件配置中改用 clang-format 预设"
            "(llvm/google/chromium/microsoft/webkit/gnu)",
            style,
            mapped,
        )
        if mapped.startswith("{"):
            return mapped
        base = mapped
    return f"{{BasedOnStyle: {base}, IndentWidth: {indent}}}"


# clang-format 配置文件名(向上递归查找,与 clang-format 原生 --style=file 行为一致)
_CLANG_FORMAT_CONFIG_NAMES = (".clang-format", "_clang-format")


def _find_clang_format_config(p: Path) -> Path | None:
    """从文件所在目录向上递归查找 .clang-format / _clang-format。"""
    d = p if p.is_dir() else p.parent
    for parent in (d, *d.parents):
        for name in _CLANG_FORMAT_CONFIG_NAMES:
            candidate = parent / name
            if candidate.is_file():
                return candidate
    return None


def _clang_format_flags(p: Path, *, style: str, indent: int) -> list[str]:
    """clang-format 旗标部分(不含可执行路径),供测试断言与 code_check 复用。

    - 项目内存在 .clang-format → ``--style=file``(clang-format 原生发现)
    - 否则 → ``--style={BasedOnStyle: X, IndentWidth: N}`` 内联兜底
    """
    flags = [f"--assume-filename={p}"]
    if _find_clang_format_config(p) is not None:
        flags.append("--style=file")
    else:
        flags.append(f"--style={_resolve_clang_format_style(style, indent)}")
    return flags


def _format_with_clang_format(
    p: Path,
    *,
    check: bool,
    style: str,
    indent: int,
) -> dict:
    """C/C++/Java/JS/TS/C#: 调 clang-format (CLI,二进制 stdin/stdout)。

    流程:
      1. shutil.which("clang-format") 定位可执行文件
      2. 读原文件 bytes(不做任何重编码/行尾归一化)
      3. ``subprocess.run(args, input=before_bytes)`` 二进制管道
      4. 字节级/text 级比较 → changed
      5. check=False & changed=True → 写回 clang-format 原始输出字节
      6. check=True → 永远不写

    Args:
        p: 待格式化 C/C++/Java/JS/TS/C# 文件路径
        check: True = dry-run,不写回
        style: clang-format 预设或 legacy astyle 风格名
        indent: 缩进空格数(fallback-style 的 IndentWidth)

    Returns:
        标准 format() 返回 dict
    """
    cmd = _find_clang_format()
    if not cmd:
        return proposal_reply(
            False,
            "clang-format 未安装,无法格式化 C/C++/Java/JS/TS/C# 文件。"
            "请运行: pip install clang-format",
            error="clang-format 未安装",
            evidence={"file": str(p)},
            options=[
                "pip install clang-format",
                "切换到 formatter=ruff(不适用,仅 Python)",
            ],
        )

    # 二进制读取:不重编码、不归一化行尾,保持字节级保真
    before_bytes = p.read_bytes()
    file_size_before = len(before_bytes)
    # 解码仅供 changed 检测/diff 摘要(utf-8-sig BOM → utf-8 → cp936 → ...)
    before_text, _encoding = _decode_text_bytes(before_bytes)

    args = cmd + _clang_format_flags(p, style=style, indent=indent)
    try:
        r = subprocess.run(
            args,
            input=before_bytes,
            capture_output=True,
            timeout=_FORMAT_TIMEOUT,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "clang-format 超时"}
    except Exception as e:
        return {"ok": False, "error": f"clang-format 调用失败: {e}"}

    if r.returncode != 0:
        err_text, _ = _decode_text_bytes(r.stderr or b"")
        return {
            "ok": False,
            "error": f"clang-format 返回异常退出码 {r.returncode}",
            "evidence": {"stderr": err_text[:500]},
        }

    after_bytes = r.stdout or b""
    after_text, _ = _decode_text_bytes(after_bytes)
    file_size_after = len(after_bytes)

    # 用 splitlines() 行级比较,容错 \n / \r\n / \r 差异
    changed = _content_changed(before_text, after_text)

    result: dict = {
        "ok": True,
        "formatter": "clang-format",
        "changed": changed,
        "file_size_before": file_size_before,
        "file_size_after": file_size_after,
    }

    if check:
        # dry-run: 不写回
        if changed:
            diff_text = _make_unified_diff(
                before_text,
                after_text,
                fromfile=str(p),
                tofile=f"{p} (formatted)",
            )
            result["diff_summary"] = _summarize_diff(diff_text)
    else:
        if changed:
            # 写回 clang-format 原始输出字节:编码/BOM/行尾全部保持
            try:
                p.write_bytes(after_bytes)
            except OSError as e:
                return {"ok": False, "error": f"写回文件失败: {e}"}

    if not changed:
        result["proposal"] = f"{p.name} 已符合 clang-format 格式规范"
    else:
        delta = file_size_after - file_size_before
        sign = "+" if delta >= 0 else ""
        action = "预览将格式化" if check else "已重新格式化"
        result["proposal"] = (
            f"clang-format {action} {p.name}({sign}{delta} 字节,"
            f" {file_size_before} → {file_size_after})"
        )

    return result


# ── Diff 摘要 ──────────────────────────────────────


def _make_unified_diff(
    before: str, after: str, fromfile: str = "before", tofile: str = "after"
) -> str:
    """生成 unified diff 文本(供 LLM 预览改动)。"""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=fromfile,
        tofile=tofile,
        n=3,
    )
    return "".join(diff)


def _summarize_diff(diff_text: str, max_lines: int = _DIFF_PREVIEW_LINES) -> str:
    """截取 diff 文本前 N 行 + 统计。给 LLM 看,避免上下文爆炸。"""
    lines = diff_text.splitlines()
    total = len(lines)
    if total == 0:
        return ""
    head = lines[:max_lines]
    if total > max_lines:
        head.append(f"... (还有 {total - max_lines} 行未显示)")
    return "\n".join(head)


def _content_changed(before: str, after: str) -> bool:
    """判断两段文本内容是否实质不同(行尾规范化)。

    WHY: 行尾差异(``\\n`` vs ``\\r\\n``)不应误判为 changed(导致
    idempotent 失效)。

    实现:用 ``splitlines()`` 拆成行列表比较——它自动处理 ``\\n`` /
    ``\\r\\n`` / ``\\r`` 任意混合,行内字符原样。
    """
    return before.splitlines() != after.splitlines()


# ── 模块级 __all__ ─────────────────────────────────


__all__ = [
    "format",
    "PY_SUFFIXES",
    "CLANG_FORMAT_SUFFIXES",
    "VALID_CLANG_FORMAT_STYLES",
    "LEGACY_ASTYLE_STYLE_MAP",
    "_detect_formatter",
    "_supported_extensions",
    "_find_clang_format",
    "_resolve_clang_format_style",
    "_clang_format_flags",
    "_format_with_ruff",
    "_format_with_clang_format",
]


# ── 调试入口 ──────────────────────────────────────


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    if len(_sys.argv) < 2:
        print("Usage: python -m tools.code_format <filepath> [--check] [--style=...]")
        _sys.exit(2)
    fp = _sys.argv[1]
    rest = _sys.argv[2:]
    kw: dict = {}
    if "--check" in rest:
        kw["check"] = True
    for a in rest:
        if a.startswith("--style="):
            kw["style"] = a.split("=", 1)[1]
        elif a.startswith("--indent="):
            kw["indent"] = int(a.split("=", 1)[1])
    print(json.dumps(format(fp, **kw), ensure_ascii=False, indent=2))
