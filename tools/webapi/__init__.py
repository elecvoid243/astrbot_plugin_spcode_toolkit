# tools/webapi/__init__.py
"""Web API endpoint handlers, extracted from main.py.

This package owns the 25 ``/spcode/*`` HTTP endpoints consumed by the
Dashboard / WebUI:

  * ``/spcode/project-status``  (GET)
  * ``/spcode/project-load``    (POST)  # 2026-07-28 — 静默加载项目
  * ``/spcode/plan-mode``       (GET)
  * ``/spcode/git-worktrees``   (GET)
  * ``/spcode/git-diff``        (GET)
  * ``/spcode/git-status``      (GET)   # v2.13+
  * ``/spcode/git-log``         (GET)   # v3.7
  * ``/spcode/file-browser``    (GET)
  * ``/spcode/file-binary``     (GET)   # 2026-07-22 — 原始字节流(供 BinaryPreview)
  * ``/spcode/file-restore``    (POST)
  * ``/spcode/file-discard-hunk`` (POST) # v2.16.0 (2026-07-06)
  * ``/spcode/file-search``     (POST)  # v2.15.0 (2026-07-02)
  * ``/spcode/file-name-search`` (POST) # v2.15.0 (2026-07-02)
  * ``/spcode/git-stage``       (POST)  # v3.7
  * ``/spcode/git-unstage``     (POST)  # v3.7
  * ``/spcode/git-commit``      (POST)  # v3.7
  * ``/spcode/git-init``        (POST)  # v2.17.0 (2026-07-15) — git init 端点
  * ``/spcode/git-repo-check``   (GET)   # v2.18.0 (2026-07-16) - git 仓库探测
  * ``/spcode/git-show``        (GET)   # v3.8 (2026-06-25)
  * ``/spcode/git-stats``       (GET)   # v2.21 (2026-07-18)
  * ``/spcode/git-worktree-add``   (POST)  # v2.14.0 (2026-06-26) — PR-B ADD endpoint
  * ``/spcode/git-worktree-remove`` (POST) # v2.14.0 (2026-06-26) — PR-C REMOVE endpoint
  * ``/spcode/git-worktree-lock``   (POST)  # v2.14.0 (2026-06-26) — PR-D LOCK endpoint
  * ``/spcode/git-worktree-unlock`` (POST)  # v2.14.0 (2026-06-26) — PR-D UNLOCK endpoint
  * ``/spcode/codegraph-status``    (GET)  # v2.14.x (2026-06-28)

  * ``/spcode/git-file``         (GET)   # spec B (2026-07-11)
  * ``/spcode/docs``             (POST)  # spec B (2026-07-11) — create/upsert
  * ``/spcode/docs``             (PATCH) # spec B (2026-07-11) — rename
  * ``/spcode/docs``             (DELETE) # spec B (2026-07-11) — delete

Each endpoint lives in its own module (e.g. ``project_status.handle``).
``register_webapi_routes`` is the single entry-point main.py calls
during ``initialize()``; it iterates :data:`ROUTES` and adapts every
handler to the ``view_handler`` interface via :func:`_wrap`.

See docs/superpowers/specs/2026-06-23-webapi-extraction-design.md
for the full architecture.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from main import SPCodeToolkit

from . import (
    btw,  # v2.20 (2026-07-17) - 一次性独立 LLM 请求(顺便问问)
    code_check,  # 2026-08-12 — POST code-check
    code_format,  # 2026-08-12 — POST code-format
    codegraph_set,  # 2026-08-06 静默切换 codegraph 项目 (POST /spcode/codegraph-set)
    codegraph_status,  # v2.14.x (2026-06-28)
    docs_crud,  # spec B (2026-07-11): POST/PATCH/DELETE /spcode/docs
    file_binary,  # 2026-07-22: GET /spcode/file-binary(原始字节,供 BinaryPreview)
    file_browser,
    file_discard_hunk,  # v2.16.0 (2026-07-06)
    file_name_search,  # v2.15.0 (2026-07-02)
    file_remove,  # 2026-07-18: POST /spcode/file-remove(删除文件)
    file_rename,  # 2026-07-18: POST /spcode/file-rename(同目录重命名)
    file_restore,
    file_search,  # v2.15.0 (2026-07-02)
    file_write,  # 2026-07-17: POST /spcode/file-write(通用文本覆写)
    git_branch_create,  # v2.17.0 (2026-07-16) — PR-D POST endpoint
    git_branch_delete,  # v2.17.0 (2026-07-16) — PR-E POST endpoint
    git_branch_switch,  # v2.17.0 (2026-07-16) — PR-F POST endpoint
    git_branches,  # v2.17.0 (2026-07-16) — PR-C GET endpoint
    git_cherry_pick,  # v2.22.0 (2026-07-28)
    git_commit,
    git_commit_amend,  # 2026-08-13 — POST git-commit-amend
    git_conflict_abort,  # v2.22.0 (2026-07-28)
    git_conflict_continue,  # v2.22.0 (2026-07-28)
    git_conflict_resolve,  # v2.22.0 (2026-07-28)
    git_conflict_status,  # v2.22.0 (2026-07-28)
    git_diff,
    git_file,  # spec B (2026-07-11): GET /spcode/git-file
    git_init,  # v2.17.0 (2026-07-16) - PR-B POST endpoint
    git_log,
    git_merge,  # v2.22.0 (2026-07-28)
    git_pull,  # 2026-08-12 — POST git-pull
    git_push,  # 2026-08-12 — POST git-push
    git_remote_set_url,  # 2026-08-12 — POST git-remote-set-url
    git_repo_check,  # v2.18.0 (2026-07-16) - GET git 仓库探测
    git_revert,  # v2.17.0 (2026-07-16) - PR-G POST endpoint
    git_show,
    git_squash,  # 2026-08-03 - POST git-squash (HEAD 锚定连续压缩)
    git_stage,
    git_stats,
    git_status,
    git_unstage,
    git_worktree_add,  # v2.14.0 (2026-06-26)
    git_worktree_lock,  # v2.14.0 (2026-06-26)
    git_worktree_remove,  # v2.14.0 (2026-06-26)
    git_worktree_unlock,  # v2.14.0 (2026-06-26)
    git_worktrees,
    operation_progress,  # 2026-08-06 静默操作实时进度 (GET /spcode/operation-progress)
    plan_mode,
    project_load,  # 2026-07-28 静默加载项目 (POST /spcode/project-load)
    project_status,
    project_unload,  # 2026-08-06 静默卸载项目 (POST /spcode/project-unload)
    vivado_status,  # PR-4 2026-07-23
)

logger = logging.getLogger(__name__)

# (route, methods, handler, desc) — registration order is stable
ROUTES: list[tuple[str, list[str], Callable, str]] = [
    (
        "/spcode/project-status",
        ["GET"],
        project_status.handle,
        "获取 spcode 当前会话已加载的项目信息(供 dashboard 调用)",
    ),
    (
        "/spcode/project-load",  # 2026-07-28 静默加载项目
        ["POST"],
        project_load.handle,
        "静默加载项目(dashboard 调用,不在聊天框 yield 用户消息;仿照 plan-mode 切换模式)",
    ),
    (
        "/spcode/project-unload",  # 2026-08-06 静默卸载项目
        ["POST"],
        project_unload.handle,
        "静默卸载当前会话已加载的项目(供 dashboard 调用)",
    ),
    (
        "/spcode/codegraph-set",  # 2026-08-06 静默切换 codegraph 默认项目
        ["POST"],
        codegraph_set.handle,
        "静默切换 codegraph 默认项目(供 dashboard 调用)",
    ),
    (
        "/spcode/operation-progress",  # 2026-08-06 静默操作实时进度
        ["GET"],
        operation_progress.handle,
        "查询静默操作实时进度(供 dashboard 轮询)",
    ),
    (
        "/spcode/plan-mode",
        ["GET"],
        plan_mode.handle,
        "获取 spcode 当前 plan-mode 状态(只读)",
    ),
    (
        "/spcode/plan-mode",  # v2.22.0 (2026-07-27)
        ["POST"],
        plan_mode.handle_set,
        "切换当前会话 plan/build 模式(dashboard chip 直调,不发聊天消息)",
    ),
    (
        "/spcode/git-worktrees",
        ["GET"],
        git_worktrees.handle,
        "列出已加载项目的 git worktree 列表",
    ),
    (
        "/spcode/git-diff",
        ["GET"],
        git_diff.handle,
        "获取已加载项目的未暂存 git diff",
    ),
    (
        "/spcode/git-status",  # v2.13 (2026-06-24)
        ["GET"],
        git_status.handle,
        "获取已加载项目的 git 工作区状态(branch/upstream/staged/unstaged/untracked)",
    ),
    (
        "/spcode/git-log",
        ["GET"],
        git_log.handle,
        "获取已加载项目的 git 历史(8 字段标准粒度)",
    ),
    (
        "/spcode/git-branches",  # v2.17.0 (2026-07-16) — PR-C
        ["GET"],
        git_branches.handle,
        "列出已加载项目的本地和远程分支",
    ),
    (
        "/spcode/git-branch-create",  # v2.17.0 (2026-07-16) — PR-D
        ["POST"],
        git_branch_create.handle,
        "git branch <name> (从当前 HEAD 拉新分支)",
    ),
    (
        "/spcode/git-branch-delete",  # v2.17.0 (2026-07-16) — PR-E
        ["POST"],
        git_branch_delete.handle,
        "git branch -d/-D (硬禁 current branch)",
    ),
    (
        "/spcode/git-branch-switch",  # v2.17.0 (2026-07-16) — PR-F
        ["POST"],
        git_branch_switch.handle,
        "git switch <name> (支持 create/detach/force 跨字段)",
    ),
    (
        "/spcode/git-revert",  # v2.17.0 (2026-07-16) — PR-G
        ["POST"],
        git_revert.handle,
        "git revert <ref> --no-edit (自动生成回滚 commit)",
    ),
    (
        "/spcode/git-squash",  # 2026-08-03
        ["POST"],
        git_squash.handle,
        "git reset --soft <oldest>^ + commit (压缩 HEAD 起连续 N 条提交)",
    ),
    (
        "/spcode/git-repo-check",  # v2.18.0 (2026-07-16)
        ["GET"],
        git_repo_check.handle,
        "判断已加载项目是否为 git 仓库(解耦自 git-branches preflight 第 5 步)",
    ),
    (
        "/spcode/git-show",  # v3.8 (2026-06-25)
        ["GET"],
        git_show.handle,
        "查看给定 ref 修改的文件列表 (name-status + numstat)",
    ),
    (
        "/spcode/git-stats",  # v2.21 (2026-07-18)
        ["GET"],
        git_stats.handle,
        "获取已加载项目的变更统计(按日聚合 + 热点文件,供 stats 面板调用)",
    ),
    (
        "/spcode/git-init",  # v2.17.0 (2026-07-15)
        ["POST"],
        git_init.handle,
        "在已存在空目录上 git init(独立 preflight,无 umo 解析)",
    ),
    (
        "/spcode/git-stage",
        ["POST"],
        git_stage.handle,
        "git add(指定文件 or all,互斥)",
    ),
    (
        "/spcode/git-unstage",
        ["POST"],
        git_unstage.handle,
        "git reset HEAD(指定文件 or all,互斥)",
    ),
    (
        "/spcode/git-commit",
        ["POST"],
        git_commit.handle,
        "git commit(严格最小,仅 message)",
    ),
    (
        "/spcode/git-commit-amend",  # 2026-08-13
        ["POST"],
        git_commit_amend.handle,
        "修改当前 HEAD 提交信息（拒绝 staged / merge commit / 冲突中）",
    ),
    (
        "/spcode/file-browser",
        ["GET"],
        file_browser.handle,
        "读取文件内容或列出单层目录",
    ),
    (
        "/spcode/file-binary",  # 2026-07-22: 原始字节流(供 BinaryPreview)
        ["GET"],
        file_binary.handle,
        "读取白名单文件(PDF/DOCX/XLSX/CSV/MD)的原始字节,支持 ?ref= 历史版本",
    ),
    (
        "/spcode/file-search",  # v2.15.0 (2026-07-02)
        ["POST"],
        file_search.handle,
        "在已加载项目(指定 worktree)内按内容搜索文件",
    ),
    (
        "/spcode/file-name-search",  # v2.15.0 (2026-07-02)
        ["POST"],
        file_name_search.handle,
        "在已加载项目(指定 worktree)内按文件名(basename)匹配文件",
    ),
    (
        "/spcode/file-restore",
        ["POST"],
        file_restore.handle,
        "恢复工作区中某一文件相对 index 的改动",
    ),
    (
        "/spcode/file-discard-hunk",  # v2.16.0 (2026-07-06)
        ["POST"],
        file_discard_hunk.handle,
        "按 hunk 丢弃工作区改动(unified diff 文本入参,git apply --reverse)",
    ),
    (
        "/spcode/git-worktree-add",  # v2.14.0 (2026-06-26)
        ["POST"],
        git_worktree_add.handle,
        "创建 git worktree(git CLI 旗标平铺)",
    ),
    (
        "/spcode/git-worktree-remove",  # v2.14.0 (2026-06-26)
        ["POST"],
        git_worktree_remove.handle,
        "删除 git worktree (硬禁 main,locked 拒,force=true 跳过 dirty)",
    ),
    (
        "/spcode/git-worktree-lock",  # v2.14.0 (2026-06-26)
        ["POST"],
        git_worktree_lock.handle,
        "锁定 git worktree(可选 --reason),main 允许但 git 自身拒绝",
    ),
    (
        "/spcode/git-worktree-unlock",  # v2.14.0 (2026-06-26)
        ["POST"],
        git_worktree_unlock.handle,
        "解锁 git worktree,main 允许但 git 自身拒绝",
    ),
    (
        "/spcode/codegraph-status",  # v2.14.x (2026-06-28)
        ["GET"],
        codegraph_status.handle,
        "获取 codegraph MCP 运行状态(供 dashboard 显示)",
    ),
    (
        "/spcode/btw",  # v2.20 (2026-07-17)
        ["POST"],
        btw.handle,
        "一次性独立 LLM 请求(顺便问问): 复用当前会话历史(命中 prefix cache),不回写历史,无工具,纯文本输出",
    ),
    (
        "/spcode/git-file",  # spec B (2026-07-11)
        ["GET"],
        git_file.handle,
        "获取 ref 下某文件的完整内容(blob),供 dashboard 文档管理子页面",
    ),
    (
        "/spcode/docs",  # spec B (2026-07-11) — create / upsert
        ["POST"],
        docs_crud.handle_post_docs,
        "创建或覆盖 docs 文件(写到工作区,后续走 Git Diff 提交)",
    ),
    (
        "/spcode/docs",  # spec B (2026-07-11) — rename
        ["PATCH"],
        docs_crud.handle_patch_docs,
        "重命名 docs 文件(纯文件系统 mv,git 后续识别 rename)",
    ),
    (
        "/spcode/docs",  # spec B (2026-07-11) — delete
        ["DELETE"],
        docs_crud.handle_delete_docs,
        "从工作区删除 docs 文件(unlink,不调 git rm)",
    ),
    (
        "/spcode/file-write",  # 2026-07-17 — workspace file editor
        ["POST"],
        file_write.handle,
        "保存 repo 文本文件(不限扩展名,upsert: 不存在则新建,响应带 created)",
    ),
    (
        "/spcode/file-rename",  # 2026-07-18 — workspace file editor
        ["POST"],
        file_rename.handle,
        "同目录重命名 repo 文件(不限扩展名,new_name 为纯文件名)",
    ),
    (
        "/spcode/file-remove",  # 2026-07-18 — workspace file editor
        ["POST"],
        file_remove.handle,
        "删除 repo 文件(不限扩展名,仅文件,目录拒绝)",
    ),
    (
        "/spcode/vivado-status",  # PR-4 2026-07-23
        ["GET"],
        vivado_status.handle,
        "获取 vivado MCP 运行状态快照(供 dashboard 显示)",
    ),
    (
        "/spcode/git-merge",  # v2.22.0 (2026-07-28)
        ["POST"],
        git_merge.handle,
        "git merge <source>（合并分支/tag/SHA 到当前 HEAD）",
    ),
    (
        "/spcode/git-cherry-pick",  # v2.22.0 (2026-07-28)
        ["POST"],
        git_cherry_pick.handle,
        "git cherry-pick <ref>（拣选单 commit 到当前 HEAD）",
    ),
    (
        "/spcode/git-conflict-status",  # v2.22.0 (2026-07-28)
        ["GET"],
        git_conflict_status.handle,
        "查询当前冲突状态（操作类型 + 冲突文件 + hunk 详情 + 三路原文）",
    ),
    (
        "/spcode/git-conflict-resolve",  # v2.22.0 (2026-07-28)
        ["POST"],
        git_conflict_resolve.handle,
        "解决冲突文件（按 hunk 选择 / 整文件 ours/theirs / custom content）",
    ),
    (
        "/spcode/git-conflict-continue",  # v2.22.0 (2026-07-28)
        ["POST"],
        git_conflict_continue.handle,
        "冲突全部解决后继续完成 merge/cherry-pick/revert",
    ),
    (
        "/spcode/git-conflict-abort",  # v2.22.0 (2026-07-28)
        ["POST"],
        git_conflict_abort.handle,
        "中止当前 merge/cherry-pick/revert 操作",
    ),
    (
        "/spcode/git-pull",  # 2026-08-12
        ["POST"],
        git_pull.handle,
        "git pull --no-edit（支持 ff_only / rebase，禁交互认证）",
    ),
    (
        "/spcode/git-push",  # 2026-08-12
        ["POST"],
        git_push.handle,
        "git push（无 upstream 时自动 -u 设置 upstream）",
    ),
    (
        "/spcode/git-remote-set-url",  # 2026-08-12
        ["POST"],
        git_remote_set_url.handle,
        "upsert git remote URL（存在则 set-url，不存在则 add）",
    ),
    (
        "/spcode/code-check",  # 2026-08-12
        ["POST"],
        code_check.handle,
        "对 repo 内单文件执行 code_check（ruff/cpplint/cppcheck）",
    ),
    (
        "/spcode/code-format",  # 2026-08-12
        ["POST"],
        code_format.handle,
        "对 repo 内单文件执行 code_format（默认写回，check=true 预览）",
    ),
]

# 旧方法名 -> 新模块级 handler (for back-compat / introspection)
HANDLERS: dict[str, Callable] = {
    "handle_get_project_status": project_status.handle,
    "handle_post_project_load": project_load.handle,  # 2026-07-28 静默加载
    "handle_get_plan_mode": plan_mode.handle,
    "handle_post_plan_mode": plan_mode.handle_set,  # v2.22.0 (2026-07-27)
    "handle_get_git_worktrees": git_worktrees.handle,
    "handle_get_git_diff": git_diff.handle,
    "handle_get_git_status": git_status.handle,  # v2.13 (2026-06-24)
    "handle_get_git_branches": git_branches.handle,  # v2.17.0 (2026-07-16)
    "handle_post_git_branch_create": git_branch_create.handle,  # v2.17.0 (2026-07-16)
    "handle_post_git_branch_delete": git_branch_delete.handle,  # v2.17.0 (2026-07-16)
    "handle_post_git_branch_switch": git_branch_switch.handle,  # v2.17.0 (2026-07-16)
    "handle_post_git_revert": git_revert.handle,  # v2.17.0 (2026-07-16)
    "handle_post_git_squash": git_squash.handle,  # 2026-08-03
    "handle_get_git_repo_check": git_repo_check.handle,  # v2.18.0 (2026-07-16)
    "handle_get_git_log": git_log.handle,
    "handle_get_git_show": git_show.handle,  # v3.8 (2026-06-25)
    "handle_get_file_browser": file_browser.handle,
    "handle_post_file_discard_hunk": file_discard_hunk.handle,
    "handle_post_file_search": file_search.handle,  # v2.15.0 (2026-07-02)
    "handle_post_file_name_search": file_name_search.handle,  # v2.15.0 (2026-07-02)
    "handle_post_file_restore": file_restore.handle,
    "handle_post_git_stage": git_stage.handle,
    "handle_post_git_unstage": git_unstage.handle,
    "handle_post_git_commit": git_commit.handle,
    "handle_post_git_commit_amend": git_commit_amend.handle,  # 2026-08-13
    "handle_post_git_init": git_init.handle,  # v2.17.0 (2026-07-15)
    "handle_post_git_worktree_add": git_worktree_add.handle,  # v2.14.0 (2026-06-26)
    "handle_post_git_worktree_lock": git_worktree_lock.handle,  # v2.14.0 (2026-06-26)
    "handle_post_git_worktree_remove": git_worktree_remove.handle,  # v2.14.0 (2026-06-26)
    "handle_post_git_worktree_unlock": git_worktree_unlock.handle,  # v2.14.0 (2026-06-26)
    "handle_get_codegraph_status": codegraph_status.handle,  # v2.14.x (2026-06-28)
    "handle_post_btw": btw.handle,  # v2.20 (2026-07-17)
    "handle_get_git_file": git_file.handle,  # spec B (2026-07-11)
    "handle_post_docs": docs_crud.handle_post_docs,  # spec B (2026-07-11)
    "handle_patch_docs": docs_crud.handle_patch_docs,  # spec B (2026-07-11)
    "handle_delete_docs": docs_crud.handle_delete_docs,  # spec B (2026-07-11)
    "handle_post_file_write": file_write.handle,  # 2026-07-17
    "handle_post_file_rename": file_rename.handle,  # 2026-07-18
    "handle_post_file_remove": file_remove.handle,  # 2026-07-18
    "handle_get_vivado_status": vivado_status.handle,  # PR-4 2026-07-23
    "handle_post_git_merge": git_merge.handle,  # v2.22.0 (2026-07-28)
    "handle_post_git_cherry_pick": git_cherry_pick.handle,  # v2.22.0 (2026-07-28)
    "handle_get_git_conflict_status": git_conflict_status.handle,  # v2.22.0 (2026-07-28)
    "handle_post_git_conflict_resolve": git_conflict_resolve.handle,  # v2.22.0 (2026-07-28)
    "handle_post_git_conflict_continue": git_conflict_continue.handle,  # v2.22.0 (2026-07-28)
    "handle_post_git_conflict_abort": git_conflict_abort.handle,  # v2.22.0 (2026-07-28)
    "handle_post_git_pull": git_pull.handle,  # 2026-08-12
    "handle_post_git_push": git_push.handle,  # 2026-08-12
    "handle_post_git_remote_set_url": git_remote_set_url.handle,  # 2026-08-12
    "handle_post_code_check": code_check.handle,  # 2026-08-12
    "handle_post_code_format": code_format.handle,  # 2026-08-12
}


def _wrap(handler: Callable, plugin: SPCodeToolkit) -> Callable:
    """Adapt a handler to the ``view_handler`` interface.

    Reads request fields from ``astrbot.api.web.request`` — the
    Quart-style proxy bound by AstrBot's ``bind_request_context`` for
    the lifetime of the request.  Individual endpoint modules can
    declare keyword-only parameters and the adapter forwards matching
    values from the request:

    * ``umo``             — GET  ``query.umo``  /  POST ``body.umo``
    * ``worktree``        — GET  ``query.worktree``  /  POST ``body.worktree``
    * ``scope``           — GET  ``query.scope`` (default ``"unstaged"``)
    * ``path``            — GET  ``query.path`` (default ``""``)
    * ``if_none_match``   — GET  ``headers.If-None-Match``
    * ``body``            — POST parsed JSON body (as dict)

    The wrapped callable accepts arbitrary positional/keyword args but
    ignores them — the framework invokes it as
    ``view_func(**path_values)`` (no positional request).  The
    plugin is closed over so the adapter stays a regular ``async def``.

    Note:
        v3.7.1 — Previously the adapter tried to pull a FastAPI
        ``Request`` from ``args[0]`` / ``kwargs["request"]``.  The
        framework never passes one (it binds the request via
        ``bind_request_context``), so ``request`` was always ``None``
        and every POST handler received ``body=None`` → ``invalid_body``.
        Switched to ``web.request`` so the adapter works regardless of
        how the caller invokes the wrapper.
    """
    sig = inspect.signature(handler)
    accepts = set(sig.parameters) - {"plugin"}

    async def view(*_args: Any, **_kwargs: Any) -> Any:
        # Late import: ``astrbot.api.web`` requires the plugin runtime
        # to be initialised.  Importing at module scope would break
        # unit tests that mock ``web.request`` before ``register_webapi_routes``
        # is called.
        from astrbot.api import web

        is_post = web.request.method == "POST"
        call_kwargs: dict[str, Any] = {}

        # Parse the JSON body once for POST requests — used by umo,
        # worktree and body injection below.
        body: dict = {}
        if is_post and (accepts & {"umo", "worktree", "body"}):
            body = (await web.request.json(default={})) or {}

        if "umo" in accepts:
            if is_post:
                call_kwargs["umo"] = body.get("umo")
            else:
                call_kwargs["umo"] = web.request.query.get("umo") or None

        if "worktree" in accepts:
            if is_post:
                call_kwargs["worktree"] = body.get("worktree")
            else:
                call_kwargs["worktree"] = web.request.query.get("worktree")

        if "scope" in accepts:
            call_kwargs["scope"] = web.request.query.get("scope") or "unstaged"

        if "path" in accepts:
            call_kwargs["path"] = web.request.query.get("path", "").strip()

        if "if_none_match" in accepts:
            call_kwargs["if_none_match"] = web.request.headers.get("if-none-match")

        if "body" in accepts:
            call_kwargs["body"] = body

        return await handler(plugin, **call_kwargs)

    return view


def register_webapi_routes(plugin: SPCodeToolkit) -> None:
    """Register all 53 ``/spcode/*`` routes against ``plugin.context``.

    Called once from ``main.py.initialize()``.  Failures are logged
    but never raised — a single broken endpoint should not block
    plugin load.

    端点数演进:
    v2.22.0 (2026-07-27): 39 -> 45 (+plan-mode POST 1 + git-merge/cherry-pick/
                          conflict 系列 5 端点)
    2026-07-28: 45 -> 46 (+1 POST /spcode/project-load 静默加载项目)
    2026-08-06: 46 -> 50 (+project-unload +codegraph-set +operation-progress;
                对账补登此前漏记的 conflict 系列实际为 47)
    2026-08-12: 50 -> 53 (+git-pull +git-push +git-remote-set-url)
    2026-08-12: 53 -> 55 (+code-check +code-format)
    2026-08-13: 55 -> 56 (+git-commit-amend)
    """
    for route, methods, handler, desc in ROUTES:
        try:
            plugin.context.register_web_api(
                route=route,
                view_handler=_wrap(handler, plugin),
                methods=methods,
                desc=desc,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("注册 spcode %s web API 失败: %s", route, exc)


__all__ = [
    "ROUTES",
    "HANDLERS",
    "_wrap",
    "register_webapi_routes",
    "btw",  # v2.20 (2026-07-17)
    "code_check",  # 2026-08-12
    "code_format",  # 2026-08-12
    "codegraph_status",  # v2.14.x (2026-06-28)
    "docs_crud",  # spec B (2026-07-11)
    "file_browser",
    "file_binary",  # 2026-07-22: 原始字节流(供 BinaryPreview)
    "file_discard_hunk",  # v2.16.0 (2026-07-06)
    "file_name_search",  # v2.15.0 (2026-07-02)
    "file_remove",  # 2026-07-18
    "file_rename",  # 2026-07-18
    "file_restore",
    "file_search",  # v2.15.0 (2026-07-02)
    "file_write",  # 2026-07-17
    "git_branch_create",  # v2.17.0
    "git_branch_delete",  # v2.17.0
    "git_branch_switch",  # v2.17.0
    "git_branches",  # v2.17.0
    "git_commit",
    "git_commit_amend",  # 2026-08-13
    "git_diff",
    "git_file",  # spec B (2026-07-11)
    "git_init",  # v2.17.0
    "git_log",
    "git_pull",  # 2026-08-12
    "git_push",  # 2026-08-12
    "git_remote_set_url",  # 2026-08-12
    "git_repo_check",  # v2.18.0
    "git_revert",  # v2.17.0
    "git_show",
    "git_squash",  # 2026-08-03
    "git_stage",
    "git_stats",
    "git_status",
    "git_unstage",
    "git_worktree_add",  # v2.14.0
    "git_worktree_lock",  # v2.14.0
    "git_worktree_remove",  # v2.14.0
    "git_worktree_unlock",  # v2.14.0
    "git_worktrees",
    "plan_mode",
    "project_load",  # 2026-07-28 静默加载
    "project_status",
    "vivado_status",  # PR-4 2026-07-23
]
