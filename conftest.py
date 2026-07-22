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


class _StubModule(types.ModuleType):
    """万能 stub 模块:任意属性访问返回 stub type。

    用于 standalone pytest 下拦截 ``from astrbot.xxx import YYY``,
    确保 YYY 总是可导入的(作为 stub type)。
    """

    def __getattr__(self, name: str) -> type:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        # 返回一个通用 stub type(同名)
        return type(name, (), {})


def _stub_missing_runtime_modules() -> None:
    """注入 v2.17.0 standalone 测试需要的最小 stub。

    v2.21 (2026-07-18, elecvoid243) 修复: **优先使用真实 astrbot 包**。
    旧实现无条件用 meta_path finder 拦截所有 ``astrbot.*`` 导入并替换为
    万能 stub。但 stub 的 ``filter.command_group`` 返回普通 function,
    没有 ``.command()`` 方法,导致 main.py 类体中 ``@project.command(...)``
    等子指令注册抛 ``AttributeError: 'function' object has no attribute
    'command'``,7 个测试文件收集失败;同时 ``isinstance(x,
    RegisteringCommandable)`` 等断言在 stub 环境下也不可能成立。

    现在: 真实 astrbot 可导入时(开发机/CI 装好 AstrBot 后),完全跳过
    astrbot stub;仅在 astrbot 缺失(极简 CI)时才启用 stub 兜底。
    python_ripgrep / send2trash / get_astrbot_workspaces_path 的
    fallback 保持按需(各自 ImportError 时才注入),不受此开关影响。
    """
    import logging as _logging

    # ── 真实 astrbot 探测:可用则跳过全部 astrbot.* stub ──
    # WHY: 测试(如 test_project_cmd.py)断言
    # ``isinstance(SPCodeToolkit.project, RegisteringCommandable)``,
    # 只有真实 ``filter.command_group`` 装饰结果才能满足。
    try:
        import astrbot as _real_astrbot  # noqa: F401
        import astrbot.api  # noqa: F401
        import astrbot.api.event  # noqa: F401
        from astrbot.core.star.register.star_handler import (  # noqa: F401
            RegisteringCommandable as _RealRC,
        )

        _astrbot_real_available = True
    except ImportError:
        _astrbot_real_available = False

    if not _astrbot_real_available:
        # ── 通用 astrbot.* stub 工厂 ──
        # WHY: 项目内 tools/* 模块在 import 时会触发大量 astrbot.* 导入链
        # (astrbot.api.event / astrbot.core.agent.run_context / ...)。
        # 逐一创建 stub 维护成本高,改用 meta_path finder 拦截所有
        # ``astrbot.*`` 导入并自动创建空模块 stub。关键模块的特定属性
        # (如 logger / AstrMessageEvent) 在下面手动注入。
        if "astrbot" not in sys.modules:
            _ast = types.ModuleType("astrbot")
            _ast.__path__ = []  # type: ignore[attr-defined]
            _ast.logger = _logging.getLogger("astrbot")
            sys.modules["astrbot"] = _ast
        else:
            # 若已被前面的代码注册,补 logger
            _ap_ast = sys.modules["astrbot"]
            if not hasattr(_ap_ast, "__path__"):
                _ap_ast.__path__ = []  # type: ignore[attr-defined]
            if not hasattr(_ap_ast, "logger"):
                _ap_ast.logger = _logging.getLogger("astrbot")  # type: ignore[attr-defined]

        class _StubLoader:
            """空 loader:不做任何事,模块已在 sys.modules 中。"""

            def create_module(self, spec):
                return sys.modules.get(spec.name)

            def exec_module(self, module):
                pass

        class _AstrBotStubFinder:
            """meta_path finder:拦截所有 ``astrbot.*`` 导入并创建万能 stub 模块。

            每个 stub 模块的任意属性访问都返回一个 stub type,
            确保 ``from astrbot.core.agent.tool import ToolExecResult``
            这样的语句不会因为属性缺失而失败。
            """

            @staticmethod
            def find_spec(fullname, path, target=None):
                if not fullname.startswith("astrbot."):
                    return None
                if fullname in sys.modules:
                    # 确保已存在的 stub 也是 package
                    existing = sys.modules[fullname]
                    if not hasattr(existing, "__path__"):
                        existing.__path__ = []  # type: ignore[attr-defined]
                    return None
                # 万能 stub
                stub = _StubModule(fullname)
                stub.__path__ = []  # type: ignore[attr-defined]
                parts = fullname.split(".")
                if len(parts) >= 2:
                    parent = ".".join(parts[:-1])
                    if parent not in sys.modules:
                        _AstrBotStubFinder._ensure_parent(parent)
                sys.modules[fullname] = stub
                return importlib.util.spec_from_loader(
                    fullname,
                    loader=_StubLoader(),
                    origin="stub",
                )

            @staticmethod
            def _ensure_parent(parent_name: str) -> None:
                if parent_name in sys.modules:
                    pkg = sys.modules[parent_name]
                    # 确保是 package(有 __path__)
                    if not hasattr(pkg, "__path__"):
                        pkg.__path__ = []  # type: ignore[attr-defined]
                    return
                parts = parent_name.split(".")
                if len(parts) >= 2:
                    _AstrBotStubFinder._ensure_parent(".".join(parts[:-1]))
                pkg = _StubModule(parent_name)
                pkg.__path__ = []  # type: ignore[attr-defined]
                sys.modules[parent_name] = pkg

        sys.meta_path.insert(0, _AstrBotStubFinder)

        # ── 关键 stub — 必须带特定属性(main.py import 语句直接引用这些名字) ──
        # astrbot.api
        _api = types.ModuleType("astrbot.api")
        _api.__path__ = []  # type: ignore[attr-defined]
        _api.logger = _logging.getLogger("astrbot.api")

        class _StubStar:
            """``star`` stub:提供 Star 基类 + filter 命名空间。"""

            Star = type("Star", (), {})
            filter = type("filter", (), {})

        _api.star = _StubStar()
        _api.FunctionTool = type("FunctionTool", (), {})
        sys.modules["astrbot.api"] = _api
        # astrbot.api.event
        _api_event = types.ModuleType("astrbot.api.event")
        _api_event.AstrMessageEvent = type("AstrMessageEvent", (), {})

        class _StubFilter:
            """``filter`` stub:接受任意装饰器调用(如 command_group)。"""

            @staticmethod
            def event_message_type(_cls: type) -> type:
                return _cls

            @staticmethod
            def command_group(*args, **kwargs):
                """``@filter.command_group(...)`` stub:返回带 ``.command()`` 的对象。

                模拟真实 AstrBot ``RegisteringCommandable`` 的最小接口:
                main.py 类体中 ``@project.command("load")`` 等子指令注册
                要求 command_group 装饰结果暴露 ``.command(name)`` 装饰器。
                """

                class _StubCommandGroup:
                    def __init__(self, fn):
                        self._fn = fn

                    def command(self, *c_args, **c_kwargs):
                        def decorator(fn):
                            return fn

                        return decorator

                    def __call__(self, *c_args, **c_kwargs):
                        if self._fn is not None:
                            return self._fn(*c_args, **c_kwargs)
                        return None

                def decorator(fn):
                    return _StubCommandGroup(fn)

                return decorator

            def __getattr__(self, name):
                """任意属性访问返回空装饰器。"""

                def decorator(*args, **kwargs):
                    def inner(fn):
                        return fn

                    return inner

                return decorator

        _api_event.filter = _StubFilter()
        sys.modules["astrbot.api.event"] = _api_event
        # astrbot.api.provider
        _api_provider = types.ModuleType("astrbot.api.provider")
        _api_provider.ProviderRequest = type("ProviderRequest", (), {})
        sys.modules["astrbot.api.provider"] = _api_provider
        # astrbot.api.star
        _api_star = types.ModuleType("astrbot.api.star")

        def _stub_register(*args, **kwargs):
            """``@register(...)`` decorator stub:返回一个空装饰器。"""

            def decorator(cls):
                return cls

            return decorator

        _api_star.StarTools = type("StarTools", (), {})
        _api_star.register = _stub_register
        sys.modules["astrbot.api.star"] = _api_star
        # astrbot.api.web
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
            # 若 astrbot 已被前面的 api stub 创建,保留它(不覆盖 logger 等属性)
            if "astrbot" not in sys.modules:
                _ap_astrbot = types.ModuleType("astrbot")
                _ap_astrbot.__path__ = []  # type: ignore[attr-defined]
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
                    if name in {
                        "__path__",
                        "__package__",
                        "__loader__",
                        "__spec__",
                        "__file__",
                        "__cached__",
                        "_loaded",
                        "__builtins__",
                        "__doc__",
                        "__name__",
                    }:
                        raise AttributeError(name)
                    if not self._loaded:
                        # 第一次真实属性访问 → exec_module,执行 main.py
                        self._loaded = True  # 先设 flag 防止 exec 内部递归
                        try:
                            _spec.loader.exec_module(self)
                        except Exception:
                            self._loaded = False  # 失败时重置,允许重试
                            raise
                    # 用 super().__getattribute__ 避免 __getattr__ 递归
                    return super().__getattribute__(name)

            sys.modules[_pkg_name + ".main"] = _LazyMainLoader()
