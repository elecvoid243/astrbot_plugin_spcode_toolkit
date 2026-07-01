"""
code_check — unified syntax + style check for Python and C/C++ source files.

Replaces the previous split between syntax_check and lint_runner: for both Python
and C/C++, a single linter covers BOTH syntax and style, so two tools would just
duplicate work.

  Python (ruff):     a single `ruff check` invocation handles parse errors AND
                     PEP 8 / common-lint issues.
  C/C++ (cppcheck    cppcheck runs FIRST for correctness; if it reports any
          + cpplint): problem-level issue, return immediately and skip cpplint.
                     Otherwise fall through to cpplint for style checks.

Supported extensions:
  .py   → ruff
  .c .cpp .cc .cxx .h .hpp .hxx .hh → cppcheck (auto) + cpplint fallback
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from ._helpers import (
    _NO_WINDOW_KWARGS,
    _get_console_python,
    detect_console_encoding,
    proposal_reply,
)

# 扩展名 → linter 映射
_PY_SUFFIXES = {".py"}
_CPP_SUFFIXES = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx", ".hh"}

# 给前 N 个 issue 附加源代码上下文，方便 LLM 定位
_CONTEXT_LINES = 2
_MAX_CONTEXT_ISSUES = 5

# cpplint 单行输出格式: "<path>:<lineno>:  <message>  [<category>] [<level>]"
# 例: "src/foo.cpp:42:  Line ends in whitespace.  [whitespace/end_of_line] [4]"
_CPPLINT_LINE_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):\s+(?P<message>.+?)\s+\[(?P<category>[^\]]+)\]\s+\[(?P<level>\d+)\]\s*$"
)
_CPPLINT_TOTAL_RE = re.compile(r"^Total errors found.*:\s*(\d+)", re.IGNORECASE)


def _detect_linter(p: Path) -> str | None:
    """根据文件扩展名选择 linter。返回 None 表示不支持。"""
    suffix = p.suffix.lower()
    if suffix in _PY_SUFFIXES:
        return "ruff"
    if suffix in _CPP_SUFFIXES:
        return "cpplint"  # auto 模式走 cpplint 入口（内部已含 cppcheck 短路）
    return None


def _supported_extensions() -> list[str]:
    return sorted(_PY_SUFFIXES | _CPP_SUFFIXES)


def check(filepath: str, linter: str = "auto") -> dict:
    """对单个源文件运行 syntax + style 合并检查。

    Args:
        filepath: 源文件路径。扩展名决定默认 linter。
        linter: auto (按扩展名) / ruff / cpplint / cppcheck。

    Returns:
        ok=True  → {"ok": True, "linter": "...", "issues": [...], "count": N}
        ok=False → {"ok": False, "error": "..."}
    """
    p = Path(filepath)
    if not p.exists():
        return {"ok": False, "error": f"文件不存在: {filepath}"}

    if linter == "auto":
        # auto 模式：按扩展名路由，且 .c/.cpp 等 C/C++ 走 _run_cpplint
        # （含 cppcheck 短路逻辑），而不是 _run_cpplint_only
        linter = _detect_linter(p)
        if not linter:
            return {
                "ok": False,
                "error": (
                    f"不支持的扩展名: {p.suffix}。本工具仅支持 Python 和 C/C++。"
                ),
                "supported_extensions": _supported_extensions(),
            }
        # C/C++ 在 auto 模式下走含 cppcheck 短路的 _run_cpplint
        if linter == "cpplint":
            runner = _run_cpplint
        else:
            runner = {"ruff": _run_ruff}.get(linter)
        if not runner:
            return {
                "ok": False,
                "error": f"auto 模式不支持的 linter: {linter}",
            }
    else:
        # 显式 linter：cpplint 走 _run_cpplint_only（**不**调 cppcheck），
        # cppcheck 走 _run_cppcheck_only（**不**调 cpplint）
        runners = {
            "ruff": _run_ruff,
            "cpplint": _run_cpplint_only,
            "cppcheck": _run_cppcheck_only,
        }
        runner = runners.get(linter)
        if not runner:
            return {
                "ok": False,
                "error": f"不支持的 linter: {linter}",
                "supported": sorted(runners.keys()),
            }

    result = runner(p)
    # 给前 5 个 issue 附加代码上下文
    if result.get("ok") and result.get("issues"):
        _add_context(p, result["issues"])
    return result


# ── C/C++: cppcheck（先于 cpplint，正确性优先）──────────


# cppcheck 问题级 severity（过滤掉 information/note/debug/missingInclude 等噪声）
_CPPCHECK_PROBLEM_SEVERITIES = frozenset(
    {"error", "warning", "style", "performance", "portability"}
)

# 短路模式常量（与 _conf_schema.json 的 options 保持一致）
# 3 种模式：
#   error   → cppcheck 有 error → 短路
#   warning → cppcheck 有 error 或 warning → 短路
#   never   → 永不短路，两个工具都跑（合并输出）
_SHORTCIRCUIT_ERROR = "error"
_SHORTCIRCUIT_WARNING = "warning"
_SHORTCIRCUIT_NEVER = "never"

_VALID_SHORTCIRCUIT_MODES = frozenset(
    {_SHORTCIRCUIT_ERROR, _SHORTCIRCUIT_WARNING, _SHORTCIRCUIT_NEVER}
)


def _get_shortcircuit_mode() -> str:
    """读取短路模式。优先级：模块级覆盖 > 环境变量 > 默认 "all"。

    允许测试或独立运行时通过 `code_check.SHORTCIRCUIT_MODE = "error"` 临时覆盖。
    """
    # 模块级覆盖（仅在 main 启动时通过 `code_check.SHORTCIRCUIT_MODE = ...` 设置）
    module_mode = globals().get("SHORTCIRCUIT_MODE")
    if module_mode and module_mode in _VALID_SHORTCIRCUIT_MODES:
        return module_mode
    # 环境变量（main.py 在 __init__ 时注入）
    env_mode = os.environ.get("CPPCHECK_SHORTCIRCUIT", _SHORTCIRCUIT_ERROR)
    return env_mode if env_mode in _VALID_SHORTCIRCUIT_MODES else _SHORTCIRCUIT_ERROR


# cppcheck 单行输出格式: "<path>:<lineno>:<col>:  <severity>: <message>  [<id>]"
# 例: "src/foo.cpp:6:12: error: Mismatching allocation and deallocation: p
#                     [mismatchAllocDealloc]"
_CPPCHECK_LINE_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<severity>\w+):\s+(?P<message>.+?)\s+\[(?P<id>[^\]]+)\]\s*$"
)


def _find_cppcheck() -> list:
    """查找 cppcheck 可执行路径：4 级 fallback（仿 _get_es_path）。

    查找顺序：
    1. CPPCHECK_PATH 环境变量（用户显式覆盖）
    2. shutil.which("cppcheck.exe") / "cppcheck"
    3. 常见安装路径（C:/Program Files/cppcheck/cppcheck.exe 等）
    """
    # 1. 环境变量
    env_path = os.environ.get("CPPCHECK_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return [str(p)]

    # 2. PATH 中查找
    for name in ("cppcheck.exe", "cppcheck"):
        found = shutil.which(name)
        if found and Path(found).exists():
            return [found]

    # 3. 常见安装路径
    candidates = [
        Path("C:/Program Files/Cppcheck/cppcheck.exe"),
        Path("C:/Program Files (x86)/Cppcheck/cppcheck.exe"),
        Path("D:/Program Files/Cppcheck/cppcheck.exe"),
        Path("C:/cppcheck/cppcheck.exe"),
        Path("D:/cppcheck/cppcheck.exe"),
    ]
    for c in candidates:
        if c.exists():
            return [str(c)]

    return []


def _parse_cppcheck_output(stderr: str) -> list[dict]:
    """解析 cppcheck stderr 为 issues 列表。

    cppcheck 2.21+ 即使发现问题也返回退出码 0，所以必须从输出内容判断。
    只保留 problem 级别 severity（error/warning/style/performance/portability），
    过滤掉 information/note/debug/missingInclude 等噪声（如
    "Include file <iostream> not found" 这种与代码正确性无关的提示）。
    """
    issues: list[dict] = []
    for line in (stderr or "").splitlines():
        m = _CPPCHECK_LINE_RE.match(line)
        if not m:
            continue
        severity = m.group("severity").lower()
        if severity not in _CPPCHECK_PROBLEM_SEVERITIES:
            continue
        try:
            issues.append(
                {
                    "line": int(m.group("line")),
                    "col": int(m.group("col")),
                    "severity": severity,
                    "id": m.group("id").strip(),
                    "message": m.group("message").strip(),
                }
            )
        except (ValueError, IndexError):
            continue
    return issues


def _run_cppcheck(p: Path) -> dict | None:
    """对 C/C++ 文件运行 cppcheck（pure：不应用 mode 过滤，返回全部 severity）。

    Returns:
        None: 工具不可用
        dict: 结构化 result（ok=True + issues + count + ...）
    """
    cppcheck_cmd = _find_cppcheck()
    if not cppcheck_cmd:
        return None  # 未安装，让 caller 跑 cpplint
    try:
        r = subprocess.run(
            cppcheck_cmd
            + [
                "--enable=warning,style,performance,portability",
                "--quiet",  # 抑制 "Checking ... " 进度信息
                "--inline-suppr",  # 支持代码内 // cppcheck-suppress 注释
                str(p),
            ],
            capture_output=True,
            text=True,
            # WHY: cppcheck.exe 是 Windows 原生 C++ 程序，stderr/stdout 遵循系统 ANSI
            # 代码页。中文 Windows 是 cp936 (GBK)；之前硬编码 utf-8 在中文环境下
            # 产生  乱码。改用 detect_console_encoding() 自动适配。
            encoding=detect_console_encoding(),
            errors="replace",
            timeout=30,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
        # cppcheck 2.21+ 总是返回 0，所以**只**依赖输出内容判断
        all_issues = _parse_cppcheck_output(r.stderr or "")

        # pure 模式：不过滤 severity，返回全部 issues
        if not all_issues:
            return {"ok": True, "linter": "cppcheck", "issues": [], "count": 0}

        return {
            "ok": True,
            "linter": "cppcheck",
            "issues": all_issues,
            "count": len(all_issues),
            "proposal": f"cppcheck 发现 {len(all_issues)} 个问题",
            "options": ["逐个修复", "确认是否有意为之"],
        }
    except subprocess.TimeoutExpired:
        # 超时视为工具不可用，让 caller 跑 cpplint
        return {
            "ok": False,
            "error": "cppcheck 超时",
            "evidence": {"cpp_file": str(p)},
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Python: ruff ─────────────────────────────────────


def _find_ruff() -> list:
    """查找 ruff 可执行路径：优先 PATH，备选 python -m ruff。"""
    if shutil.which("ruff"):
        return ["ruff"]
    try:
        subprocess.run(
            [_get_console_python(), "-m", "ruff", "--version"],
            capture_output=True,
            timeout=5,
            check=True,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
        return [_get_console_python(), "-m", "ruff"]
    except Exception:
        return []


def _run_ruff(p: Path) -> dict:
    ruff_cmd = _find_ruff()
    if not ruff_cmd:
        return proposal_reply(
            False,
            "ruff 未安装，无法检查 Python 文件。请运行: pip install ruff",
            error="ruff 未安装",
            evidence={"python_file": str(p)},
            options=["pip install ruff", "切换到 linter=cpplint 不适用（仅 C/C++）"],
        )
    try:
        r = subprocess.run(
            ruff_cmd + ["check", "--output-format", "json", str(p)],
            capture_output=True,
            text=True,
            # ruff 输出 UTF-8 JSON;显式 encoding 防止中文 Windows 下默认 cp936
            # 触发 UnicodeDecodeError(stdout 变 None);与 cpplint 路径保持一致。
            encoding="utf-8",
            errors="replace",
            timeout=30,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
        if r.returncode == 0:
            return {"ok": True, "linter": "ruff", "issues": [], "count": 0}
        issues = json.loads(r.stdout) if r.stdout.strip() else []
        result = {"ok": True, "linter": "ruff", "issues": issues, "count": len(issues)}
        if issues:
            result["proposal"] = f"ruff 发现 {len(issues)} 个问题"
            result["options"] = ["逐个修复", "确认是否有意为之"]
        return result
    except json.JSONDecodeError:
        return {
            "ok": True,
            "linter": "ruff",
            "raw": r.stdout.strip()[:2000],
            "count": 0,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ruff 超时"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── C/C++: cpplint（pure；先调 cppcheck 见 _run_cpplint） ──


def _find_cpplint() -> list:
    """查找 cpplint 可执行路径：优先 PATH，备选 python -m cpplint。"""
    if shutil.which("cpplint"):
        return ["cpplint"]
    try:
        subprocess.run(
            [_get_console_python(), "-m", "cpplint", "--version"],
            capture_output=True,
            timeout=5,
            check=True,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
        return [_get_console_python(), "-m", "cpplint"]
    except Exception:
        return []


def _run_cpplint_only(p: Path) -> dict:
    """只跑 cpplint（不调 cppcheck）。供 linter='cpplint' 显式调用，以及作为
    cppcheck 短路检查通过后的 fallback。"""
    cpplint_cmd = _find_cpplint()
    if not cpplint_cmd:
        return proposal_reply(
            False,
            "cpplint 未安装，无法检查 C/C++ 文件。请运行: pip install cpplint",
            error="cpplint 未安装",
            evidence={"cpp_file": str(p)},
            options=["pip install cpplint", "切换到 linter=ruff 不适用（仅 Python）"],
        )
    try:
        r = subprocess.run(
            cpplint_cmd + [str(p)],
            capture_output=True,
            text=True,
            # WHY: cpplint 是 Python 写的，stdout/stderr 默认 utf-8；但用户的
            # 中文源码/注释、文件路径被 cpplint 印出时，Windows 下走控制台编码。
            # 用 detect_console_encoding() 自动适配，与 cppcheck 保持一致。
            encoding=detect_console_encoding(),
            errors="replace",
            timeout=30,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
        # cpplint 把 issues 写到 STDERR，把 "Done processing" + "Total errors" 写到 STDOUT。
        # 早期版本只用 r.stdout 解析时漏掉了所有 issues（count=0 但 cpplint_total_reported>0）。
        # 合并两流后用同一套正则解析，对未来 stream 分配变化也保持兼容。
        combined = (r.stderr or "") + "\n" + (r.stdout or "")
        issues, total_reported = _parse_cpplint_output(combined)

        if r.returncode == 0 and not issues:
            result = {"ok": True, "linter": "cpplint", "issues": [], "count": 0}
        else:
            result = {
                "ok": True,
                "linter": "cpplint",
                "issues": issues,
                "count": len(issues),
            }
            if total_reported is not None and total_reported != len(issues):
                result["cpplint_total_reported"] = total_reported
            if issues:
                result["proposal"] = f"cpplint 发现 {len(issues)} 个问题"
                result["options"] = ["逐个修复", "确认是否有意为之"]
        return result
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "cpplint 超时"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _filter_cppcheck_by_mode(result: dict | None, mode: str) -> dict:
    """按 shortcircuit 模式过滤 cppcheck issues。

    用途：_run_cpplint 入口需要按 mode 判断"是否有硬错误"以决定是否短路。
    merge 模式不短路时，**不**调用此函数，直接用 raw cppcheck 全部 issues。
    """
    if result is None:
        return {"ok": False, "issues": [], "count": 0}
    all_issues = result.get("issues", [])
    if not all_issues:
        return result  # 已是空 issues，无需过滤

    if mode == _SHORTCIRCUIT_ERROR:
        # 只有 error 视为硬错误
        issues = [i for i in all_issues if i["severity"] == "error"]
    elif mode == _SHORTCIRCUIT_WARNING:
        # error 或 warning 视为硬错误
        issues = [i for i in all_issues if i["severity"] in ("error", "warning")]
    else:
        # 未知 mode fallback：不过滤
        return result

    if len(issues) == len(all_issues):
        # 过滤后没变，无需透传 suppressed
        return result

    if not issues:
        return {"ok": True, "linter": "cppcheck", "issues": [], "count": 0}

    # 透传被过滤掉的"软提示"
    suppressed = [i for i in all_issues if i not in issues]
    suppressed_desc = (
        "style/performance/portability"
        if mode == _SHORTCIRCUIT_WARNING
        else "非 error 级别"
    )
    return {
        "ok": True,
        "linter": "cppcheck",
        "issues": issues,
        "count": len(issues),
        "proposal": (
            f"cppcheck 发现 {len(issues)} 条代码正确性问题；"
            f"cpplint 发现 {len(suppressed)} 条{suppressed_desc} 代码风格问题 "
        ),
        "options": ["逐个修复", "确认是否有意为之"],
        "cppcheck_suppressed": [
            {
                "line": i["line"],
                "severity": i["severity"],
                "id": i["id"],
                "message": i["message"],
            }
            for i in suppressed
        ],
    }


def _run_cppcheck_only(p: Path):
    """供 linter='cppcheck' 显式调用：只跑 cppcheck，返回全部 severity。

    _run_cppcheck 本身已是 pure（无 mode 过滤），所以直接调即可。
    """
    r = _run_cppcheck(p)
    if r is None:
        return proposal_reply(
            False,
            "cppcheck 未安装，无法检查。请从 https://cppcheck.sourceforge.io/ 下载并安装，"
            "或设置 CPPCHECK_PATH 环境变量指向 cppcheck.exe。",
            error="cppcheck 未安装",
            evidence={"cpp_file": str(p)},
            options=[
                "安装 cppcheck: https://cppcheck.sourceforge.io/",
                "设置 CPPCHECK_PATH 环境变量",
                "在 WebUI 的 cppcheck_path 字段填入 cppcheck.exe 完整路径",
            ],
        )
    return r


def _run_cpplint(p: Path) -> dict:
    """auto 模式下的 C/C++ 入口。

    根据 shortcircuit 模式分派：
      - merge:   两个工具都跑，组装分组输出（linters.cppcheck + linters.cpplint）
      - 其它:    原短路逻辑（cppcheck 有问题就立即返回，否则跑 cpplint）
    """
    mode = _get_shortcircuit_mode()

    # 跑 cppcheck（pure，全部 severity）
    cppcheck_raw = _run_cppcheck(p)

    # 短路判断：按 mode 过滤 cppcheck issues
    if mode != _SHORTCIRCUIT_NEVER and cppcheck_raw is not None:
        cppcheck_filtered = _filter_cppcheck_by_mode(cppcheck_raw, mode)
        if cppcheck_filtered.get("count", 0) > 0:
            # 找到硬错误：附加 context 后短路返回
            if cppcheck_filtered.get("issues"):
                _add_context(p, cppcheck_filtered["issues"])
            return cppcheck_filtered

    # 不短路：跑 cpplint，两个结果合并
    # 合并用 cppcheck 全部 severity（不应用 mode 过滤），让 LLM 看到所有提示
    cpplint_result = _run_cpplint_only(p)
    return _merge_linter_results(p, cppcheck_raw, cpplint_result)


def _extract_linter_block(result, expected_linter: str) -> dict:
    """把单工具 result 转换为 merge 模式的 block 结构。

    用于 _merge_linter_results：把 _run_cppcheck / _run_cpplint_only 的返回值
    统一包装成前端易于分组的 schema（含 available / ok / issues / count / error）。
    """
    if result is None:
        return {
            "available": False,
            "ok": False,
            "issues": [],
            "count": 0,
            "error": f"{expected_linter} 未安装或不可用",
            "_linter": expected_linter,
        }
    return {
        "available": True,
        "ok": result.get("ok", False),
        "issues": result.get("issues", []),
        "count": result.get("count", 0),
        "error": result.get("error", ""),
        "_linter": expected_linter,
    }


def _merge_linter_results(p: Path, cppcheck_result, cpplint_result) -> dict:
    """merge 模式：两个工具都跑，把结果组装成分组输出。

    Schema（最简化）：
      - 顶层只保留 ok / linter / linters / proposal（proposal 给 LLM 摘要）
      - 每个 linter block 自带 issues/count，**唯一权威源**（取消扁平合并）
      - 取消顶层 issues/count：避免与 linters.* 重复，消除"哪个是权威源"歧义
    """
    cpp_block = _extract_linter_block(cppcheck_result, "cppcheck")
    cp_block = _extract_linter_block(cpplint_result, "cpplint")

    # 防止 None issues
    cpp_block["issues"] = list(cpp_block.get("issues") or [])
    cp_block["issues"] = list(cp_block.get("issues") or [])

    # merge 模式特有：两个工具的 issues 都附加 code context（统一为前 5 条）
    if cpp_block["issues"]:
        _add_context(p, cpp_block["issues"])
    if cp_block["issues"]:
        _add_context(p, cp_block["issues"])

    # 给每条 issue 加 _linter 标记，方便 LLM 单条引用时识别来源
    for iss in cpp_block["issues"]:
        iss["_linter"] = "cppcheck"
    for iss in cp_block["issues"]:
        iss["_linter"] = "cpplint"

    # 构造 proposal（LLM 决策参考；唯一允许在 merge 顶层出现的"摘要"字段）
    total_count = cpp_block["count"] + cp_block["count"]
    available_count = sum(1 for b in (cpp_block, cp_block) if b["available"])

    if total_count == 0:
        if available_count == 2:
            proposal_text = "cppcheck + cpplint 都通过"
        else:
            proposal_text = f"通过（{available_count}/2 个工具可用）"
    else:
        parts = []
        for name, block in (("cppcheck", cpp_block), ("cpplint", cp_block)):
            if block["available"] and block["count"] > 0:
                parts.append(f"{name} {block['count']}")
        proposal_text = f"{' + '.join(parts)} 共 {total_count} 个问题"

    return {
        "ok": True,
        "linter": "merge",
        "linters": {
            "cppcheck": cpp_block,
            "cpplint": cp_block,
        },
        "proposal": proposal_text,
    }


def _parse_cpplint_output(stdout: str) -> tuple[list[dict], int | None]:
    """解析 cpplint 输出为 issues 列表。

    cpplint 会在每行打印 "<path>:<lineno>:  <message>  [<category>] [<level>]"，
    末尾会有 "Total errors found: N"。解析失败的行会被忽略。

    Returns:
        (issues, total_or_None)  total 是 cpplint 自己报告的总数
    """
    issues: list[dict] = []
    total: int | None = None
    for line in stdout.splitlines():
        m_total = _CPPLINT_TOTAL_RE.search(line)
        if m_total:
            try:
                total = int(m_total.group(1))
            except (ValueError, IndexError):
                pass
            continue
        m = _CPPLINT_LINE_RE.match(line)
        if m:
            try:
                issues.append(
                    {
                        "line": int(m.group("line")),
                        "message": m.group("message").strip(),
                        "category": m.group("category").strip(),
                        "level": int(m.group("level")),
                    }
                )
            except (ValueError, IndexError):
                continue
    return issues, total


# ── 通用：为前 N 个 issue 附加代码上下文 ─────────────


def _get_line_context(
    p: Path, line_num: int, context_size: int = _CONTEXT_LINES
) -> list[str] | None:
    """读取文件，返回指定行附近 context_size 行的上下文。"""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    except Exception:
        return None
    start = max(0, line_num - context_size - 1)
    end = min(len(lines), line_num + context_size)
    ctx: list[str] = []
    for i in range(start, end):
        actual = i + 1
        marker = "→" if actual == line_num else " "
        ctx.append(f"{marker}{actual:>4}: {lines[i].rstrip()[:120]}")
    return ctx


def _add_context(p: Path, issues: list, max_issues: int = _MAX_CONTEXT_ISSUES) -> None:
    """为前 max_issues 个问题附加代码上下文。ruff/cpplint 输出位置字段名不同，兼容处理。"""
    for issue in issues[:max_issues]:
        line_num = issue.get("line")
        if not line_num:
            loc = issue.get("location")
            if isinstance(loc, dict):
                line_num = loc.get("row")
        if not line_num:
            # 尝试从 message 中提取 (line N)（兼容未来其它风格）
            m = re.search(r"\(line (\d+)\)", issue.get("message", ""))
            if m:
                line_num = int(m.group(1))
        if line_num:
            ctx = _get_line_context(p, line_num)
            if ctx:
                issue["context"] = ctx
