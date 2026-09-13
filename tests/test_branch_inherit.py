"""分支会话状态惰性继承(inherit_state)测试。

覆盖:
- 命中本 umo 状态时零开销直返(不触分支关系)
- 沿分支关系克隆父状态,克隆后两边独立
- 传递链 A→B→C(B 无状态时 C 上溯到 A)
- 无关系 / 开关关闭 / 深度超限(环)→ None 且不写状态
- agentsmd AgentsStateManager 与 worktree put(kwargs)两种存储形态
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tools import worktree_activation
from tools._branch_inherit import inherit_state
from tools.agentsmd._state import AgentsState, AgentsStateManager
from tools.project import state as project_state

PARENT_UMO = "webchat:FriendMessage:parent"
CHILD_UMO = "webchat:FriendMessage:child"


def _relations(*pairs: tuple[str, str]) -> dict[str, dict]:
    """把 (child_session_id, source_session_id) 序列转成关系表。"""
    return {
        child: {"source_session_id": source, "source_message_id": 1}
        for child, source in pairs
    }


def _patch_relations(relations: dict[str, dict]):
    return patch(
        "astrbot.core.db_helper.get_branch_relations",
        new=AsyncMock(return_value=relations),
    )


@pytest.fixture(autouse=True)
def _clean_states():
    project_state.reset()
    worktree_activation.reset()
    yield
    project_state.reset()
    worktree_activation.reset()


def test_returns_existing_state_without_db_hit():
    project_state.put(PARENT_UMO, {"directory": "/p"})
    with _patch_relations({}) as mock_rel:
        state = asyncio.run(
            inherit_state(
                PARENT_UMO,
                subsystem="project",
                get_state=project_state.get,
                set_state=project_state.put,
            )
        )
    assert state == {"directory": "/p"}
    mock_rel.assert_not_awaited()


def test_clones_parent_state_and_is_independent():
    project_state.put(
        PARENT_UMO, {"directory": "/p", "skipped_substeps": {"codegraph"}}
    )
    with _patch_relations(_relations(("child", "parent"))):
        state = asyncio.run(
            inherit_state(
                CHILD_UMO,
                subsystem="project",
                get_state=project_state.get,
                set_state=project_state.put,
            )
        )

    assert state is not None and state["directory"] == "/p"
    child_state = project_state.get(CHILD_UMO)
    parent_state = project_state.get(PARENT_UMO)
    assert child_state is not None and child_state is not parent_state

    # 克隆后两边独立演化
    child_state["directory"] = "/child-own"
    assert parent_state["directory"] == "/p"


def test_transitive_chain_falls_back_to_ancestor():
    # A(有状态) ← B(无状态) ← C:关系只登记 leaf→mid、mid→grand
    project_state.put("webchat:FriendMessage:grand", {"directory": "/grand"})
    relations = _relations(("mid", "grand"), ("leaf", "mid"))
    with _patch_relations(relations):
        state = asyncio.run(
            inherit_state(
                "webchat:FriendMessage:leaf",
                subsystem="project",
                get_state=project_state.get,
                set_state=project_state.put,
            )
        )
    assert state == {"directory": "/grand"}
    assert project_state.get("webchat:FriendMessage:leaf") == {"directory": "/grand"}


def test_no_relation_returns_none():
    with _patch_relations({}):
        state = asyncio.run(
            inherit_state(
                CHILD_UMO,
                subsystem="project",
                get_state=project_state.get,
                set_state=project_state.put,
            )
        )
    assert state is None
    assert project_state.get(CHILD_UMO) is None


def test_disabled_returns_none_even_with_parent_state():
    project_state.put(PARENT_UMO, {"directory": "/p"})
    with _patch_relations(_relations(("child", "parent"))):
        state = asyncio.run(
            inherit_state(
                CHILD_UMO,
                subsystem="project",
                get_state=project_state.get,
                set_state=project_state.put,
                enabled=False,
            )
        )
    assert state is None
    assert project_state.get(CHILD_UMO) is None


def test_depth_limit_breaks_cycles():
    # 环:child → parent → child(...),且双方都无状态
    relations = _relations(("child", "parent"), ("parent", "child"))
    with _patch_relations(relations):
        state = asyncio.run(
            inherit_state(
                CHILD_UMO,
                subsystem="project",
                get_state=project_state.get,
                set_state=project_state.put,
            )
        )
    assert state is None


def test_agentsmd_manager_state_inherited():
    manager = AgentsStateManager()
    manager.set(
        PARENT_UMO,
        AgentsState(
            path="D:/proj/AGENTS.md",
            directory="D:/proj",
            last_content="# AGENTS",
            mtime=1.0,
        ),
    )
    with _patch_relations(_relations(("child", "parent"))):
        state = asyncio.run(
            inherit_state(
                CHILD_UMO,
                subsystem="agentsmd",
                get_state=manager.get,
                set_state=manager.set,
            )
        )

    assert state is not None and state.path == "D:/proj/AGENTS.md"
    child_state = manager.get(CHILD_UMO)
    parent_state = manager.get(PARENT_UMO)
    assert child_state is not None and child_state is not parent_state


def test_worktree_state_inherited_via_kwargs_put():
    worktree_activation.put(
        PARENT_UMO, path="D:/repo/.wt/x", branch="feature", directory="D:/repo"
    )
    with _patch_relations(_relations(("child", "parent"))):
        state = asyncio.run(
            inherit_state(
                CHILD_UMO,
                subsystem="worktree",
                get_state=worktree_activation.get,
                set_state=lambda u, s: worktree_activation.put(u, **s),
            )
        )

    assert state is not None and state["path"] == "D:/repo/.wt/x"
    assert worktree_activation.get(CHILD_UMO)["branch"] == "feature"
