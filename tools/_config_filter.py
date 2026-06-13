"""enabled_tools 配置过滤逻辑（独立模块，方便测试）。

从 main.py 提取出来，使纯函数（不依赖 astrbot）可被单元测试直接 import。
"""

from __future__ import annotations

# 全部可用工具名（用于校验 enabled_tools 字段）
#
# 注意:`todo_list` / `inta_shell` 不在此处——它们是组别名(group alias),
# 在下方 _TOOL_GROUPS 中定义,会被 filter_enabled_tools() 展开为实际工具名。
# 这样用户在配置中只看到一个 `todo_list` / `inta_shell` 选项,
# 勾选后系统一次性注入该组全部子工具,避免漏勾导致功能残缺。
ALL_TOOL_NAMES: list[str] = [
    "code_check",
    # "code_index",
    # "code_explore",
    "es_search",
    "astrbot_file_remove",
    "astrbot_file_compare",
    # todo_list 4 个子工具(v2.6 通过 `todo_list` 组别名一键启用)
    "todo_create",
    "todo_query",
    "todo_modify",
    "todo_clear",
    # inta_shell 5 个子工具(v2.5)
    "astrbot_inta_shell_start",
    "astrbot_inta_shell_send",
    "astrbot_inta_shell_read",
    "astrbot_inta_shell_stop",
    "astrbot_inta_shell_list",
]

# 快捷组名(可一键启用整组工具)
#
# 设计动机:避免用户在配置页"勾选 4 个 todo_* 工具"这种易错操作。
# 配置 UI 只暴露组别名,filter_enabled_tools() 内部展开为全部子工具名。
_TOOL_GROUPS: dict[str, list[str]] = {
    # 交互式 Shell 会话管理:start / send / read / stop / list
    "inta_shell": [
        "astrbot_inta_shell_start",
        "astrbot_inta_shell_send",
        "astrbot_inta_shell_read",
        "astrbot_inta_shell_stop",
        "astrbot_inta_shell_list",
    ],
    # Todo list 自我管理:create / query / modify / clear
    # 4 个工具强相关,缺一会导致功能不完整(如 modify 不能 add),
    # 因此必须以组形式整体启用。
    "todo_list": [
        "todo_create",
        "todo_query",
        "todo_modify",
        "todo_clear",
    ],
}


def filter_enabled_tools(
    all_tool_names: list[str],
    enabled: list[str] | None,
) -> tuple[list[str], set[str]]:
    """根据配置过滤启用的工具名。

    Args:
        all_tool_names: 本插件所有可用工具名（如 ALL_TOOL_NAMES）
        enabled: 配置中的 enabled_tools 列表（None/空 = 全部禁用）
                 支持快捷组名(如 `inta_shell` → 展开为 5 个 shell 工具)

    Returns:
        (启用的工具名列表, 未识别的工具名集合)
    """
    if not enabled:
        return [], set()
    enabled_set = set(enabled)
    valid = set(all_tool_names)
    # 展开快捷组名
    expanded: set[str] = set()
    for name in list(enabled_set):
        if name in _TOOL_GROUPS:
            expanded.update(_TOOL_GROUPS[name])
            enabled_set.remove(name)
    enabled_set.update(expanded & valid)

    unknown = enabled_set - valid
    return sorted(enabled_set & valid), unknown
