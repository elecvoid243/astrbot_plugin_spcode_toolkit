# /spcode/file-restore Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 spcode 工具箱中新增 `POST /spcode/file-restore` 端点,接受前端传入的文件路径,调用 `git checkout -- <file>` 撤销该文件的工作区改动,与既有 `GET /spcode/git-diff` 端点形成完整闭环。

**Architecture:**
- **入口**:`main.py` 中新增 `handle_post_file_restore` 方法(对称于既有 `handle_get_git_diff`),并在 `initialize()` 内通过 `register_web_api` 注册为 `POST` 路由
- **响应骨架**:新增到 `tools/_config.py` 的 `make_file_restore_empty_envelope()` / `make_file_restore_success_envelope()` 工厂(对称于既有 `make_git_diff_empty_envelope`)
- **路径安全**:新增 `_validate_restore_file()` 4 步防御(在 main.py 内部 helper;与 `tools/_helpers._validate_worktree_param` 互补)
- **测试**:新增 `tests/test_file_restore.py`,沿用 `tests/test_git_diff.py` 的 fixture 模式(真 git + tmp_path)

**Tech Stack:** Python 3.10+, `asyncio.create_subprocess_exec`(既有 `_run_git_async`),`subprocess`(既有 `run_sync` 风格),pytest + pytest-asyncio(沿用 conftest fixtures),ruff(沿用既有配置)

**参考规范:** `docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md`

---

## 文件结构

| 文件 | 类型 | 职责 |
|------|------|------|
| `main.py` | 修改 | 新增 `handle_post_file_restore` 方法(POST handler)+ `_validate_restore_file` 内部 helper;`initialize()` 内新增 `register_web_api` 块 |
| `tools/_config.py` | 修改 | 新增 `make_file_restore_empty_envelope()` + `make_file_restore_success_envelope()` 工厂 |
| `tests/test_file_restore.py` | 新增 | 26 个测试用例 + 4 个 fixture helper |
| `docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md` | 已存在 | brainstorming 阶段产出 |
| `docs/superpowers/plans/2026-06-22-file-restore-endpoint.md` | 本文件 | implementation plan |

> 不修改 `_conf_schema.json` / `metadata.yaml` / `README.md`(本端点无新配置项 / 元数据)

---

## Chunk 1: 响应骨架工厂(Tools 层)

### Task 1.1: 在 `tools/_config.py` 新增 `make_file_restore_empty_envelope` 与 `make_file_restore_success_envelope`

**Files:**
- Modify: `tools/_config.py:120` 之后(在 `make_git_diff_empty_envelope` 之后)

- [ ] **Step 1: 编写失败测试**

新建 `tests/test_file_restore_envelope.py`(临时,后续并入 test_file_restore.py):

```python
from astrbot_plugin_spcode_toolkit.tools._config import (
    make_file_restore_empty_envelope,
    make_file_restore_success_envelope,
)


def test_empty_envelope_shape():
    env = make_file_restore_empty_envelope(reason="no_project_loaded")
    assert env["status"] == "ok"
    assert env["data"]["restored"] is False
    assert env["data"]["reason"] == "no_project_loaded"
    assert env["data"]["scope"] == "unstaged"
    assert env["data"]["elapsed_ms"] == 0


def test_success_envelope_shape():
    env = make_file_restore_success_envelope(
        umo="u:m", file="main.py", directory="/tmp/repo", elapsed_ms=42
    )
    assert env["status"] == "ok"
    assert env["data"]["restored"] is True
    assert env["data"]["reason"] is None
    assert env["data"]["file"] == "main.py"
    assert env["data"]["worktree"] == "/tmp/repo"
    assert env["data"]["elapsed_ms"] == 42
```

- [ ] **Step 2: 运行测试,验证失败**

Run: `python -m pytest tests/test_file_restore_envelope.py -v`
Expected: `ImportError: cannot import name 'make_file_restore_empty_envelope'`

- [ ] **Step 3: 实现 `make_file_restore_empty_envelope` 与 `make_file_restore_success_envelope`**

在 `tools/_config.py` 末尾(在 `make_git_worktrees_empty_envelope` 之后)追加:

```python
def make_file_restore_empty_envelope(
    *,
    umo: str | None = None,
    file: str = "",
    directory: str | None = None,
    worktree: str | None = None,
    scope: str = "unstaged",
    reason: str,
    stderr: str = "",
    elapsed_ms: int = 0,
) -> dict:
    """构造 /spcode/file-restore 失败路径的响应骨架。

    Spec: docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md §8
    """
    return {
        "status": "ok",
        "data": {
            "restored": False,
            "directory": directory,
            "umo": umo,
            "worktree": worktree or directory,
            "file": file,
            "scope": scope,
            "elapsed_ms": elapsed_ms,
            "stderr": stderr,
            "reason": reason,
        },
    }


def make_file_restore_success_envelope(
    *,
    umo: str | None,
    file: str,
    directory: str,
    elapsed_ms: int,
) -> dict:
    """构造 /spcode/file-restore 成功路径的响应骨架。"""
    return {
        "status": "ok",
        "data": {
            "restored": True,
            "directory": directory,
            "umo": umo,
            "worktree": directory,
            "file": file,
            "scope": "unstaged",
            "elapsed_ms": elapsed_ms,
            "stderr": "",
            "reason": None,
        },
    }
```

- [ ] **Step 4: 运行测试,验证通过**

Run: `python -m pytest tests/test_file_restore_envelope.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add tools/_config.py tests/test_file_restore_envelope.py
git commit -m "feat(file-restore): add response envelope factories (TDD step 1)"
```

- [ ] **Step 6: 合并临时测试文件到 test_file_restore.py 的 `_envelope` 章节**

(为避免临时文件残留,下一步在 Chunk 2 中统一把 envelope 测试并入 test_file_restore.py,然后删除此临时文件)

---

## Chunk 2: Handler 主体(Main 层 + Tests)

### Task 2.1: TDD - 第一个失败测试(handler 存在 + invalid_body)

**Files:**
- Create: `tests/test_file_restore.py`

- [ ] **Step 1: 创建 `tests/test_file_restore.py` 顶部 imports + fixtures + 第一个测试**

```python
"""Tests for the POST /spcode/file-restore HTTP endpoint.

Spec: docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md
Author: elecvoid243 @ 2026-06-22

Real git is used (no subprocess mocking) via tmp_path fixtures. The handler
must be exercised through a constructed SPCodeToolkit instance using the
``plugin`` fixture (from conftest).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# 启用 pytest-asyncio strict 模式
pytestmark = pytest.mark.asyncio

# sys.path setup(与 tests/test_git_diff.py 相同)
_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
_PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402
from tests.conftest import _make_plugin  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit


# ── Fixtures & helpers ───────────────────────────────────

@pytest.fixture
def plugin():
    """Per-test plugin instance."""
    return _make_plugin()


def _init_git_repo(path: Path) -> None:
    """Initialize a minimal git repo in tmp_path with one initial commit."""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=path, check=True)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    """Inject a project into plugin._loaded_projects bypassing /project load."""
    plugin._loaded_projects[umo] = {
        "directory": str(directory),
        "loaded_at": time.time(),
    }


def _patch_post_body(monkeypatch, body: dict | None) -> None:
    """Mock astrbot.api.web.request.get_json() returning body."""
    from astrbot.api import web
    mock = MagicMock()
    mock.get_json = MagicMock(return_value=body)
    monkeypatch.setattr(web, "request", mock)


# ── T1: handler exists + invalid_body ────────────────────

async def test_restore_invalid_body_returns_invalid_body(plugin, monkeypatch):
    """body 不是 dict 时返回 invalid_body(无需 git 调用)。"""
    _patch_post_body(monkeypatch, body="not a dict")
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "invalid_body"
    assert data["file"] == ""


async def test_restore_missing_file_field_returns_missing_file(plugin, monkeypatch):
    """body 缺 file 字段时返回 missing_file。"""
    _patch_post_body(monkeypatch, body={"umo": "u:m"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "missing_file"


async def test_restore_empty_file_returns_missing_file(plugin, monkeypatch):
    """body file 字段是空字符串或仅空白时返回 missing_file。"""
    _patch_post_body(monkeypatch, body={"file": "   "})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "missing_file"
```

- [ ] **Step 2: 运行测试,验证失败**

Run: `python -m pytest tests/test_file_restore.py -v`
Expected: `AttributeError: 'SPCodeToolkit' object has no attribute 'handle_post_file_restore'`

- [ ] **Step 3: 实现最小 handler 骨架(仅 invalid_body + missing_file 分支)**

在 `main.py` 中 `_make_git_worktrees_empty_envelope` 函数之后新增一个 import + handler:

```python
# 在文件顶部 import 区,与其他 envelope 工厂并列
from .tools._config import (
    DEFAULT_CONFIG,
    record,
    MAX_GIT_DIFF_BYTES,
    _GIT_DIFF_ENCODING,
    make_git_diff_empty_envelope,
    make_git_worktrees_empty_envelope,
    make_file_restore_empty_envelope,    # 新增
    make_file_restore_success_envelope,  # 新增
)

# ── /spcode/file-restore 端点(POST) ─────────────────────
# Spec: docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md


async def handle_post_file_restore(self) -> dict:
    """Web API handler for ``POST /spcode/file-restore``.

    恢复工作区中某一文件相对于 index 的改动(``git checkout -- <file>``)。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    from astrbot.api import web

    # 1. 读取 body
    try:
        body = web.request.get_json(silent=True)
    except Exception:
        body = None
    if not isinstance(body, dict):
        return make_file_restore_empty_envelope(
            reason="invalid_body", elapsed_ms=_elapsed()
        )

    # 2. 提取 file 字段
    file_field = body.get("file", "")
    if not isinstance(file_field, str) or not file_field.strip():
        return make_file_restore_empty_envelope(
            reason="missing_file", elapsed_ms=_elapsed()
        )
    file_path = file_field.strip()

    # 3. 提取 umo / worktree(留接口位;后续 task 接入)
    umo = body.get("umo") or None
    worktree_param = body.get("worktree")
    if not isinstance(worktree_param, str):
        worktree_param = None

    # TODO: feature flag + umo 解析 + worktree 校验 + git 写操作(后续 tasks)
    return make_file_restore_empty_envelope(
        umo=umo, file=file_path, reason="feature_disabled",
        elapsed_ms=_elapsed()
    )
```

- [ ] **Step 4: 运行测试,验证通过**

Run: `python -m pytest tests/test_file_restore.py::test_restore_invalid_body_returns_invalid_body tests/test_file_restore.py::test_restore_missing_file_field_returns_missing_file tests/test_file_restore.py::test_restore_empty_file_returns_missing_file -v`
Expected: 3 passed

- [ ] **Step 5: 删除临时 envelope 测试文件 `tests/test_file_restore_envelope.py`(若已创建)**

(此步留给后续 chunk 完成后执行,本 task 中暂不创建临时文件)

- [ ] **Step 6: 提交**

```bash
git add main.py tests/test_file_restore.py tools/_config.py
git commit -m "feat(file-restore): POST handler skeleton + invalid_body/missing_file paths (TDD)"
```

---

### Task 2.2: TDD - feature_disabled + no_project_loaded + directory_missing + not_a_git_repo

**Files:**
- Modify: `tests/test_file_restore.py`(追加测试)
- Modify: `main.py`(扩展 handler)

- [ ] **Step 1: 追加失败测试**

在 `tests/test_file_restore.py` 末尾追加:

```python
# ── T2: gating ──────────────────────────────────────────

async def test_restore_no_project_loaded_returns_no_project_loaded(plugin, monkeypatch):
    """_loaded_projects 空时返回 no_project_loaded。"""
    _patch_post_body(monkeypatch, body={"file": "main.py"})
    plugin._loaded_projects.clear()
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "no_project_loaded"
    assert data["file"] == "main.py"


async def test_restore_feature_disabled_agentsmd(plugin, monkeypatch):
    """agentsmd_enabled=False 时返回 feature_disabled。"""
    _patch_post_body(monkeypatch, body={"file": "main.py"})
    plugin._config["agentsmd_enabled"] = False
    plugin._loaded_projects.clear()
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "feature_disabled"


async def test_restore_feature_disabled_codegraph(plugin, monkeypatch):
    """codegraph_enabled=False 时返回 feature_disabled。"""
    _patch_post_body(monkeypatch, body={"file": "main.py"})
    plugin._config["codegraph_enabled"] = False
    plugin._loaded_projects.clear()
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "feature_disabled"


async def test_restore_directory_missing(plugin, monkeypatch):
    """loaded 目录已被删除时返回 directory_missing。"""
    _patch_post_body(monkeypatch, body={"file": "main.py"})
    plugin._loaded_projects.clear()
    plugin._loaded_projects["u:m"] = {
        "directory": str(Path("/nonexistent/please/ignore/12345")),
        "loaded_at": time.time(),
    }
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "directory_missing"


async def test_restore_not_a_git_repo(plugin, tmp_path, monkeypatch):
    """加载的目录不是 git 仓库时返回 not_a_git_repo。"""
    _patch_post_body(monkeypatch, body={"file": "main.py"})
    not_repo = tmp_path / "not_a_repo"
    not_repo.mkdir()
    plugin._loaded_projects["u:m"] = {
        "directory": str(not_repo),
        "loaded_at": time.time(),
    }
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "not_a_git_repo"
```

- [ ] **Step 2: 运行测试,验证失败**

Run: `python -m pytest tests/test_file_restore.py -v -k "feature_disabled or no_project_loaded or directory_missing or not_a_git_repo"`
Expected: 5 failed(reason 错误,目前全返回 feature_disabled)

- [ ] **Step 3: 扩展 handler,实现 gating + git probe**

替换 `handle_post_file_restore` 中 `TODO` 部分,补充 feature flag、umo 解析、worktree 校验(暂用 None)、git probe 逻辑:

```python
    # 3. feature flag
    if not (
        self._config.get("agentsmd_enabled", True)
        and self._config.get("codegraph_enabled", True)
    ):
        return make_file_restore_empty_envelope(
            umo=umo, file=file_path, reason="feature_disabled",
            elapsed_ms=_elapsed()
        )

    # 4. umo 解析与回退
    if umo:
        info = self._loaded_projects.get(umo)
    else:
        if not self._loaded_projects:
            info = None
        else:
            _, info = max(
                self._loaded_projects.items(),
                key=lambda kv: kv[1].get("loaded_at", 0),
            )
    if info is None:
        return make_file_restore_empty_envelope(
            umo=umo, file=file_path, reason="no_project_loaded",
            elapsed_ms=_elapsed()
        )
    directory = info.get("directory", "")

    # 5. worktree 校验(6 步防御,与 git-diff 相同)
    if worktree_param is not None and worktree_param.strip():
        validated_wt, wt_err = _validate_worktree_param(
            self._git_binary(), directory, worktree_param
        )
        if wt_err is not None:
            logger.warning(
                f"[file-restore] rejected ?worktree={worktree_param!r} "
                f"(loaded={directory!r})"
            )
            return make_file_restore_empty_envelope(
                umo=umo, file=file_path, reason=wt_err,
                directory=directory, elapsed_ms=_elapsed()
            )
        directory = validated_wt

    # 6. 目录存在性
    if not Path(directory).is_dir():
        return make_file_restore_empty_envelope(
            umo=umo, file=file_path, reason="directory_missing",
            directory=directory, elapsed_ms=_elapsed()
        )

    # 7. git repo probe
    probe = await _run_git_async(
        [self._git_binary(), "-C", directory, "rev-parse", "--is-inside-work-tree"],
        encoding="utf-8",
    )
    if not probe["ok"]:
        combined = (probe.get("stderr", "") + probe.get("error", "")).lower()
        if "not a git repository" in combined:
            return make_file_restore_empty_envelope(
                umo=umo, file=file_path, reason="not_a_git_repo",
                directory=directory, elapsed_ms=_elapsed()
            )
        if "未安装" in probe.get("error", ""):
            return make_file_restore_empty_envelope(
                umo=umo, file=file_path, reason="git_unavailable",
                directory=directory, elapsed_ms=_elapsed()
            )
        return make_file_restore_empty_envelope(
            umo=umo, file=file_path, reason="git_error",
            directory=directory,
            stderr=probe.get("stderr", "") or probe.get("error", ""),
            elapsed_ms=_elapsed()
        )

    # TODO: file 路径校验 + git checkout(后续 task)
    return make_file_restore_empty_envelope(
        umo=umo, file=file_path, directory=directory,
        reason="file_not_found",  # 占位:让现有测试通过
        elapsed_ms=_elapsed()
    )
```

- [ ] **Step 4: 运行测试,验证通过**

Run: `python -m pytest tests/test_file_restore.py -v -k "feature_disabled or no_project_loaded or directory_missing or not_a_git_repo"`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add main.py tests/test_file_restore.py
git commit -m "feat(file-restore): gating + git probe (TDD step 2)"
```

---

### Task 2.3: TDD - 路径安全校验 `_validate_restore_file`

**Files:**
- Modify: `tests/test_file_restore.py`(追加 7 个路径安全测试)
- Modify: `main.py`(新增 `_validate_restore_file` helper)

- [ ] **Step 1: 追加失败测试**

```python
# ── T3: file 路径安全 ───────────────────────────────────

async def test_restore_rejects_absolute_path(plugin, tmp_path, monkeypatch):
    """file 是绝对路径时返回 path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "/etc/passwd"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "path_unsafe"


async def test_restore_rejects_windows_absolute_path(plugin, tmp_path, monkeypatch):
    """file 是 Windows 盘符绝对路径时返回 path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "C:\\Windows\\system.ini"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "path_unsafe"


async def test_restore_rejects_parent_traversal(plugin, tmp_path, monkeypatch):
    """file 含 .. 段时返回 path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "../foo.py"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "path_unsafe"


async def test_restore_rejects_dot_git_path(plugin, tmp_path, monkeypatch):
    """file 指向 .git 内部时返回 path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": ".git/config"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "path_unsafe"


async def test_restore_rejects_backslash_path(plugin, tmp_path, monkeypatch):
    """file 含反斜杠(Windows 风格)时返回 path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "src\\foo.py"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "path_unsafe"


async def test_restore_rejects_symlink_escape(plugin, tmp_path, monkeypatch):
    """file 是 symlink 且指向 worktree 外时返回 path_unsafe。"""
    _init_git_repo(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(str(outside), tmp_path / "escape_link")
    subprocess.run(["git", "add", "-N", "escape_link"], cwd=tmp_path, check=True)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "escape_link"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "path_unsafe"


async def test_restore_file_not_found(plugin, tmp_path, monkeypatch):
    """file 解析后路径不存在时返回 file_not_found。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "does_not_exist.py"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "file_not_found"
```

- [ ] **Step 2: 运行测试,验证失败**

Run: `python -m pytest tests/test_file_restore.py -v -k "rejects or file_not_found"`
Expected: 7 failed(全部返回 file_not_found,而不是预期的 path_unsafe)

- [ ] **Step 3: 实现 `_validate_restore_file` helper(在 main.py 模块级)**

放在 `_validate_worktree_param` import 之后(实际不在同文件,而是同模块 — 紧邻 `_make_xxx_envelope` 系列函数):

```python
def _validate_restore_file(
    file_path: str, worktree: Path,
) -> tuple[Path | None, str | None]:
    """4-step defense for the ``file`` field of /spcode/file-restore.

    Returns ``(resolved_path, None)`` on success; ``(None, "path_unsafe")`` on
    rejection. Spec §5.
    """
    if not file_path:
        return None, "path_unsafe"

    # Step 1: 拒绝绝对路径与路径穿越
    if file_path.startswith("/") or file_path.startswith("\\"):
        return None, "path_unsafe"
    if "\\" in file_path:
        return None, "path_unsafe"
    if ".." in file_path.replace("\\", "/").split("/"):
        return None, "path_unsafe"

    # Step 2: resolve 到 worktree 内部
    worktree_resolved = worktree.resolve()
    target = (worktree_resolved / file_path).resolve()
    try:
        target.relative_to(worktree_resolved)
    except ValueError:
        return None, "path_unsafe"

    # Step 3: 拒绝 .git 内部
    if any(part == ".git" for part in target.parts):
        return None, "path_unsafe"

    # Step 4: symlink 防御
    real = os.path.realpath(target)
    if os.path.normcase(real) != os.path.normcase(str(target)):
        return None, "path_unsafe"

    return target, None
```

- [ ] **Step 4: 接入 handler**

替换 `handle_post_file_restore` 中 git probe 之后的 `TODO` 部分:

```python
    # 8. file 路径安全校验
    target, path_err = _validate_restore_file(file_path, Path(directory))
    if path_err is not None:
        logger.warning(
            f"[file-restore] rejected file={file_path!r} "
            f"(worktree={directory!r}): {path_err}"
        )
        return make_file_restore_empty_envelope(
            umo=umo, file=file_path, reason="path_unsafe",
            directory=directory, elapsed_ms=_elapsed()
        )

    # 9. file 存在性
    if not target.exists():
        return make_file_restore_empty_envelope(
            umo=umo, file=file_path, reason="file_not_found",
            directory=directory, elapsed_ms=_elapsed()
        )

    # TODO: git status 预检 + git checkout(下个 task)
    return make_file_restore_empty_envelope(
        umo=umo, file=file_path, directory=directory,
        reason="file_not_found",  # 占位
        elapsed_ms=_elapsed()
    )
```

- [ ] **Step 5: 运行测试,验证通过**

Run: `python -m pytest tests/test_file_restore.py -v -k "rejects or file_not_found"`
Expected: 7 passed

- [ ] **Step 6: 提交**

```bash
git add main.py tests/test_file_restore.py
git commit -m "feat(file-restore): _validate_restore_file 4-step defense (TDD step 3)"
```

---

### Task 2.4: TDD - git status 预检 + git checkout 写操作 + 成功路径

**Files:**
- Modify: `tests/test_file_restore.py`(追加 6 个测试)
- Modify: `main.py`(实现 status 预检 + git checkout)

- [ ] **Step 1: 追加失败测试**

```python
# ── T4: 预检 + 写操作 + 成功路径 ─────────────────────────

async def test_restore_not_modified_returns_not_modified(plugin, tmp_path, monkeypatch):
    """文件无改动时返回 not_modified。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "README.md"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "not_modified"


async def test_restore_untracked_file_returns_untracked(plugin, tmp_path, monkeypatch):
    """未 git add -N 的新文件返回 untracked_file。"""
    _init_git_repo(tmp_path)
    (tmp_path / "new.py").write_text("print('hi')\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "new.py"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "untracked_file"
    assert data["stderr"] != ""


async def test_restore_modifies_file_back_to_index(plugin, tmp_path, monkeypatch):
    """修改文件后 restore,内容回到 HEAD。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("modified content", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "README.md"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is True
    assert data["reason"] is None
    assert data["file"] == "README.md"
    # 文件内容真的被还原
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "init"


async def test_restore_intent_to_add_file(plugin, tmp_path, monkeypatch):
    """git add -N new.py + 写内容,restore 取消新增意图。"""
    _init_git_repo(tmp_path)
    (tmp_path / "new.py").write_text("print('hi')\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "new.py"], cwd=tmp_path, check=True)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "new.py"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is True
    # 新增意图被取消:new.py 重新变回 untracked
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "new.py" in status  # 仍被列出,因为有 content


async def test_restore_response_envelope_shape(plugin, tmp_path, monkeypatch):
    """成功响应字段齐全。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"umo": "u:m", "file": "README.md"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    # 必含字段
    for key in ("restored", "directory", "umo", "worktree", "file", "scope",
                "elapsed_ms", "stderr", "reason"):
        assert key in data, f"missing key: {key}"
    # 类型
    assert data["restored"] is True
    assert isinstance(data["directory"], str)
    assert data["scope"] == "unstaged"
    assert isinstance(data["elapsed_ms"], int)


async def test_restore_logs_audit_trail(plugin, tmp_path, monkeypatch, caplog):
    """成功路径有 INFO 级别审计日志。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"umo": "u:m", "file": "README.md"})
    with caplog.at_level("INFO"):
        await plugin.handle_post_file_restore()
    assert any("file-restore" in r.message for r in caplog.records)
```

- [ ] **Step 2: 运行测试,验证失败**

Run: `python -m pytest tests/test_file_restore.py -v -k "not_modified or untracked or modifies or intent or envelope or logs"`
Expected: 6 failed(目前最后兜底返回 file_not_found 或 path_unsafe)

- [ ] **Step 3: 实现 status 预检 + git checkout + 成功路径**

替换 `handle_post_file_restore` 中 file 存在性之后的 `TODO` 部分:

```python
    # 10. git status --porcelain 预检
    status = await _run_git_async(
        [self._git_binary(), "-C", directory, "status", "--porcelain", "--", file_path],
        encoding="utf-8",
    )
    if status["ok"]:
        porcelain = status["stdout"]
        if not porcelain.strip():
            # working tree 与 index 一致 → 无可恢复
            return make_file_restore_empty_envelope(
                umo=umo, file=file_path, reason="not_modified",
                directory=directory, elapsed_ms=_elapsed()
            )
        first = porcelain.splitlines()[0] if porcelain else ""
        if first.startswith("??") or first.startswith("!!"):
            return make_file_restore_empty_envelope(
                umo=umo, file=file_path, reason="untracked_file",
                directory=directory, stderr=porcelain,
                elapsed_ms=_elapsed()
            )

    # 11. 执行 git checkout -- <file>
    result = await _run_git_async(
        [self._git_binary(), "-C", directory, "-c", "color.ui=never",
         "checkout", "--", file_path],
        encoding="utf-8",
    )

    if not result["ok"]:
        stderr = result.get("stderr", "")
        lower = stderr.lower()
        if "did not match any file" in lower or "unknown revision" in lower:
            reason = "untracked_file"
        else:
            reason = "git_error"
        return make_file_restore_empty_envelope(
            umo=umo, file=file_path, reason=reason,
            directory=directory, stderr=stderr, elapsed_ms=_elapsed()
        )

    # 12. 成功
    logger.info(
        f"[file-restore] restored: file={file_path!r} "
        f"worktree={directory!r} umo={umo!r} elapsed_ms={_elapsed()}"
    )
    return make_file_restore_success_envelope(
        umo=umo, file=file_path, directory=directory,
        elapsed_ms=_elapsed()
    )
```

- [ ] **Step 4: 运行测试,验证通过**

Run: `python -m pytest tests/test_file_restore.py -v -k "not_modified or untracked or modifies or intent or envelope or logs"`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add main.py tests/test_file_restore.py
git commit -m "feat(file-restore): git status precheck + git checkout + audit log (TDD step 4)"
```

---

### Task 2.5: TDD - worktree 6 步防御测试 + 配置 git_path 测试

**Files:**
- Modify: `tests/test_file_restore.py`(追加 3 个测试)

- [ ] **Step 1: 追加失败测试**

```python
# ── T5: worktree 防御 + git_path 配置 ───────────────────

async def test_restore_with_worktree_param_succeeds(plugin, tmp_path, monkeypatch):
    """合法 worktree + file,restore 在 worktree 内执行。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={
        "worktree": str(tmp_path), "file": "README.md"
    })
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is True
    assert data["worktree"] == str(tmp_path)


async def test_restore_worktree_param_rejected(plugin, tmp_path, monkeypatch):
    """?worktree= 越界路径(跨 repo)时被 worktree 6 步防御拒绝。"""
    _init_git_repo(tmp_path)
    other = tmp_path / "other_repo"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={
        "worktree": str(other), "file": "README.md"
    })
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "worktree_invalid"


async def test_restore_uses_configured_git_path(plugin, tmp_path, monkeypatch):
    """git_path 配置后,handler 使用配置的 git 路径。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    # 注入一个假的 git 路径(实际不可执行);handler 应当先尝试调用,失败原因应是 git_unavailable
    # —— 但更直接的验证:看 _git_binary() 返回值
    plugin._config["git_path"] = "/custom/path/to/git.exe"
    _patch_post_body(monkeypatch, body={"file": "README.md"})
    # 该路径在测试环境通常不存在;但我们至少要断言 _git_binary() 返回配置值
    assert plugin._git_binary() == "/custom/path/to/git.exe"
```

- [ ] **Step 2: 运行测试,验证通过(前两个应已通过,第三个是直接断言)**

Run: `python -m pytest tests/test_file_restore.py -v -k "worktree_param or uses_configured_git_path"`
Expected: 3 passed(worktree_param_succeeds 与 worktree_param_rejected 已在 Task 2.2 中通过 worktree 校验逻辑;uses_configured_git_path 直接断言 `_git_binary()`)

- [ ] **Step 3: 提交**

```bash
git add tests/test_file_restore.py
git commit -m "test(file-restore): worktree 6-step + git_path config tests"
```

---

### Task 2.6: 路由注册(`initialize()` 内)

**Files:**
- Modify: `main.py`(`initialize()` 方法,紧邻 `/spcode/file-browser` 注册块)

- [ ] **Step 1: 在 `initialize()` 中追加 `register_web_api` 块**

在 `file-browser` 注册块之后追加:

```python
# v3.5: 注册 /spcode/file-restore — 供 dashboard "↩ 恢复" 按钮调用。
# 详见 docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md
try:
    self.context.register_web_api(
        route="/spcode/file-restore",
        view_handler=self.handle_post_file_restore,
        methods=["POST"],
        desc="恢复工作区中某一文件相对于 index 的改动(供 dashboard 调用)",
    )
except Exception as exc:  # pragma: no cover - defensive
    logger.warning(f"注册 spcode file-restore web API 失败: {exc!s}")
```

- [ ] **Step 2: 运行 ruff 检查**

Run: `python -m ruff check main.py tools/_config.py tests/test_file_restore.py`
Expected: 0 error

- [ ] **Step 3: 运行全量测试**

Run: `python -m pytest tests/ -q --no-header 2>&1 | python -c "import sys; lines=sys.stdin.readlines(); print(''.join(lines[-5:]))"`
Expected: 大部分 passed,少量 skipped(codegraph 依赖)

- [ ] **Step 4: 提交**

```bash
git add main.py
git commit -m "feat(file-restore): register POST /spcode/file-restore route"
```

---

### Task 2.7: 集成验证 + 全量 ruff/pytest

- [ ] **Step 1: 运行 ruff 全项目检查**

Run: `python -m ruff check .`
Expected: 0 error

- [ ] **Step 2: 运行 pytest 全量**

Run: `python -m pytest tests/ -q --no-header 2>&1 | python -c "import sys; lines=sys.stdin.readlines(); print(''.join(lines[-3:]))"`
Expected: 100+ passed, 6 skipped (codegraph 依赖), 0 failed

- [ ] **Step 3: 最终提交**

```bash
git add -A
git status  # 确认无遗留
git commit -m "chore(file-restore): final integration check" --allow-empty
```

---

## Self-Review Checklist

完成所有 task 后,逐项核对:

- [ ] `tools/_config.py` 含 `make_file_restore_empty_envelope` 与 `make_file_restore_success_envelope` 工厂
- [ ] `main.py` 含 `_validate_restore_file` helper(4 步防御)
- [ ] `main.py` 含 `handle_post_file_restore` 方法(完整 11 步流程)
- [ ] `initialize()` 内含 `register_web_api("/spcode/file-restore", ..., methods=["POST"])` 块
- [ ] `tests/test_file_restore.py` 至少 26 个测试用例,全部通过
- [ ] `ruff check .` 0 error
- [ ] `pytest tests/` 全部通过(允许既有 skip)
- [ ] 6 个 commit,每个独立可回滚
- [ ] 文档:`docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md` + 本 plan 文件均已存在
- [ ] **未修改**:`_conf_schema.json` / `metadata.yaml` / `README.md`
