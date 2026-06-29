"""code_format — auto-format a single source file (Python / C·C++ / Java / JS·TS / C#).

与 code_check 的关系:code_check 是**只读**检查,code_format 是**写**工具
(可能修改文件)。LLM 在 plan 模式下不应调用本工具。

设计要点(2026-06-25, v2.14 引入):

1. **formatter = "auto" 路由**
   - .py → ruff format
   - .c/.cpp/.cc/.cxx/.h/.hpp/.hxx/.hh/.java/.js/.jsx/.ts/.tsx/.mjs/.cjs/.cs → AStyle

2. **astyle 调用的稳定性问题**
   AStyle 3.x 默认行为是"输出到 stdout 不原地修改",且不写回原文件
   时 exit=1(反直觉)。我们的策略是 **永远 stdin/stdout 调用**:
   - 读原文件 → stdin 喂给 AStyle
   - 拿 stdout 结果与原文件比较
   - check=False 时,只有 changed 才写回(不创建 .orig 备份)
   - check=True 时,**永远不写回**
   这样 astyle 的 --suffix 行为差异、--quiet 等开关都不影响主流程。

3. **ruff 调用**
   - check=False: `ruff format <file>`(直接写回)
   - check=True:  `ruff format --check --diff <file>(不写,只报告)
   ruff 返回码 0 = 无需改,非 0 = 有改动;同时读 stdout 的 diff 摘要。

4. **idempotent 语义**
   第二次格式化同一文件 → changed=False(我们用 stdlib difflib 比对,
   不依赖工具本身的"未变"信号,因为 astyle 永远返回新内容)。

5. **错误分类**
   - 工具未安装 → ok=False + error="X 未安装" + proposal(参考 code_check)
   - 不支持扩展名 → ok=False + supported_extensions
   - 文件不存在 → ok=False + 错误信息
   - 解析失败 → ok=False + 原始 stderr 摘要
   - 超时 → ok=False + error="X 超时"

Author: elecvoid243, 2026-06-25
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ._helpers import _NO_WINDOW_KWARGS, detect_console_encoding, proposal_reply

# ── 扩展名 → formatter 路由表 ─────────────────────────
# 顺序敏感:.py 必须先于 ASTYLE_SUFFIXES 集合。
PY_SUFFIXES = {".py"}

# AStyle 3.6 官方支持的语言: C, C++, C++/CLI, Objective-C, C#, Java
# 加上社区共识的 JS/TS 兼容(sucessful rendering but no language-specific rules)。
ASTYLE_SUFFIXES: set[str] = {
    # C / C++
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx", ".hh",
    # Java
    ".java",
    # JavaScript (astyle 兼容处理,无语言特定规则)
    ".js", ".jsx", ".mjs", ".cjs",
    # C#
    ".cs",
}

# astyle --style= 选项的合法值(对应 AStyle 3.6 文档)
VALID_ASTYLE_STYLES: frozenset[str] = frozenset(
    {
        "allman", "java", "kr", "linux", "google", "stroustrup",
        "whitesmith", "horstmann", "ratliff", "vtk", "none",
    }
)

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
    style: str = "allman",
    indent: int = 4,
) -> dict:
    """对单个源文件运行代码格式化。

    Args:
        filepath: 源文件绝对路径。
        formatter: auto / ruff / astyle。
        check: True=dry-run,只检测不写入。
        style: astyle style 预设(仅 astyle 生效)。
        indent: 缩进空格数(astyle → --indent=spaces=N;ruff 不支持,作为 metadata)。

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
                    f"code_format 仅支持 Python 和 AStyle 支持的语言。"
                ),
                "supported_extensions": _supported_extensions(),
            }
    elif formatter not in ("ruff", "astyle"):
        return {
            "ok": False,
            "error": f"不支持的 formatter: {formatter}",
            "supported": ["auto", "ruff", "astyle"],
        }

    # ── 3. 风格参数校验(astyle) ──
    formatter_options = {"style": style, "indent": indent}
    if formatter == "astyle" and style not in VALID_ASTYLE_STYLES:
        return {
            "ok": False,
            "error": f"不支持的 astyle 风格: {style}",
            "supported_styles": sorted(VALID_ASTYLE_STYLES),
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
        result = _format_with_astyle(
            p, check=check, style=style, indent=indent,
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
    if suffix in ASTYLE_SUFFIXES:
        return "astyle"
    return None


def _supported_extensions() -> list[str]:
    return sorted(PY_SUFFIXES | ASTYLE_SUFFIXES)


# ── ruff 路径 ────────────────────────────────────────


def _find_ruff() -> list[str]:
    """查找 ruff 可执行路径:优先 PATH,备选 python -m ruff。

    与 code_check._find_ruff 行为一致(同一项目复用同一工具)。
    """
    if shutil.which("ruff"):
        return ["ruff"]
    try:
        subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            timeout=5,
            check=True,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
        return [sys.executable, "-m", "ruff"]
    except Exception:
        return []


def _format_with_ruff(p: Path, *, check: bool, indent: int) -> dict:
    """Python: 调 ruff format。indent 仅作 metadata(ruff 用自身默认配置)。"""
    ruff_cmd = _find_ruff()
    if not ruff_cmd:
        return proposal_reply(
            False,
            "ruff 未安装,无法格式化 Python 文件。请运行: pip install ruff",
            error="ruff 未安装",
            evidence={"python_file": str(p)},
            options=[
                "pip install ruff",
                "切换到 formatter=astyle(不适用,仅 C/C++/Java/JS/TS/C#)",
            ],
        )

    before_bytes = p.read_bytes()
    file_size_before = len(before_bytes)

    if check:
        # --check 不写回;--diff 输出 diff 文本(写到 stdout)
        args = ruff_cmd + ["format", "--check", "--diff", str(p)]
    else:
        # 直接格式化(写回原文件)
        args = ruff_cmd + ["format", str(p)]

    try:
        r = subprocess.run(
            args, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=_FORMAT_TIMEOUT,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ruff 超时"}
    except Exception as e:
        return {"ok": False, "error": f"ruff 调用失败: {e}"}

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
        changed = (file_size_after != file_size_before)

    result: dict = {
        "ok": True,
        "formatter": "ruff",
        "changed": changed,
        "file_size_before": file_size_before,
        "file_size_after": file_size_after,
    }

    if r.returncode == 2:
        # 错误:读取 stderr 第一行作为错误描述
        err = (r.stderr or "").strip().splitlines()
        err_msg = err[0] if err else "ruff 返回错误"
        return {
            "ok": False,
            "error": f"ruff 格式化失败: {err_msg}",
            "evidence": {"stderr": (r.stderr or "")[:500]},
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


# ── astyle 路径 ──────────────────────────────────────


def _find_astyle() -> list[str]:
    """查找 AStyle 可执行路径:3 级 fallback。

    顺序:
      1. ASTYLE_PATH 环境变量
      2. shutil.which("AStyle") / shutil.which("astyle")
      3. (当前不实现平台特定常见路径;env + which 已覆盖 win/mac/linux)
    """
    env_path = os.environ.get("ASTYLE_PATH")
    if env_path:
        ep = Path(env_path)
        if ep.exists():
            return [str(ep)]
    for name in ("AStyle.exe", "AStyle", "astyle"):
        found = shutil.which(name)
        if found and Path(found).exists():
            return [found]
    return []


def _format_with_astyle(
    p: Path, *, check: bool, style: str, indent: int,
) -> dict:
    """C/C++/Java/JS/TS/C#: 调 AStyle via stdin/stdout。

    关键:我们**永远不**让 astyle 自己写回原文件。流程:
      1. 读原文件 → stdin
      2. astyle 写 stdout
      3. compare → changed
      4. check=False & changed=True → 写回原文件
      5. check=True → 永远不写
    """
    astyle_cmd = _find_astyle()
    if not astyle_cmd:
        return proposal_reply(
            False,
            "astyle 未安装,无法格式化 C/C++/Java/JS/TS/C# 文件。"
            "请运行: pip install astyle(自动安装 AStyle.exe 到 Scripts/目录)",
            error="astyle 未安装",
            evidence={"file": str(p)},
            options=[
                "pip install astyle",
                "设置环境变量 ASTYLE_PATH 指向 AStyle.exe",
                "切换到 formatter=ruff(不适用,仅 Python)",
            ],
        )

    before_bytes = p.read_bytes()
    file_size_before = len(before_bytes)

    # 解码源文件内容作为 astyle stdin 输入。优先 UTF-8(源代码首选),
    # 中文 Windows 下回退到 GBK(cp936)。这样 astyle 收到的是字符串,
    # 配合 ``text=True`` + ``encoding=detect_console_encoding()`` 正常处理 stdout。
    try:
        before_text = before_bytes.decode("utf-8")
    except UnicodeDecodeError:
        before_text = before_bytes.decode(detect_console_encoding(), errors="replace")

    args = astyle_cmd + [
        f"--style={style}",
        f"--indent=spaces={indent}",
    ]

    try:
        r = subprocess.run(
            args, input=before_text, capture_output=True,
            timeout=_FORMAT_TIMEOUT,
            # WHY: astyle.exe 是 Windows C++ 程序,stdout/stderr 走系统 ANSI 代码页;
            # 中文 Windows 是 cp936(GBK)。用 detect_console_encoding() 适配。
            text=True,
            encoding=detect_console_encoding(),
            errors="replace",
            # astyle.exe 是 Windows CUI 子程序;pythonw.exe 启动下抑制黑窗
            **_NO_WINDOW_KWARGS,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "astyle 超时"}
    except Exception as e:
        return {"ok": False, "error": f"astyle 调用失败: {e}"}

    after_text = r.stdout
    if r.returncode not in (0, 1):
        # astyle 返回非 0/1 → 真错误
        return {
            "ok": False,
            "error": f"astyle 返回异常退出码 {r.returncode}",
            "evidence": {"stderr": (r.stderr or "")[:500]},
        }

    # astyle 极少见 stderr-only 错误信息;但已从 stdout 拿到结果
    file_size_after = len(after_text.encode("utf-8"))
    # 比较时规范化行尾:Windows 的 write_text 会把 \n → \r\n,
    # 而 astyle stdin/stdout 保留 \n。直接 == 比较会把"行尾差异"误判为 changed。
    # 用 splitlines() 行内比较,容错 \n / \r\n / \r。
    changed = _content_changed(before_bytes.decode("utf-8", errors="replace"), after_text)

    result: dict = {
        "ok": True,
        "formatter": "astyle",
        "changed": changed,
        "file_size_before": file_size_before,
        "file_size_after": file_size_after,
    }

    if check:
        # dry-run: 不写回
        if changed:
            diff_text = _make_unified_diff(
                before_bytes.decode("utf-8", errors="replace"),
                after_text,
                fromfile=str(p),
                tofile=f"{p} (formatted)",
            )
            result["diff_summary"] = _summarize_diff(diff_text)
    else:
        if changed:
            # 写回原文件(UTF-8,兼容中英文注释)
            try:
                p.write_text(after_text, encoding="utf-8")
            except OSError as e:
                return {"ok": False, "error": f"写回文件失败: {e}"}

    if not changed:
        result["proposal"] = f"{p.name} 已符合 astyle 格式规范"
    else:
        delta = file_size_after - file_size_before
        sign = "+" if delta >= 0 else ""
        action = "预览将格式化" if check else "已重新格式化"
        result["proposal"] = (
            f"astyle {action} {p.name}({sign}{delta} 字节,"
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
        before_lines, after_lines,
        fromfile=fromfile, tofile=tofile,
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

    WHY: Windows 上 ``Path.write_text`` 会把 ``\\n`` → ``\\r\\n``;
    astyle stdin/stdout 保留 ``\\n``。直接 ``==`` 比较会把
    "行尾差异"误判为 changed(导致 idempotent 失效)。

    实现:用 ``splitlines()`` 拆成行列表比较——它自动处理 ``\\n`` /
    ``\\r\\n`` / ``\\r`` 任意混合,行内字符原样。
    """
    return before.splitlines() != after.splitlines()


# ── 模块级 __all__ ─────────────────────────────────


__all__ = [
    "format",
    "PY_SUFFIXES",
    "ASTYLE_SUFFIXES",
    "VALID_ASTYLE_STYLES",
    "_detect_formatter",
    "_supported_extensions",
    "_find_ruff",
    "_find_astyle",
    "_format_with_ruff",
    "_format_with_astyle",
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
