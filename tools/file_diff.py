"""
file_diff — 文件差异比较。
纯 difflib 标准库，逐行比较两个文件，返回结构化 added/removed + unified diff。
"""

from __future__ import annotations

import difflib
from pathlib import Path

# 文件过大直接拒绝（走外部 diff 工具）
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def _read(path: Path) -> str:
    """UTF-8 → GBK fallback 读取（兼容 Windows 中文编码）。"""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gbk")


def compare(file_a: str, file_b: str) -> dict:
    """比较两个文件，返回结构化差异。"""
    pa, pb = Path(file_a), Path(file_b)

    if not pa.exists():
        return {"ok": False, "error": f"文件不存在: {file_a}"}
    if not pb.exists():
        return {"ok": False, "error": f"文件不存在: {file_b}"}

    if pa.stat().st_size > _MAX_FILE_SIZE or pb.stat().st_size > _MAX_FILE_SIZE:
        return {
            "ok": False,
            "error": "文件超过 50MB 上限，请使用外部 diff 工具",
        }

    try:
        a_text = _read(pa)
    except Exception as e:
        return {"ok": False, "error": f"无法读取 {file_a}: {e}"}
    try:
        b_text = _read(pb)
    except Exception as e:
        return {"ok": False, "error": f"无法读取 {file_b}: {e}"}

    lines_a = a_text.splitlines()
    lines_b = b_text.splitlines()

    diff = list(
        difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile=file_a,
            tofile=file_b,
            lineterm="",
        )
    )

    full_count = len(diff)
    displayed = diff[:100]
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))

    # 人类可读的文本格式，对齐 astrbot_file_edit_tool 的 _format_result
    # 模式：方便前端用 ` ```diff ``` ` 正则提取并复用现有 DiffPreview 组件
    if full_count == 0:
        text = f"Compared {file_a} and {file_b}.\nFiles are identical."
    else:
        text = (
            f"Compared {file_a} and {file_b}.\n"
            f"{added} addition(s), {removed} removal(s).\n"
            f"\n"
            f"Diff:\n"
            f"```diff\n"
            f"{chr(10).join(displayed)}\n"
            f"```"
        )

    return {
        "ok": True,
        "file_a": file_a,
        "file_b": file_b,
        "added": added,
        "removed": removed,
        "total_changes": added + removed,
        "diff": "\n".join(displayed),
        "diff_lines_shown": len(displayed),
        "diff_lines_total": full_count,
        "truncated": full_count > 100,
        "identical": full_count == 0,
        "text": text,  # 对齐 astrbot_file_edit_tool 格式，前端可复用 DiffPreview
    }
