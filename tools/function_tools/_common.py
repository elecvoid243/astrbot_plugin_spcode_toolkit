"""function_tools 内部共享 helper — 统一 run_sync + record + err_json 模式。"""
from __future__ import annotations

from .._helpers import err_json, run_sync, unwrap
from .._stats import _record


async def record_and_run(
    name: str, fn, *args, err_prefix: str = "", **kwargs
):
    """统一模板: _record → run_sync → unwrap,异常时 err_json 包装。

    适用于 code_check / es_search / file_remove / file_compare 这 4 个
    "调一次同步函数,返回 dict" 模式的工具。
    """
    _record(name)
    try:
        result = await run_sync(fn, *args, **kwargs)
        return unwrap(result)
    except Exception as e:
        prefix = f"{err_prefix} 失败: " if err_prefix else "失败: "
        return err_json(f"{prefix}{e}")
