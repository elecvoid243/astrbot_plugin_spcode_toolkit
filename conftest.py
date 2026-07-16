"""Worktree 根级 conftest: 修正 sys.path + lazy main.py 加载 + 运行时 stub。

WHY:
    现有 test_*.py 的 import 逻辑假设目录结构是:
        <project_root>/astrbot_plugin_spcode_toolkit/main.py
    (即包嵌在项目根下, main.py 在包内)
    通过 ``parent.parent.parent`` 找到 ``<project_root>`` 的父目录,再 import。

    但 git worktree 实际目录结构是:
        <worktree>/main.py  ← main.py 在 worktree 根,不在子包内
        <worktree>/tests/test_*.py
    ``parent.parent.parent`` 在 worktree 中解析成 ``<worktrees>/``,而那里
    有一个"ghost worktree"  ``<worktrees>/astrbot_plugin_spcode_toolkit/``,
    会抢先匹配,把测试引到错误的 main.py。

    修复: 把 worktree 根(``parent.parent``)加到 sys.path, 然后强制把
    ``astrbot_plugin_spcode_toolkit/main.py`` 重新解析为 worktree 自己的
    main.py(通过 importlib.util.spec_from_file_location)。

    v2.17.0 fix:
      - 改为 **lazy main.py 加载**:只在首次 ``import astrbot_plugin_spcode_toolkit.main``
        时才 exec_module,而不是 conftest 收集阶段 eager load。
        Eager load 会触发整条 main.py → tools.webapi → tools.function_tools
        → tools.inta_shell 导入链,这条链在 standalone pytest 下会撞上多个
        AstrBot 运行时注入点(``astrbot.core.utils.astrbot_path`` 等)。
      - **运行时 stub 注入**:为 standalone 测试所需的 4 个模块注入最小 stub
        (astrbot.api.web / python_ripgrep / send2trash /
         astrbot.core.utils.astrbot_path.get_astrbot_workspaces_path)。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_WORKTREE_ROOT = Path(__file__).resolve().parent
_MAIN_PY = _WORKTREE_ROOT / "main.py"
sys.path.insert(0, str(_WORKTREE_ROOT))


class _StubJSONResponse:
    """Standalone pytest stub for ``astrbot.api.web.JSONResponse``.

    Why: 项目内的 ``_JSONResponseCompat(JSONResponse)`` 用
    ``(content, status_code=200, headers=None)`` 调用 super().__init__,
    而 quart 的 Response 不接受 ``status_code`` kwarg。
    此 stub 仅保存 ``_content`` / ``status_code`` / ``headers``,
    dict-like ``__getitem__`` / ``get`` 由 ``_JSONResponseCompat`` 子类注入。
    """

    def __init__(
        self,
        content: object,
        status_code: int = 200,
        headers: object = None,
    ) -> None:
        self._content = content
        self.status_code = status_code
        self.headers = headers


def _stub_missing_runtime_modules() -> None:
    """注入 v2.17.0 standalone 测试需要的最小 stub。"""
    # ── astrbot.api.web ──
    try:
        import astrbot.api.web  # noqa: F401  (probe)
    except ImportError:
        _astbot_web = types.ModuleType("astrbot.api.web")
        _astbot_web.JSONResponse = _StubJSONResponse
        _astbot_web.request = None
        sys.modules["astrbot.api.web"] = _astbot_web

    # ── python_ripgrep ──
    try:
        import python_ripgrep  # noqa: F401
    except ImportError:
        _rg = types.ModuleType("python_ripgrep")

        def _not_implemented(*args, **kwargs):
            raise NotImplementedError(
                "python_ripgrep stub (conftest.py v2.17.0); real impl not installed"
            )

        _rg.files = _not_implemented
        _rg.search = _not_implemented
        _rg.files_with_matches = _not_implemented
        sys.modules["python_ripgrep"] = _rg

    # ── send2trash ──
    try:
        import send2trash  # noqa: F401
    except ImportError:
        _s2t = types.ModuleType("send2trash")

        def _send2trash_stub(path):  # pragma: no cover
            import os
            os.remove(path)

        _s2t.send2trash = _send2trash_stub
        sys.modules["send2trash"] = _s2t

    # ── astrbot.core.utils.astrbot_path.get_astrbot_workspaces_path ──
    # WHY: v2.17.0 (2026-07-16) — tools/inta_shell/paths.py 在 import 时执行
    # ``from astrbot.core.utils.astrbot_path import get_astrbot_workspaces_path``,
    # 新版 AstrBot 已删除该函数(改为 ``get_astrbot_data_path()``),导致整条
    # main.py → tools.webapi → tools.function_tools → tools.inta_shell 导入链
    # 在 standalone pytest 下崩溃。注入最小 stub 返回项目内 data/workspaces。
    try:
        from astrbot.core.utils.astrbot_path import (  # noqa: F401  (probe)
            get_astrbot_workspaces_path,
        )
    except ImportError:
        try:
            import astrbot.core.utils.astrbot_path as _ap_module
        except ImportError:
            # astrbot 完全不存在,创建一个空模块壳
            _ap_module = types.ModuleType("astrbot.core.utils.astrbot_path")
            _ap_pkg = types.ModuleType("astrbot.core.utils")
            _ap_pkg.__path__ = []  # type: ignore[attr-defined]
            _ap_core = types.ModuleType("astrbot.core")
            _ap_core.__path__ = []  # type: ignore[attr-defined]
            _ap_astrbot = types.ModuleType("astrbot")
            sys.modules["astrbot"] = _ap_astrbot
            sys.modules["astrbot.core"] = _ap_core
            sys.modules["astrbot.core.utils"] = _ap_pkg
            sys.modules["astrbot.core.utils.astrbot_path"] = _ap_module

        def _get_astrbot_workspaces_path_stub() -> Path:
            """Standalone pytest stub returning the in-repo data/workspaces."""
            return _WORKTREE_ROOT / "data" / "workspaces"

        _ap_module.get_astrbot_workspaces_path = _get_astrbot_workspaces_path_stub


_pkg_name = "astrbot_plugin_spcode_toolkit"
if _pkg_name not in sys.modules:
    # ── 注册 package(但不加载 main.py) ──
    _pkg = types.ModuleType(_pkg_name)
    _pkg.__path__ = [str(_WORKTREE_ROOT)]
    sys.modules[_pkg_name] = _pkg

    # ── 为 main 子模块注册 lazy loader ──
    # WHY: 原始 conftest eager load main.py → 触发整条工具链 import,
    # standalone pytest 下撞 AstrBot 运行时注入点。改为 lazy:
    # 当 test 真正 ``import astrbot_plugin_spcode_toolkit.main`` 时,
    # 才执行 main.py,此时 test 已准备好所需 monkeypatch / fixture。
    if _MAIN_PY.exists():
        _spec = importlib.util.spec_from_file_location(
            _pkg_name + ".main", str(_MAIN_PY)
        )
        if _spec and _spec.loader:
            _stub_missing_runtime_modules()

            class _LazyMainLoader(types.ModuleType):
                """代表 ``astrbot_plugin_spcode_toolkit.main`` 的 lazy module。

                首次属性访问时执行 main.py,把内容挪到自身。
                重要:必须先把 ModuleType 基础属性塞进 __dict__,否则
                exec_module 内部访问 ``__file__`` / ``__spec__`` 时会再次
                触发本 ``__getattr__``,形成 RecursionError。
                """

                def __init__(self):
                    super().__init__(_pkg_name + ".main")
                    self.__spec__ = _spec
                    self.__loader__ = _spec.loader
                    self.__file__ = _spec.origin
                    self.__cached__ = getattr(_spec, "cached", None)
                    self._loaded = False

                def __getattr__(self, name):
                    # ModuleType 内部属性:不能让其触发 exec_module
                    if name in {"__path__", "__package__", "__loader__",
                                "__spec__", "__file__", "__cached__", "_loaded",
                                "__builtins__", "__doc__", "__name__"}:
                        raise AttributeError(name)
                    if not self._loaded:
                        # 第一次真实属性访问 → exec_module,执行 main.py
                        self._loaded = True
                        _spec.loader.exec_module(self)
                    return getattr(self, name)

            sys.modules[_pkg_name + ".main"] = _LazyMainLoader()