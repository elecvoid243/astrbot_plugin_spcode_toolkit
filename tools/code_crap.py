"""code_crap — CRAP(Change Risk Anti-Patterns)指标检测。

CRAP = CC² × (1 − coverage)³ + CC,按函数组合圈复杂度与测试覆盖率,
识别"复杂且无测试保护"的高风险代码(阈值 30 为业界通行警告线)。

双引擎路由(扩展名决定):
  .py                              → crap4py 0.1.1 积木组合(engine="crap4py")
  .c .cpp .cc .cxx .h .hpp .hxx .hh → lizard + 自建 LCOV DA 行覆盖(engine="lizard")

设计要点(2026-08-30):
  1. **统一 schema**:两路径同构(functions/summary/proposal),按 crap 降序。
  2. **覆盖率可选**:lcov 缺失时 coverage=None、crap=cc²+cc(最坏情况),
     顶层 coverage_source="none" 标注 —— AI 刚写完代码未跑测试时也可立即调用。
  3. **Python 用 crap4py 积木组合**而非 build_report:保留行号(discover_functions
     的 line_range),镜像上游 _report._score_entry 的编排;
     collect_files 注入绕过其对 test_*.py 的跳过(工具被显式点名时应分析)。
  4. **C/C++ 用行覆盖而非分支覆盖**:gcov 的 BRDA 基于汇编级控制流
     (gcovr issue #339),对模板/异常代码失真。
  5. **CRAP 公式单一真相源**:两路径统一复用 crap4py._crap.crap_score。

已知局限:
  - Python 路径按 bare name 匹配 CC(嵌套同名函数会串号),与上游 _score_entry 一致。
  - Python 路径源码读取固定 utf-8(crap4py discovery 默认),GBK 源文件会得到
    "未发现函数"。
  - 跨引擎 CC 口径不同(radon 表达式感知模型 vs lizard 自有模型),跨语言分数不可比。

Spec: docs/superpowers/specs/2026-08-30-code-crap-tool-design.md
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ._helpers import _decode_text_bytes, proposal_reply

logger = logging.getLogger(__name__)

# 扩展名 → 引擎路由(.py → crap4py;.cpp 系 → lizard),与 code_check 白名单一致
_PY_SUFFIXES = {".py"}
_CPP_SUFFIXES = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx", ".hh"}

# 文件大小上限(10MB,仿 code_format):防止 LLM 误喂巨型文件
_MAX_FILE_SIZE = 10 * 1024 * 1024

# functions 列表截断上限(summary 保持全量,防 LLM 上下文爆炸)
_MAX_FUNCTIONS = 50

# risk 分档阈值(业界通行:30 警告线,60 高危线)
_RISK_MODERATE = 30.0
_RISK_HIGH = 60.0

_DEFAULT_MAX_CRAP = 30.0


def _get_crap4py() -> dict | None:
    """延迟导入 crap4py 积木;缺失返回 None(优雅降级为安装提示)。

    crap4py __init__ 为空,从子模块导入其稳定纯函数层。
    """
    try:
        from crap4py._crap import crap_score
        from crap4py.complexity import cyclomatic_complexity
        from crap4py.coverage import NA, parse_lcov, resolve_coverage
        from crap4py.discovery import discover_functions
    except ImportError:
        return None
    return {
        "crap_score": crap_score,
        "cyclomatic_complexity": cyclomatic_complexity,
        "NA": NA,
        "parse_lcov": parse_lcov,
        "resolve_coverage": resolve_coverage,
        "discover_functions": discover_functions,
    }


def _get_crap_score():
    """C/C++ 路径只取公式函数(单一真相源);crap4py 缺失返回 None。"""
    try:
        from crap4py._crap import crap_score
    except ImportError:
        return None
    return crap_score


# ── 公开入口 ─────────────────────────────────────────


def check(
    filepath: str,
    lcov: str | None = None,
    *,
    max_crap: float = _DEFAULT_MAX_CRAP,
) -> dict:
    """对单个源文件运行 CRAP 检测。

    Args:
        filepath: 源文件绝对路径。扩展名决定引擎。
        lcov: 可选,LCOV 覆盖率文件绝对路径(coverage.py --cov-report=lcov /
            gcovr --lcov / lcov 均可产出)。缺省时按覆盖率 0% 计最坏情况值。
        max_crap: 告警阈值,仅影响 risk 分档与 proposal,不拦截。

    Returns:
        ok=True  → 统一 schema dict(file/language/engine/coverage_source/
                   threshold/functions/summary/proposal[,lcov_warning])
        ok=False → {"ok": False, "error": "..."}(可能含 proposal/options)
    """
    p = Path(filepath)
    if not p.exists():
        return {"ok": False, "error": f"文件不存在: {filepath}"}
    if not p.is_file():
        return {"ok": False, "error": f"不是普通文件: {filepath}"}
    try:
        size = p.stat().st_size
    except OSError as e:
        return {"ok": False, "error": f"无法 stat 文件: {e}"}
    if size > _MAX_FILE_SIZE:
        return {
            "ok": False,
            "error": (
                f"文件过大 ({size} > {_MAX_FILE_SIZE} 字节),"
                f"code_crap 拒绝处理 >10MB 的文件"
            ),
        }
    try:
        max_crap = float(max_crap)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"max_crap 必须是数字,收到: {max_crap!r}"}
    if max_crap <= 0:
        return {"ok": False, "error": f"max_crap 必须为正数,收到: {max_crap}"}

    suffix = p.suffix.lower()
    if suffix in _PY_SUFFIXES:
        return _analyze_python(p, lcov, max_crap)
    if suffix in _CPP_SUFFIXES:
        return _analyze_cpp(p, lcov, max_crap)
    return {
        "ok": False,
        "error": f"不支持的扩展名: {p.suffix}。code_crap 仅支持 Python 和 C/C++。",
        "supported_extensions": sorted(_PY_SUFFIXES | _CPP_SUFFIXES),
    }


# ── 行构建与汇总(两路径共用,保证 schema 同构)──────────


def _risk(crap: float) -> str:
    if crap > _RISK_HIGH:
        return "high"
    if crap >= _RISK_MODERATE:
        return "moderate"
    return "ok"


def _suggestion(cc: int, coverage: float | None) -> str:
    """确定性改进建议(供 LLM 直接行动)。coverage=None 表示无覆盖率数据。"""
    if coverage is None:
        base = "无覆盖率数据:当前为覆盖率 0% 的最坏情况分值,补测试后可大幅降低"
        if cc >= 10:
            return base + ";圈复杂度偏高,建议先拆分函数再补测试"
        return base
    if cc >= 10 and coverage < 0.5:
        return "圈复杂度偏高且覆盖不足:先拆分函数降低复杂度,再优先补主路径测试"
    if cc >= 10:
        return "圈复杂度偏高:考虑拆分函数或简化分支结构"
    if coverage < 0.5:
        return "覆盖率不足:优先补测试"
    return ""


def _build_row(
    name: str,
    line_start: int,
    line_end: int,
    cc: int,
    coverage: float | None,
    crap: float,
) -> dict:
    return {
        "name": name,
        "line_start": int(line_start),
        "line_end": int(line_end),
        "cc": int(cc),
        "coverage": round(float(coverage), 4) if coverage is not None else None,
        "crap": round(float(crap), 1),
        "risk": _risk(float(crap)),
        "suggestion": _suggestion(int(cc), coverage),
    }


def _finalize(
    p: Path,
    language: str,
    engine: str,
    coverage_source: str,
    lcov_warning: str | None,
    rows: list[dict],
    max_crap: float,
) -> dict:
    """按 crap 降序汇总为统一 schema;summary 始终全量,functions 截断到前 50。"""
    rows = sorted(rows, key=lambda r: (-r["crap"], r["name"]))
    total = len(rows)
    craps = [r["crap"] for r in rows]
    over = sum(1 for c in craps if c > max_crap)
    result: dict = {
        "ok": True,
        "file": str(p),
        "language": language,
        "engine": engine,
        "coverage_source": coverage_source,
        "threshold": max_crap,
        "functions": rows[:_MAX_FUNCTIONS],
        "summary": {
            "total": total,
            "max_crap": max(craps) if craps else 0.0,
            "avg_crap": round(sum(craps) / total, 1) if total else 0.0,
            "over_threshold": over,
        },
    }
    if lcov_warning:
        result["lcov_warning"] = lcov_warning
    if total == 0:
        result["proposal"] = "未发现函数"
    elif total > _MAX_FUNCTIONS:
        result["functions_truncated"] = True
        result["proposal"] = (
            f"共 {total} 个函数,仅展示 CRAP 最差的前 {_MAX_FUNCTIONS} 个;"
            f"{over}/{total} 超过阈值 {max_crap:g}"
        )
    elif over:
        worst = rows[0]
        result["proposal"] = (
            f"{over}/{total} 个函数 CRAP 超过阈值 {max_crap:g}"
            f"(最差 {worst['name']}={worst['crap']})"
        )
    else:
        result["proposal"] = f"全部 {total} 个函数 CRAP 低于阈值 {max_crap:g}"
    return result


# ── LCOV 加载(两路径共用)─────────────────────────────


def _load_lcov_text(lcov_path: str | None) -> tuple[str | None, str | None]:
    """读取 lcov 文本。返回 (text, warning);降级不抛错,由 warning 透出。"""
    if not lcov_path:
        return None, None
    lp = Path(lcov_path)
    if not lp.is_file():
        return None, f"lcov 文件不存在: {lcov_path}(已降级为无覆盖率模式)"
    try:
        text, _enc = _decode_text_bytes(lp.read_bytes())
    except OSError as e:
        return None, f"lcov 读取失败: {e}(已降级为无覆盖率模式)"
    if not text.strip():
        return None, f"lcov 文件为空: {lcov_path}(已降级为无覆盖率模式)"
    return text, None


# ── Python 路径:crap4py ──────────────────────────────


def _analyze_python(p: Path, lcov_path: str | None, max_crap: float) -> dict:
    """Python:crap4py 积木组合(discover → CC → 分支覆盖 → crap_score)。"""
    c4 = _get_crap4py()
    if c4 is None:
        return proposal_reply(
            False,
            "crap4py 未安装,无法分析 Python 文件的 CRAP。请运行: pip install crap4py",
            error="crap4py 未安装",
            evidence={"python_file": str(p)},
            options=["pip install crap4py", "切换到 .c/.cpp 文件(lizard 路径)"],
        )

    source = p.read_text(encoding="utf-8", errors="replace")
    try:
        cc_results = c4["cyclomatic_complexity"](source)
    except SyntaxError as e:
        return {"ok": False, "error": f"Python 解析失败: {e}"}
    cc_map = {r.name: r.cc for r in cc_results}

    # WHY collect_files 注入:crap4py 默认跳过 test_*.py/*_test.py 且受
    # gitignore 影响;本工具被 LLM 显式点名文件时应始终分析,故绕过其收集器。
    entries = c4["discover_functions"](
        [str(p)], root=str(p.parent), collect_files=lambda paths: [str(p)]
    )

    lcov_text, lcov_warning = _load_lcov_text(lcov_path)
    lcov_data = c4["parse_lcov"](lcov_text) if lcov_text is not None else None
    coverage_source = "lcov" if lcov_data is not None else "none"

    rows: list[dict] = []
    for entry in entries:
        bare = entry.qualified_name.rsplit(".", 1)[-1]
        cc = int(cc_map.get(bare, 1))  # bare name 匹配,与上游 _score_entry 一致
        coverage = None
        if lcov_data is not None:
            cov = c4["resolve_coverage"](
                entry.module_label, entry.line_range, lcov_data
            )
            if cov is not c4["NA"]:  # NA = 源文件无 SF 记录
                coverage = float(cov)
        base_cov = coverage if coverage is not None else 0.0
        rows.append(
            _build_row(
                entry.qualified_name,
                entry.line_range[0],
                entry.line_range[1],
                cc,
                coverage,
                float(c4["crap_score"](cc, base_cov)),
            )
        )
    return _finalize(
        p, "python", "crap4py", coverage_source, lcov_warning, rows, max_crap
    )


# ── C/C++ 路径:lizard(占位,Task 2 实现)──────────────


def _analyze_cpp(p: Path, lcov_path: str | None, max_crap: float) -> dict:
    raise NotImplementedError("Task 2 实现")


# ── 调试入口 ─────────────────────────────────────────


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    if len(_sys.argv) < 2:
        print("Usage: python -m tools.code_crap <filepath> [lcov_path] [--max-crap=N]")
        _sys.exit(2)
    _fp = _sys.argv[1]
    _lcov = _sys.argv[2] if len(_sys.argv) > 2 and not _sys.argv[2].startswith("--") else None
    _mc = _DEFAULT_MAX_CRAP
    for _a in _sys.argv[2:]:
        if _a.startswith("--max-crap="):
            _mc = float(_a.split("=", 1)[1])
    print(json.dumps(check(_fp, _lcov, max_crap=_mc), ensure_ascii=False, indent=2))
