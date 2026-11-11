"""enabled_tools 配置过滤逻辑（独立模块，方便测试）。

从 main.py 提取出来，使纯函数（不依赖 astrbot）可被单元测试直接 import。
"""

from __future__ import annotations

# 全部可用工具名（用于校验 enabled_tools 字段）
#
# 注意:`todo_list` 不在此处——它是组别名(group alias),
# 在下方 _TOOL_GROUPS 中定义,会被 filter_enabled_tools() 展开为实际工具名。
# 这样用户在配置中只看到一个 `todo_list` 选项,
# 勾选后系统一次性注入该组全部子工具,避免漏勾导致功能残缺。
ALL_TOOL_NAMES: list[str] = [
    "code_check",
    "code_format",  # v2.14 (2026-06-25): 新增;与 code_check 配对
    "code_crap",  # code_crap (2026-08-30): 新增;CRAP 风险检测(只读)
    # "code_index",
    # "code_explore",
    "es_search",
    "astrbot_file_remove",
    "astrbot_file_compare",
    # todo_list 组(v2.35 起只有 1 个工具:todo_manage 的 6 个 action)
    "todo_manage",
]

# 快捷组名(可一键启用整组工具)
#
# 设计动机:避免用户在配置页"勾选 4 个 todo_* 工具"这种易错操作。
# 配置 UI 只暴露组别名,filter_enabled_tools() 内部展开为全部子工具名。
_TOOL_GROUPS: dict[str, list[str]] = {
    # Todo list 自我管理:单个 todo_manage 覆盖 create / query / add /
    # update / delete / clear 六个 action。组别名保留以兼容老配置。
    "todo_list": ["todo_manage"],
    "file_compare": ["astrbot_file_compare"],
    "file_remove": ["astrbot_file_remove"],
}


def filter_enabled_tools(
    all_tool_names: list[str],
    enabled: list[str] | None,
) -> tuple[list[str], set[str]]:
    """根据配置过滤启用的工具名。

    Args:
        all_tool_names: 本插件所有可用工具名（如 ALL_TOOL_NAMES）
        enabled: 配置中的 enabled_tools 列表（None/空 = 全部禁用）
                 支持快捷组名(如 `todo_list` → 展开为 6 个 todo 工具)

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
