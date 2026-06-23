"""_TodoToolBase — 4 个 todo_* 工具的公共基类。

封装 umo 提取、TodoStore 初始化、async dispatch。
子类只需定义自己的 parameters / call()，其余样板代码继承自此基类。

所有 call() 方法返回 ToolExecResult (JSON 字符串):
- 成功路径经 _dispatch → unwrap() → JSON 字符串
- 失败路径经 _err() 直接生成 JSON 字符串
"""
from __future__ import annotations

import json as _json
import os

from astrbot.api import FunctionTool
from astrbot.api.star import StarTools

from .._helpers import err_json, run_sync, unwrap
from .._stats import _record
from .. import todo_list as _todo_list_mod


class _TodoToolBase(FunctionTool):
    def _err(self, error: str, proposal: str = "") -> str:
        """Build a JSON error response string with optional proposal.

        永远返回 JSON 字符串(与 unwrap() 风格一致),保证 call() 协议统一。
        """
        payload: dict = {"ok": False, "error": error}
        if proposal:
            payload["proposal"] = proposal
        return _json.dumps(payload, ensure_ascii=False)

    def _setup(self, context) -> tuple | dict:
        """提取 umo,创建 store,返回 (store, umo) 元组。

        失败时返回 dict 错误响应(供 _dispatch 透传给 unwrap 包成 JSON 字符串)。

        v2.11: 隔离键从 sender_key (platform:sender_id) 切到 umo (unified_msg_origin)。
        """
        try:
            event = context.context.event
        except AttributeError:
            return {"ok": False, "error": "无 event 上下文"}
        umo = _todo_list_mod.extract_umo(event)
        data_dir = str(StarTools.get_data_dir())
        todos_dir = os.path.join(data_dir, "todos")
        store = _todo_list_mod.TodoStore(todos_dir)
        return store, umo

    async def _dispatch(self, context, fn, *args, **kwargs) -> str:
        """通用 dispatch: 记录调用 + setup + 异步执行 fn(store, umo, *args, **kwargs)。

        返回: 永远为 JSON 字符串(与 unwrap() 风格一致)。
        - setup 失败 → 直接 unwrap(setup_dict) 透传 proposal 字段
        - 业务异常 → err_json 包装
        """
        _record(self.name)
        try:
            setup = self._setup(context)
            if isinstance(setup, dict):  # 错误响应,经 unwrap 包成 JSON 字符串
                return unwrap(setup)
            store, umo = setup
            result = await run_sync(lambda: fn(store, umo, *args, **kwargs))
            return unwrap(result)
        except Exception as e:
            return err_json(f"{self.name} 失败: {e}")
