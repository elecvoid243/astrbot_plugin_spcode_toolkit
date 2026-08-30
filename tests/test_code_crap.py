"""tools/code_crap 单元测试。

Spec: docs/superpowers/specs/2026-08-30-code-crap-tool-design.md
覆盖:
  1:  文件不存在
  2:  不支持扩展名
  3:  文件过大
  4:  Python 无覆盖 → 最坏情况分值 + 统一 schema
  5:  Python risk 分档(moderate/high)
  6:  Python 语法错误
  7:  Python 无函数文件
  8:  test_*.py 显式点名也分析(collect_files 注入绕过)
  9:  crap4py 缺失 → proposal_reply
  10: Python + lcov → 分支覆盖合并
  11: lcov 文件缺失 → 降级 + lcov_warning
  12: lcov 无匹配 SF → NA → 最坏情况
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import code_crap  # noqa: E402

# def simple: lines 1-4, cc=2(1 个 if);def branchy: lines 6-12, cc=4(if+if+elif)
_PY_SRC = (
    "def simple(x):\n"
    "    if x > 0:\n"
    "        return 1\n"
    "    return 0\n"
    "\n"
    "def branchy(x):\n"
    "    if x > 0:\n"
    "        if x > 10:\n"
    "            return 2\n"
    "        elif x > 5:\n"
    "            return 1\n"
    "    return 0\n"
)

# 最坏情况(coverage=0):simple=4+2=6,branchy=16+4=20
_PY_LCOV = (
    "SF:src_sample.py\n"
    "BRDA:2,0,0,1\n"
    "BRDA:2,0,1,1\n"
    "BRDA:7,0,0,1\n"
    "BRDA:8,0,0,0\n"
    "BRDA:10,0,0,1\n"
    "end_of_record\n"
)


def _write_py(tmp_path: Path, name: str = "src_sample.py", text: str = _PY_SRC) -> Path:
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    return f


def test_missing_file(tmp_path: Path):
    result = code_crap.check(str(tmp_path / "ghost.py"))
    assert result["ok"] is False
    assert "文件不存在" in result["error"]


def test_unsupported_extension(tmp_path: Path):
    f = tmp_path / "note.txt"
    f.write_text("hello", encoding="utf-8")
    result = code_crap.check(str(f))
    assert result["ok"] is False
    assert "不支持的扩展名" in result["error"]
    assert ".py" in result["supported_extensions"]
    assert ".cpp" in result["supported_extensions"]


def test_file_too_large(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(code_crap, "_MAX_FILE_SIZE", 10)
    f = _write_py(tmp_path)
    result = code_crap.check(str(f))
    assert result["ok"] is False
    assert "文件过大" in result["error"]


def test_python_no_coverage_worst_case(tmp_path: Path):
    f = _write_py(tmp_path)
    result = code_crap.check(str(f))
    assert result["ok"] is True
    assert result["language"] == "python"
    assert result["engine"] == "crap4py"
    assert result["coverage_source"] == "none"
    assert result["threshold"] == 30
    rows = result["functions"]
    assert [r["name"] for r in rows] == ["branchy", "simple"]  # crap 降序
    by_name = {r["name"]: r for r in rows}
    assert by_name["simple"]["cc"] == 2
    assert by_name["simple"]["crap"] == 6.0
    assert by_name["simple"]["line_start"] == 1
    assert by_name["simple"]["line_end"] == 4
    assert by_name["simple"]["coverage"] is None
    assert by_name["simple"]["risk"] == "ok"
    assert "最坏情况" in by_name["simple"]["suggestion"]
    assert by_name["branchy"]["cc"] == 4
    assert by_name["branchy"]["crap"] == 20.0
    assert by_name["branchy"]["line_start"] == 6
    assert by_name["branchy"]["line_end"] == 12
    assert result["summary"] == {
        "total": 2,
        "max_crap": 20.0,
        "avg_crap": 13.0,
        "over_threshold": 0,
    }
    assert "全部 2 个函数" in result["proposal"]


def test_python_risk_tiers_and_threshold(tmp_path: Path):
    src = (
        "def tier_moderate(x):\n"  # 6 个 if → cc=7 → 最坏 56 → moderate
        "    if x == 1:\n        return 1\n"
        "    if x == 2:\n        return 2\n"
        "    if x == 3:\n        return 3\n"
        "    if x == 4:\n        return 4\n"
        "    if x == 5:\n        return 5\n"
        "    if x == 6:\n        return 6\n"
        "    return 0\n"
        "\n"
        "def tier_high(x):\n"  # 8 个 if → cc=9 → 最坏 90 → high
        "    if x == 1:\n        return 1\n"
        "    if x == 2:\n        return 2\n"
        "    if x == 3:\n        return 3\n"
        "    if x == 4:\n        return 4\n"
        "    if x == 5:\n        return 5\n"
        "    if x == 6:\n        return 6\n"
        "    if x == 7:\n        return 7\n"
        "    if x == 8:\n        return 8\n"
        "    return 0\n"
    )
    f = _write_py(tmp_path, text=src)
    result = code_crap.check(str(f), max_crap=30)
    by_name = {r["name"]: r for r in result["functions"]}
    assert by_name["tier_moderate"]["crap"] == 56.0
    assert by_name["tier_moderate"]["risk"] == "moderate"
    assert by_name["tier_high"]["crap"] == 90.0
    assert by_name["tier_high"]["risk"] == "high"
    assert result["summary"]["over_threshold"] == 2
    assert "2/2 个函数" in result["proposal"]
    assert "最差 tier_high=90.0" in result["proposal"]


def test_python_syntax_error(tmp_path: Path):
    f = tmp_path / "broken.py"
    f.write_text("def broken(:\n", encoding="utf-8")
    result = code_crap.check(str(f))
    assert result["ok"] is False
    assert "解析失败" in result["error"]


def test_python_no_functions(tmp_path: Path):
    f = tmp_path / "empty.py"
    f.write_text("x = 1\n", encoding="utf-8")
    result = code_crap.check(str(f))
    assert result["ok"] is True
    assert result["functions"] == []
    assert result["summary"]["total"] == 0
    assert result["proposal"] == "未发现函数"


def test_python_test_file_still_analyzed(tmp_path: Path):
    f = _write_py(tmp_path, name="test_sample.py")
    result = code_crap.check(str(f))
    assert result["ok"] is True
    assert result["summary"]["total"] == 2


def test_crap4py_missing(tmp_path: Path, monkeypatch):
    for mod in (
        "crap4py",
        "crap4py._crap",
        "crap4py.complexity",
        "crap4py.coverage",
        "crap4py.discovery",
    ):
        monkeypatch.setitem(sys.modules, mod, None)
    f = _write_py(tmp_path)
    result = code_crap.check(str(f))
    assert result["ok"] is False
    assert "crap4py 未安装" in result["error"]
    assert "pip install crap4py" in result["proposal"]


def test_python_lcov_branch_coverage(tmp_path: Path):
    f = _write_py(tmp_path)
    lcov = tmp_path / "lcov.info"
    lcov.write_text(_PY_LCOV, encoding="utf-8")
    result = code_crap.check(str(f), str(lcov))
    assert result["ok"] is True
    assert result["coverage_source"] == "lcov"
    by_name = {r["name"]: r for r in result["functions"]}
    # simple(range 1-4):BRDA line2 两条全 taken → cov 1.0 → crap = cc = 2.0
    assert by_name["simple"]["coverage"] == 1.0
    assert by_name["simple"]["crap"] == 2.0
    # branchy(range 6-12):BRDA line 7,8,10 → taken 1,0,1 → cov 2/3
    # crap = 16*(1/3)^3 + 4 = 4.5926 → 4.6
    assert by_name["branchy"]["coverage"] == pytest.approx(0.6667, abs=1e-3)
    assert by_name["branchy"]["crap"] == pytest.approx(4.6, abs=0.05)
    assert "lcov_warning" not in result


def test_python_lcov_missing_degrades(tmp_path: Path):
    f = _write_py(tmp_path)
    result = code_crap.check(str(f), "Z:/no/such/lcov.info")
    assert result["ok"] is True
    assert result["coverage_source"] == "none"
    assert "不存在" in result["lcov_warning"]
    by_name = {r["name"]: r for r in result["functions"]}
    assert by_name["branchy"]["crap"] == 20.0  # 降级为最坏情况


def test_python_lcov_no_matching_sf(tmp_path: Path):
    f = _write_py(tmp_path)
    lcov = tmp_path / "lcov.info"
    lcov.write_text(
        "SF:other_file.py\nBRDA:2,0,0,1\nend_of_record\n", encoding="utf-8"
    )
    result = code_crap.check(str(f), str(lcov))
    assert result["ok"] is True
    assert result["coverage_source"] == "lcov"  # 文件加载成功,但无匹配记录
    by_name = {r["name"]: r for r in result["functions"]}
    # resolve_coverage 返回 NA → coverage=None → 最坏情况
    assert by_name["simple"]["coverage"] is None
    assert by_name["simple"]["crap"] == 6.0
