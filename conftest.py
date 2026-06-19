"""Worktree 根级 conftest: 修正 sys.path,确保从 worktree 加载 main.py。

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

    这只影响 worktree 中 pytest 收集时;原仓库结构下, ``parent.parent``
    仍然指向项目根, ``main.py`` 同样在此处 — 所以两个环境都正常。
"""

import importlib.util
import sys
from pathlib import Path


_WORKTREE_ROOT = Path(__file__).resolve().parent
_MAIN_PY = _WORKTREE_ROOT / "main.py"
sys.path.insert(0, str(_WORKTREE_ROOT))


# 预加载 main.py, 让 test_*.py 的 ``from astrbot_plugin_spcode_toolkit
# import main`` 解析到 worktree 自己的 main.py(而非 ghost worktree 的)。
if _MAIN_PY.exists():
    _spec = importlib.util.spec_from_file_location(
        "astrbot_plugin_spcode_toolkit.main", str(_MAIN_PY)
    )
    if _spec and _spec.loader:
        _mod = importlib.util.module_from_spec(_spec)
        # 必须先 exec_module(真正加载 main.py 代码到 _mod.__dict__),
        # 再注册到 sys.modules,否则 test_*.py 的 import 会拿到空模块。
        # 同时注册 package 本身,防止 Python 再次 sys.path 扫描:
        _pkg_name = "astrbot_plugin_spcode_toolkit"
        if _pkg_name not in sys.modules:
            import types

            _pkg = types.ModuleType(_pkg_name)
            _pkg.__path__ = [str(_WORKTREE_ROOT)]  # 让包内 import 也能解析
            sys.modules[_pkg_name] = _pkg
        sys.modules[_pkg_name + ".main"] = _mod
        _spec.loader.exec_module(_mod)
