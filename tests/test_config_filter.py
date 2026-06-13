"""_config_filter 配置过滤逻辑测试。

覆盖:
- 基础过滤(全部禁用 / 显式启用 / 未知工具名)
- 组别名展开(inta_shell / todo_list)
- 混合场景(组别名 + 显式工具名 + 未知名)
- 边界场景(None / 空列表 / 重复)
- 迁移路径:旧 config 里有 "todo_list" 条目时,新代码应自动展开为 4 个工具

__author__: AstrBot Agent Harness 开发专家
__created__: 2026-06-13
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools._config_filter import (  # noqa: E402
    ALL_TOOL_NAMES,
    filter_enabled_tools,
)


# ── 1. 基础过滤行为 ─────────────────────────────────


def test_empty_config_disables_all_tools():
    """enabled_tools 为空 → 全部禁用(安全默认)。"""
    enabled, unknown = filter_enabled_tools(ALL_TOOL_NAMES, [])
    assert enabled == []
    assert unknown == set()


def test_none_config_disables_all_tools():
    """enabled_tools=None → 全部禁用(与空列表等价)。"""
    enabled, unknown = filter_enabled_tools(ALL_TOOL_NAMES, None)
    assert enabled == []
    assert unknown == set()


def test_explicit_single_tool_enabled():
    """显式启用单个工具(非组别名)。"""
    enabled, unknown = filter_enabled_tools(ALL_TOOL_NAMES, ["code_check"])
    assert enabled == ["code_check"]
    assert unknown == set()


def test_explicit_multiple_tools_enabled_sorted():
    """显式启用多个工具,返回结果按字母序排列。"""
    enabled, unknown = filter_enabled_tools(
        ALL_TOOL_NAMES,
        ["es_search", "code_check", "astrbot_file_compare"],
    )
    assert enabled == ["astrbot_file_compare", "code_check", "es_search"]
    assert unknown == set()


def test_unknown_tool_name_reported():
    """未识别的工具名进入 unknown 集合,被忽略(不抛异常)。"""
    enabled, unknown = filter_enabled_tools(
        ALL_TOOL_NAMES, ["code_check", "bogus_tool", "another_bogus"]
    )
    assert enabled == ["code_check"]
    assert unknown == {"bogus_tool", "another_bogus"}


def test_duplicate_tool_names_deduplicated():
    """重复工具名去重(集合语义)。"""
    enabled, unknown = filter_enabled_tools(
        ALL_TOOL_NAMES, ["code_check", "code_check", "es_search", "es_search"]
    )
    assert enabled == ["code_check", "es_search"]
    assert unknown == set()


# ── 2. 组别名展开(inta_shell) ──────────────────────


def test_inta_shell_group_expands_to_5_tools():
    """勾选 inta_shell → 展开为 5 个 shell 工具。"""
    enabled, unknown = filter_enabled_tools(ALL_TOOL_NAMES, ["inta_shell"])
    expected = {
        "astrbot_inta_shell_start",
        "astrbot_inta_shell_send",
        "astrbot_inta_shell_read",
        "astrbot_inta_shell_stop",
        "astrbot_inta_shell_list",
    }
    assert set(enabled) == expected
    assert "inta_shell" not in enabled, "组别名本身不应出现在启用列表中"
    assert unknown == set()


def test_inta_shell_group_mixed_with_explicit_tools():
    """组别名 + 显式工具名可同时启用(展开 + 合并)。"""
    enabled, unknown = filter_enabled_tools(
        ALL_TOOL_NAMES, ["inta_shell", "code_check"]
    )
    assert "code_check" in enabled
    assert "astrbot_inta_shell_start" in enabled
    assert len(enabled) == 6  # 5 shell + 1 code_check


# ── 3. 组别名展开(todo_list) ────────────────────────


def test_todo_list_group_expands_to_4_tools():
    """勾选 todo_list → 展开为 4 个 todo 工具(本测试的核心)。

    验证需求:用户在配置页只看到一个 `todo_list` 选项,
    勾选后系统一次性给 LLM 注入 todo_create / todo_query / todo_modify / todo_clear
    四个工具,避免用户漏勾导致功能不完整。
    """
    enabled, unknown = filter_enabled_tools(ALL_TOOL_NAMES, ["todo_list"])
    expected = {
        "todo_create",
        "todo_query",
        "todo_modify",
        "todo_clear",
    }
    assert set(enabled) == expected, (
        f"todo_list 组应展开为 4 个 todo_* 工具,实际得到 {set(enabled)}"
    )
    assert "todo_list" not in enabled, "组别名本身不应出现在启用列表中"
    assert unknown == set()


def test_todo_list_group_size_is_4():
    """todo_list 组展开后必须恰好 4 个工具(防回归:漏掉或加多)。"""
    enabled, _ = filter_enabled_tools(ALL_TOOL_NAMES, ["todo_list"])
    assert len(enabled) == 4, (
        f"todo_list 组应展开为 4 个工具,实际得到 {len(enabled)} 个: {enabled}"
    )


def test_todo_list_group_mixed_with_explicit_tools():
    """todo_list 组别名 + 显式工具名可同时启用。"""
    enabled, unknown = filter_enabled_tools(
        ALL_TOOL_NAMES, ["todo_list", "code_check"]
    )
    assert "code_check" in enabled
    assert "todo_create" in enabled
    assert "todo_query" in enabled
    assert "todo_modify" in enabled
    assert "todo_clear" in enabled
    assert len(enabled) == 5  # 4 todo + 1 code_check
    assert unknown == set()


# ── 4. 混合组别名 ───────────────────────────────────


def test_both_groups_expand_together():
    """inta_shell + todo_list 一起勾选 → 展开为 9 个工具。"""
    enabled, unknown = filter_enabled_tools(
        ALL_TOOL_NAMES, ["inta_shell", "todo_list"]
    )
    # 5 shell + 4 todo = 9
    assert len(enabled) == 9
    assert "astrbot_inta_shell_start" in enabled
    assert "astrbot_inta_shell_list" in enabled
    assert "todo_create" in enabled
    assert "todo_clear" in enabled
    assert unknown == set()


def test_all_options_enabled_returns_all_group_children():
    """勾选全部 2 个组别名 → 展开为所有组的子工具(独立工具不会被启用)。

    注意:`code_check` / `es_search` / `astrbot_file_remove` / `astrbot_file_compare`
    是独立工具(不属于任何组),不会因为勾选组别名而被自动启用。
    """
    from tools._config_filter import _TOOL_GROUPS

    enabled, unknown = filter_enabled_tools(ALL_TOOL_NAMES, ["inta_shell", "todo_list"])

    # 收集所有组的子工具名
    all_group_children = set()
    for children in _TOOL_GROUPS.values():
        all_group_children.update(children)

    # 启用列表应 = 所有组的子工具(组别名已展开,自身不在内)
    assert set(enabled) == all_group_children
    assert len(enabled) == len(all_group_children)
    assert unknown == set()


# ── 5. 迁移路径:旧 config 兼容 ──────────────────────


def test_legacy_todo_list_entry_expands_correctly():
    """v2.2 ~ v2.6 之间,部分用户的旧 config 可能含 "todo_list" 条目。

    新代码应自动将其识别为组别名并展开,而不是当成"未知工具名"忽略。
    这确保了老用户升级到 v2.6.1 后无需手动改 config 即可恢复 todo 功能。
    """
    # 模拟老用户的 config(只有 "todo_list" 条目)
    enabled, unknown = filter_enabled_tools(ALL_TOOL_NAMES, ["todo_list"])
    # 4 个 todo_* 工具应被启用
    assert "todo_create" in enabled
    assert "todo_query" in enabled
    assert "todo_modify" in enabled
    assert "todo_clear" in enabled
    # 不会因为 "todo_list" 而报告 unknown
    assert unknown == set(), (
        f"组别名 todo_list 不应被当作 unknown,但报告了: {unknown}"
    )


# ── 6. ALL_TOOL_NAMES 完整性约束 ────────────────────


def test_all_tool_names_excludes_group_aliases():
    """ALL_TOOL_NAMES 不应包含组别名本身(组别名只属于 _TOOL_GROUPS)。

    这是关键不变量:组别名在 ALL_TOOL_NAMES 中会被当作"独立工具"通过校验,
    但实际 main.py 中没有注册名为 todo_list / inta_shell 的 FunctionTool,
    会导致 enabled_set & valid 通过,但 tools_to_register 找不到对应类。

    这个测试是契约保护:如果有人不慎把 "todo_list" 加回 ALL_TOOL_NAMES,
    本测试会失败,提醒需要把它移到 _TOOL_GROUPS。
    """
    assert "todo_list" not in ALL_TOOL_NAMES, (
        "todo_list 是组别名,只应存在于 _TOOL_GROUPS,不应在 ALL_TOOL_NAMES 中"
    )
    assert "inta_shell" not in ALL_TOOL_NAMES, (
        "inta_shell 是组别名,只应存在于 _TOOL_GROUPS,不应在 ALL_TOOL_NAMES 中"
    )


def test_all_tool_names_contains_all_group_children():
    """_TOOL_GROUPS 中的所有子工具名都必须在 ALL_TOOL_NAMES 中。

    否则 filter_enabled_tools() 内的 `expanded & valid` 过滤会把它们丢弃,
    启用列表会变成空(用户困惑:勾选了但啥也没启用)。
    """
    from tools._config_filter import _TOOL_GROUPS

    all_set = set(ALL_TOOL_NAMES)
    for group_name, children in _TOOL_GROUPS.items():
        missing = [c for c in children if c not in all_set]
        assert not missing, (
            f"组 `{group_name}` 包含不在 ALL_TOOL_NAMES 中的工具: {missing}"
        )
