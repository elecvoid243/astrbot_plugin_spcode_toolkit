"""End-to-end smoke test for the spcode webapi surface.

The Dashboard talks to AstrBot via the 24 ``/spcode/*`` HTTP endpoints
registered by :func:`tools.webapi.register_webapi_routes`.  This
test exercises the route table and each handler in isolation:

* All handlers are present, callable, and accept the
  ``plugin`` positional + keyword args documented in their
  signatures.
* The ``_wrap`` adapter pulls the right values out of a fake
  Starlette/Quart ``request`` and forwards them.
* ``register_webapi_routes`` is idempotent and defensive — a
  broken endpoint is logged but does not abort the others.

The tests use a plain :class:`unittest.mock.MagicMock` for
``plugin``; handler-specific state is set up inline.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from tools.webapi import HANDLERS, ROUTES, _wrap, register_webapi_routes

# === All 6 handlers are present and call-return-dict =====================


def _make_minimal_plugin() -> MagicMock:
    """Plugin mock with the attributes most handlers touch."""
    plugin = MagicMock()
    plugin._loaded_projects = {}
    plugin._loaded_agents = {}
    plugin._plan_mode = {}
    plugin._plan_reminded = {}
    plugin._config = {
        "agentsmd_enabled": False,
        "codegraph_enabled": False,
        "git_path": "",
    }
    return plugin


# file_browser.handle reads ``path`` from ``web.request`` directly (legacy
# pattern: it predates the ``_wrap`` adapter's kwargs injection).  Exercising
# it requires a starlette/quart ``request`` contextvar; the dedicated
# ``tests/test_file_browser.py`` already covers that path in isolation.
#
# git_log.handle reads ``n/ref/path/author/since/until`` from ``web.request.query``
# directly (because _wrap only injects umo/worktree/...; query-string filters
# like n=20&author=foo are handled inline).  The dedicated
# ``tests/test_git_log.py`` covers the full path with monkeypatched web.request.
_SKIP_FILE_BROWSER = frozenset(
    {
        "handle_get_file_browser",
        "handle_get_git_log",  # PR-2 (2026-06-24)
        "handle_get_git_show",  # v3.8 (2026-06-25)
        "handle_get_git_file",  # spec B (2026-07-11) — uses web.request.query inline
        "handle_post_btw",  # v2.20 (2026-07-17) — 需要 body 形参(_wrap 注入)
    }
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name",
    sorted(set(HANDLERS.keys()) - _SKIP_FILE_BROWSER),
)
async def test_handler_callable_returns_dict(handler_name: str) -> None:
    """Every handler in :data:`HANDLERS` can be awaited with a mock
    plugin and returns a dict-shaped object (possibly an error envelope,
    或 ``_JSONResponseCompat`` — starlette-compatible 双面体)。
    """
    from tools.webapi._helpers import _JSONResponseCompat

    plugin = _make_minimal_plugin()
    handler = HANDLERS[handler_name]
    # We pass empty kwargs so handlers fall through to their default
    # (no-umo / no-worktree / no-scope) branches.
    result = await handler(plugin)
    assert isinstance(result, (dict, _JSONResponseCompat)), (
        f"{handler_name} returned {type(result)!r}"
    )


def test_file_browser_handler_excluded_from_smoke() -> None:
    """Pin the exclusion so we notice if a refactor enables plain calls."""
    assert "handle_get_file_browser" not in (set(HANDLERS.keys()) - _SKIP_FILE_BROWSER)


def test_git_log_handler_excluded_from_smoke() -> None:
    """Pin the exclusion (PR-2): git_log uses web.request.query inline."""
    assert "handle_get_git_log" not in (set(HANDLERS.keys()) - _SKIP_FILE_BROWSER)


def test_git_show_handler_excluded_from_smoke() -> None:
    """Pin the exclusion (v3.8): git_show uses web.request.query inline."""
    assert "handle_get_git_show" not in (set(HANDLERS.keys()) - _SKIP_FILE_BROWSER)


def test_btw_handler_excluded_from_smoke() -> None:
    """v2.20: handle_post_btw 需要 body 形参,smoke parametrize 自动调
    handler(plugin) 无 body 会触发 TypeError (body is keyword-only)。"""
    assert "handle_post_btw" not in (set(HANDLERS.keys()) - _SKIP_FILE_BROWSER)


def test_routes_table_has_thirty_endpoints() -> None:
    """The route table lists all spcode webapi endpoints.

    v2.17.0 (2026-07-15) 新增 6 个 git 端点(init/branches/create/delete/switch/revert)。
    v2.18.0 (2026-07-16) 新增 1 个 git 端点(repo-check)。
    端点总数演进:24 (spec B) -> 30 (v2.17.0) -> 31 (v2.18.0) -> 32 (v2.20 btw)
    -> 33 (2026-07-17 file-write) -> 35 (2026-07-18 file-rename/remove)
    -> 36 (2026-07-22 file-binary) -> 37 (PR-4 vivado-status).
    - 24 个之前端点:9 GET + 11 POST + 1 PATCH + 1 DELETE = 22 routes,
      部分端点共享路径(/spcode/docs 用 POST/PATCH/DELETE 三方法)。
    - v2.17.0 新增:1 GET (git-branches) + 5 POST (init/create/delete/switch/revert)。
    - v2.18.0 新增:1 GET (git-repo-check)。
    - 31 entries:12 GET + 17 POST + 1 PATCH + 1 DELETE (含 /spcode/docs 多方法)。
    """
    routes = {entry[0] for entry in ROUTES}
    assert routes == {
        "/spcode/project-status",
        "/spcode/plan-mode",
        "/spcode/git-worktrees",
        "/spcode/git-diff",
        "/spcode/git-status",  # v2.13 (2026-06-24)
        "/spcode/git-log",  # PR-2 (2026-06-24)
        "/spcode/git-show",  # v3.8 (2026-06-25)
        "/spcode/git-stats",  # v2.21 (2026-07-18)
        "/spcode/git-stage",  # PR-3 (2026-06-24)
        "/spcode/git-unstage",  # PR-4 (2026-06-24)
        "/spcode/git-commit",  # PR-5 (2026-06-24)
        "/spcode/file-browser",
        "/spcode/file-binary",  # 2026-07-22 — 原始字节流(供 BinaryPreview)
        "/spcode/file-restore",
        "/spcode/file-discard-hunk",  # v2.16.0 (2026-07-06)
        "/spcode/git-worktree-add",  # v2.14.0 (2026-06-26) PR-B
        "/spcode/git-worktree-remove",  # v2.14.0 (2026-06-26) PR-C
        "/spcode/git-worktree-lock",  # v2.14.0 (2026-06-26) PR-D
        "/spcode/git-worktree-unlock",  # v2.14.0 (2026-06-26) PR-D
        "/spcode/codegraph-status",  # v2.14.x (2026-06-28)
        # ── v2.20 (2026-07-17) — POST /spcode/btw ──
        "/spcode/btw",  # v2.20 btw endpoint (顺便问问)
        "/spcode/file-search",  # v2.15.0 (2026-07-02)
        "/spcode/file-name-search",  # v2.15.0 (2026-07-02)
        "/spcode/git-file",  # spec B (2026-07-11)
        "/spcode/docs",  # spec B (2026-07-11)
        # ── v2.17.0 (2026-07-15) PR-B ~ PR-G 新增 6 端点 ──
        "/spcode/git-init",  # v2.17.0 PR-B
        "/spcode/git-branches",  # v2.17.0 PR-C GET
        "/spcode/git-branch-create",  # v2.17.0 PR-D
        "/spcode/git-branch-delete",  # v2.17.0 PR-E
        "/spcode/git-branch-switch",  # v2.17.0 PR-F
        "/spcode/git-revert",  # v2.17.0 PR-G
        "/spcode/git-repo-check",  # v2.18.0 (2026-07-16)
        "/spcode/file-write",  # 2026-07-17 (workspace file editor)
        "/spcode/file-rename",  # 2026-07-18 (workspace file editor)
        "/spcode/file-remove",  # 2026-07-18 (workspace file editor)
        "/spcode/vivado-status",  # PR-4 (2026-07-23)
    }
    # Methods sanity:
    # 24 base: 10 GET + 12 POST + 1 PATCH + 1 DELETE = 24 entries
    #   docs 是 POST/PATCH/DELETE 三方法(同一路径)
    # v2.17.0 +1 GET (git-branches) + 5 POST = 6 entries
    # 30 entries total: 11 GET + 17 POST + 1 PATCH + 1 DELETE
    # + file-binary (GET) + file-rename (POST) + file-remove (POST)
    # + git-stats (GET) + file-write (POST) + vivado-status (GET)
    methods = [m for entry in ROUTES for m in entry[1]]
    assert methods.count("GET") == 15  # +file-binary +vivado-status
    assert methods.count("POST") == 21
    assert methods.count("PATCH") == 1
    assert methods.count("DELETE") == 1


class TestV217NewEndpointsSmoke:
    """v2.17.0 6 个新端点的 smoke 验证(纯 import + route 检查)。

    spec §6.4 端到端烟囱测试:每个新端点被注册到 ROUTES 表 + handler
    模块可正确 import。handler 的实质逻辑在专属 test file
    (``tests/test_git_*``) 验证。
    """

    @staticmethod
    def _route_paths() -> set[str]:
        from tools.webapi import ROUTES

        return {r[0] for r in ROUTES}

    def test_git_init_route_registered(self) -> None:
        from tools.webapi import git_init
        from tools.webapi import ROUTES  # noqa: F401  (consistency)

        assert git_init.handle is not None
        assert "/spcode/git-init" in self._route_paths()

    def test_git_branches_route_registered(self) -> None:
        from tools.webapi import git_branches

        assert git_branches.handle is not None
        assert "/spcode/git-branches" in self._route_paths()

    def test_git_branch_create_route_registered(self) -> None:
        from tools.webapi import git_branch_create

        assert git_branch_create.handle is not None
        assert "/spcode/git-branch-create" in self._route_paths()

    def test_git_branch_delete_route_registered(self) -> None:
        from tools.webapi import git_branch_delete

        assert git_branch_delete.handle is not None
        assert "/spcode/git-branch-delete" in self._route_paths()

    def test_git_branch_switch_route_registered(self) -> None:
        from tools.webapi import git_branch_switch

        assert git_branch_switch.handle is not None
        assert "/spcode/git-branch-switch" in self._route_paths()

    def test_git_revert_route_registered(self) -> None:
        from tools.webapi import git_revert

        assert git_revert.handle is not None
        assert "/spcode/git-revert" in self._route_paths()


# === _wrap adapter ====================================================


@pytest.mark.asyncio
async def test_wrap_injects_umo_from_get_query(monkeypatch) -> None:
    """GET 路径:``_wrap`` 从 ``web.request.query`` 读取 ``umo``。"""
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def handler(plugin, *, umo=None):  # type: ignore[no-untyped-def]
        captured["umo"] = umo
        return {"status": "ok"}

    mock_req = make_web_request_mock(query={"umo": "abc:1"})
    mock_req.method = "GET"
    monkeypatch.setattr(web, "request", mock_req)

    view = _wrap(handler, plugin=None)
    await view()
    assert captured["umo"] == "abc:1"


@pytest.mark.asyncio
async def test_wrap_injects_umo_from_post_body(monkeypatch) -> None:
    """POST 路径:``_wrap`` 从 ``web.request.json()`` 读取 ``umo``。"""
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def handler(plugin, *, umo=None):  # type: ignore[no-untyped-def]
        captured["umo"] = umo
        return {"status": "ok"}

    mock_req = make_web_request_mock()

    async def _json(default=None):  # type: ignore[no-untyped-def]
        return {"umo": "xyz:2"}

    mock_req.method = "POST"
    mock_req.json = _json
    monkeypatch.setattr(web, "request", mock_req)

    view = _wrap(handler, plugin=None)
    await view()
    assert captured["umo"] == "xyz:2"


@pytest.mark.asyncio
async def test_wrap_injects_scope_with_default(monkeypatch) -> None:
    """GET 缺省 scope → 注入 ``"unstaged"``。"""
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def handler(plugin, *, scope=None):  # type: ignore[no-untyped-def]
        captured["scope"] = scope
        return {"status": "ok"}

    mock_req = make_web_request_mock(query={})
    mock_req.method = "GET"
    monkeypatch.setattr(web, "request", mock_req)

    view = _wrap(handler, plugin=None)
    await view()
    assert captured["scope"] == "unstaged"


# === _wrap adapter — real framework call pattern ======================
# v3.7 regression: AstrBot's registered_web_api dispatcher invokes the
# wrapped handler as ``view_func(**path_values)`` — no positional
# ``request`` argument.  The previous _wrap tried to pull ``request``
# from ``args[0]`` and silently got ``None``, so every POST endpoint
# (git-stage / git-unstage / git-commit) received ``body=None`` and
# returned ``invalid_body``.  These tests pin down the framework
# contract: ``_wrap`` must read the request from
# ``astrbot.api.web.request`` (the Quart-style proxy bound by the
# framework's ``bind_request_context``), regardless of how the caller
# invokes the wrapper.


@pytest.mark.asyncio
async def test_wrap_post_body_via_web_request(monkeypatch) -> None:
    """POST with body — framework calls ``view()`` with no positional args.

    ``_wrap`` must read body from ``web.request.json()`` and forward
    body / umo / worktree as kwargs to the handler.
    """
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def handler(plugin, *, body=None, umo=None, worktree=None):  # type: ignore[no-untyped-def]
        captured["body"] = body
        captured["umo"] = umo
        captured["worktree"] = worktree
        return {"status": "ok"}

    payload = {
        "files": ["a.py", "b.py"],
        "umo": "abc:1",
        "worktree": "feat/x",
    }

    async def _json(default=None):  # type: ignore[no-untyped-def]
        return payload

    mock_req = make_web_request_mock()
    mock_req.method = "POST"
    mock_req.json = _json
    monkeypatch.setattr(web, "request", mock_req)

    view = _wrap(handler, plugin=None)
    # Real framework call pattern — no positional args.
    await view()

    assert captured["body"] == payload
    assert captured["umo"] == "abc:1"
    assert captured["worktree"] == "feat/x"


@pytest.mark.asyncio
async def test_wrap_get_query_via_web_request(monkeypatch) -> None:
    """GET with query — framework calls ``view()`` with no positional args.

    ``_wrap`` must read umo/worktree/scope/path from ``web.request.query``.
    """
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def handler(plugin, *, umo=None, worktree=None, scope=None):  # type: ignore[no-untyped-def]
        captured["umo"] = umo
        captured["worktree"] = worktree
        captured["scope"] = scope
        return {"status": "ok"}

    mock_req = make_web_request_mock(
        query={"umo": "u:m", "worktree": "feat/y", "scope": "staged"},
    )
    mock_req.method = "GET"
    monkeypatch.setattr(web, "request", mock_req)

    view = _wrap(handler, plugin=None)
    await view()

    assert captured["umo"] == "u:m"
    assert captured["worktree"] == "feat/y"
    assert captured["scope"] == "staged"


# === register_webapi_routes ===========================================


def test_register_webapi_routes_calls_context_thirty_two_times() -> None:
    """``register_webapi_routes`` must call ``register_web_api`` once per route.

    v2.17.0 (2026-07-15): route count 24 -> 30(+git-init/branches/create/delete/switch/revert)。
    v2.18.0 (2026-07-16): route count 30 -> 31(+git-repo-check)。
    v2.20 (2026-07-17): route count 31 -> 32(+btw)。
    """
    plugin = MagicMock()
    register_webapi_routes(plugin)
    # 38 entries: 35 (pre) + file-binary (2026-07-22) + git-stats + vivado-status (PR-4)
    assert plugin.context.register_web_api.call_count == 38


def test_register_webapi_routes_continues_on_failure() -> None:
    """A single failure must not abort the rest of the loop."""

    plugin = MagicMock()
    call_count = 0

    def _maybe_fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise RuntimeError("simulated failure")

    plugin.context.register_web_api.side_effect = _maybe_fail

    # Should not raise; should attempt all 38 routes (+file-binary +git-stats +vivado-status).
    register_webapi_routes(plugin)
    assert call_count == 38


# ─── PR-B (v2.14.0, 2026-06-26) ────────────────────────────────────


def test_git_worktree_add_route_registered() -> None:
    """git_worktree_add 应在 ROUTES 表中注册 (PR-B ADD endpoint)。"""
    routes = [r[0] for r in ROUTES]
    assert "/spcode/git-worktree-add" in routes


def test_handlers_dict_has_add_entry() -> None:
    """HANDLERS 表应包含 handle_post_git_worktree_add。"""
    assert "handle_post_git_worktree_add" in HANDLERS


# ─── PR-C (v2.14.0, 2026-06-26) ────────────────────────────────────


def test_git_worktree_remove_route_registered() -> None:
    """git_worktree_remove 应在 ROUTES 表中注册 (PR-C REMOVE endpoint)。"""
    routes = [r[0] for r in ROUTES]
    assert "/spcode/git-worktree-remove" in routes


def test_handlers_dict_has_remove_entry() -> None:
    """HANDLERS 表应包含 handle_post_git_worktree_remove。"""
    assert "handle_post_git_worktree_remove" in HANDLERS


# ─── PR-D (v2.14.0, 2026-06-26) ────────────────────────────────────


def test_git_worktree_lock_route_registered() -> None:
    """git_worktree_lock 应在 ROUTES 表中注册 (PR-D LOCK endpoint)。"""
    routes = [r[0] for r in ROUTES]
    assert "/spcode/git-worktree-lock" in routes


def test_git_worktree_unlock_route_registered() -> None:
    """git_worktree_unlock 应在 ROUTES 表中注册 (PR-D UNLOCK endpoint)。"""
    routes = [r[0] for r in ROUTES]
    assert "/spcode/git-worktree-unlock" in routes


def test_handlers_dict_has_lock_entry() -> None:
    """HANDLERS 表应包含 handle_post_git_worktree_lock。"""
    assert "handle_post_git_worktree_lock" in HANDLERS


def test_handlers_dict_has_unlock_entry() -> None:
    """HANDLERS 表应包含 handle_post_git_worktree_unlock。"""
    assert "handle_post_git_worktree_unlock" in HANDLERS


# ── PR-B ADD endpoint _wrap integration (Task 2.3 — E2E 收尾) ──────


@pytest.mark.asyncio
async def test_add_full_e2e_cycle(tmp_path):
    """E2E:从 0 创建 primary → ADD worktree → 验证目录/branch/HEAD 真实存在。

    这是 PR-B 端到端冒烟(模拟 dashboard 的 '新建 worktree' 流程):
      1. 在 tmp_path 创建 primary git repo + 1 commit
      2. 通过 handler 调 ADD -b feat → 真实 git worktree add
      3. 验证:
         - 返回 success
         - worktree 目录真实存在
         - new branch 在 git branch list 中
         - HEAD 指向该 branch
         - GET worktrees(模拟前端刷新)能列出新增
    """
    import subprocess
    from unittest.mock import MagicMock
    from tools.webapi.git_worktree_add import handle as add_handle
    from tools.webapi.git_worktrees import handle as list_handle

    primary = tmp_path / "primary"
    primary.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(primary)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(primary), "config", "user.email", "t@t.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(primary), "config", "user.name", "T"],
        check=True,
        capture_output=True,
    )
    (primary / "README.md").write_text("# Test repo\n")
    subprocess.run(
        ["git", "-C", str(primary), "add", "."], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(primary), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )

    # 模拟 plugin state
    plugin = MagicMock()
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "file_remove_blacklist": None,
    }
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {
        "directory": str(primary),
        "loaded_at": 0.0,
    }

    target = str(tmp_path / "wt-feat")
    body = {"path": target, "branch": "feat", "create": True}

    # 1. ADD
    result = await add_handle(
        plugin,
        umo="e2e:umo",
        worktree=None,
        body=body,
    )
    assert result["data"]["reason"] is None, result["data"]["stderr"]
    assert result["data"]["loaded"] is True

    # 2. 验证文件系统层
    assert (tmp_path / "wt-feat").is_dir()
    assert (tmp_path / "wt-feat" / "README.md").is_file()

    # 3. 验证 git 层
    branch_list = subprocess.run(
        ["git", "-C", str(primary), "branch", "--list", "feat"],
        capture_output=True,
        text=True,
    ).stdout
    assert "feat" in branch_list, f"branch not created: {branch_list!r}"

    # 4. GET worktrees 端到端(需要 populate tools.project.state)
    from tools.project import state as _proj_state

    _proj_state.reset()
    _proj_state.put("e2e:umo", {"directory": str(primary), "loaded_at": 100.0})

    list_result = await list_handle(plugin)
    _proj_state.reset()  # cleanup
    assert list_result["data"]["loaded"] is True
    worktree_paths = [wt["path"] for wt in list_result["data"]["worktrees"]]
    assert any(
        os.path.normpath(p) == os.path.normpath(target) for p in worktree_paths
    ), f"new worktree not in list: {worktree_paths}"


@pytest.mark.asyncio
async def test_wrap_post_to_git_worktree_add_passes_body(
    monkeypatch,
    tmp_path,
) -> None:
    """E2E:POST /spcode/git-worktree-add → _wrap 把 body 透传给 handler。

    验证 _wrap 适配器对新增的 git-worktree-add 端点正确工作:
    framework 调用 view() (无位置参数),_wrap 从 web.request.json() 读 body
    并透传给 handler.handle(plugin, body=...)。
    """
    from astrbot.api import web
    from tests.conftest import make_web_request_mock
    from tools.webapi import _wrap, HANDLERS

    captured: dict = {}

    real_handle = HANDLERS["handle_post_git_worktree_add"]

    async def stub_handle(plugin, *, body=None, umo=None, worktree=None):  # type: ignore[no-untyped-def]
        captured["body"] = body
        captured["umo"] = umo
        # 用 minimum 字段触发 path_unsafe(快速失败,无需真实 git)
        return {"status": "ok", "data": {"reason": "path_unsafe"}}

    # Patch the handler
    monkeypatch.setattr(
        "tools.webapi.git_worktree_add.handle",
        stub_handle,
    )

    payload = {
        "path": str(tmp_path / "x"),
        "branch": "feat",
        "umo": "abc:1",
    }

    async def _json(default=None):  # type: ignore[no-untyped-def]
        return payload

    mock_req = make_web_request_mock()
    mock_req.method = "POST"
    mock_req.json = _json
    monkeypatch.setattr(web, "request", mock_req)

    # Use a MagicMock plugin (the stub handle ignores it)
    view = _wrap(stub_handle, plugin=None)
    await view()

    assert captured["body"] == payload
    assert captured["umo"] == "abc:1"
    # Sanity: real handler should be async-callable with a real plugin mock
    import inspect

    sig = inspect.signature(real_handle)
    assert "plugin" in sig.parameters
    assert "body" in sig.parameters


# ─── v2.16.0 (2026-07-06) file-discard-hunk ───────────────────────


def test_handlers_dict_has_discard_hunk_entry() -> None:
    """HANDLERS 表应包含 handle_post_file_discard_hunk(v2.16.0)。"""
    assert "handle_post_file_discard_hunk" in HANDLERS


@pytest.mark.asyncio
async def test_wrap_injects_body_for_post(monkeypatch) -> None:
    """POST 路径:_wrap 为声明 body 的 handler 注入 JSON body。"""
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def handler(plugin, *, body=None):  # type: ignore[no-untyped-def]
        captured["body"] = body
        return {"status": "ok"}

    payload = {
        "file": "src/app.py",
        "patch_text": "diff --git a/src/app.py b/src/app.py\n",
    }

    async def _json(default=None):  # type: ignore[no-untyped-def]
        return payload

    mock_req = make_web_request_mock(query={})
    mock_req.method = "POST"
    mock_req.json = _json
    monkeypatch.setattr(web, "request", mock_req)

    view = _wrap(handler, plugin=None)
    await view()

    assert captured["body"] == payload
