"""Worktree 激活状态(per-umo,模块级 dict,2026-08-20)。

数据流:
    Dashboard GitDiffSidebar 的 worktree 标签右键"激活"
      → POST /spcode/worktree-activate 写入本状态
      → on_llm_request 钩子(main.py ``_worktree_activation_inject``)读取,
        把激活 worktree 指引以 ``extra_user_content_parts``
        (``TextPart.mark_as_temp()``)注入每次 LLM 请求,
        让 LLM 知道要在哪个 worktree 上工作。

绑定校验:
    activation 记录激活时已加载项目的 ``directory``。读取方
    (:func:`get_for_directory`)传入当前 loaded project directory,
    不一致(项目已切换/卸载) → 视为未激活。这样无需在各
    load/unload 路径上显式清理,状态天然随项目生命周期失效。

与 ``tools/project/state.py`` 同为模块级单例设计(插件进程内唯一,
子系统代码不回写 main.py 实例属性)。
"""

from __future__ import annotations

# umo -> {"path": str, "branch": str | None, "directory": str}
#   path      激活 worktree 的绝对路径(porcelain 原样)
#   branch    激活时的分支名(detached 为 None,仅展示用途)
#   directory 激活时已加载项目的 directory(绑定校验用)
_active_worktrees: dict[str, dict] = {}


def put(umo: str, *, path: str, branch: str | None, directory: str) -> None:
    """登记/覆盖指定 umo 的激活 worktree。

    Args:
        umo: 会话 unified_msg_origin。
        path: worktree 绝对路径(须已在 worktree 列表中查证过)。
        branch: 分支名;detached worktree 传 None。
        directory: 激活时已加载项目的 directory(绑定校验用)。
    """
    _active_worktrees[umo] = {
        "path": path,
        "branch": branch,
        "directory": directory,
    }


def get(umo: str) -> dict | None:
    """取指定 umo 的激活记录(不做绑定校验;没有则 None)。"""
    return _active_worktrees.get(umo)


def get_for_directory(umo: str, directory: str | None) -> dict | None:
    """取激活记录,且要求其绑定的项目 directory 与当前一致。

    Args:
        umo: 会话 unified_msg_origin。
        directory: 当前已加载项目的 directory;None 或与记录不一致
            (项目已切换/卸载)时返回 None。

    Returns:
        激活记录 dict(path/branch/directory),未激活或绑定失效则 None。
    """
    info = _active_worktrees.get(umo)
    if info is None:
        return None
    if not directory or info.get("directory") != directory:
        return None
    return info


def pop(umo: str) -> dict | None:
    """弹出并返回指定 umo 的激活记录(取消激活;没有则 None)。"""
    return _active_worktrees.pop(umo, None)


def reset() -> None:
    """清空所有激活状态(测试 fixture / 插件 terminate)。"""
    _active_worktrees.clear()
