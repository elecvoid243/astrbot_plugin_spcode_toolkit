"""Tests for POST /spcode/git-file-export — 历史版本导出到临时文件。

Spec: docs/superpowers/specs/2026-10-06-git-file-export-design.md
Author: elecvoid243, 2026-10-06
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _proj_state
from tools.webapi import git_file_export as _gfe

pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin() -> Any:
    return _make_plugin()


@pytest.fixture(autouse=True)
def export_root(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """把导出根指到临时目录,测试不污染真实 plugin_data。"""
    root = tmp_path_factory.mktemp("git-history-export")
    monkeypatch.setattr(_gfe, "EXPORT_ROOT_OVERRIDE", root)
    return root


def test_export_root_follows_plugin_data_convention() -> None:
    """默认导出根 = <astrbot data>/plugin_data/astrbot_plugin_spcode_toolkit/
    temp/git-history(与 todo 存储同一 StarTools.get_data_dir 真源)。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_gfe, "EXPORT_ROOT_OVERRIDE", None)
        root = _gfe._export_root()
    assert str(root).replace("\\", "/").endswith(
        "data/plugin_data/astrbot_plugin_spcode_toolkit/temp/git-history"
    )


def test_export_root_falls_back_when_star_tools_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """StarTools 不可用(standalone/异常)→ 回退插件仓库 data/temp/git-history。"""
    import astrbot.api.star as _star

    def _boom(*_a, **_k):
        raise RuntimeError("no star map")

    monkeypatch.setattr(_gfe, "EXPORT_ROOT_OVERRIDE", None)
    monkeypatch.setattr(_star.StarTools, "get_data_dir", staticmethod(_boom))
    root = _gfe._export_root()
    assert str(root).replace("\\", "/").endswith("data/temp/git-history")


def _init_git_repo_with_commits(
    path: Path, contents: list[tuple[str, str]]
) -> list[str]:
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", "-b", "main"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    shas: list[str] = []
    for fname, text in contents:
        (path / fname).parent.mkdir(parents=True, exist_ok=True)
        (path / fname).write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"add {fname}", "-q"],
            cwd=path,
            check=True,
        )
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        shas.append(sha)
    return shas


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.put(umo, {"directory": directory, "loaded_at": time.time()})


async def _call(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, body: dict, **query: str
) -> Any:
    """直接调 handler(POST 端点的 body/umo/worktree 由 _wrap 注入,
    单测走显式参数,与 test_git_revert 同模式)。"""
    return await _gfe.handle(plugin, body=body, **query)


# ─── happy path ──────────────────────────────────────────────────────


async def test_export_writes_blob_and_returns_abs_path(
    monkeypatch: pytest.MonkeyPatch,
    plugin: Any,
    tmp_path: Path,
    export_root: Path,
) -> None:
    shas = _init_git_repo_with_commits(
        tmp_path, [("src/a.py", "v1\n"), ("src/a.py", "v2\n")]
    )
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call(monkeypatch, plugin, {"path": "src/a.py", "ref": shas[0]})
    d = r["data"]
    assert d["success"] is True, d
    assert d["exported"] is True
    assert d["size"] == 3
    assert d["is_binary"] is False
    # 绝对路径指向导出根下的 <sha7>/src/a.py
    abs_path = Path(d["abs_path"])
    assert str(abs_path).replace("\\", "/").startswith(
        str(export_root).replace("\\", "/")
    )
    assert abs_path.name == "a.py"
    assert shas[0][:7] in str(abs_path)
    # 内容 = 该 ref 下的 blob(不是最新版)
    assert abs_path.read_text(encoding="utf-8") == "v1\n"


async def test_export_overwrites_same_target(
    monkeypatch: pytest.MonkeyPatch,
    plugin: Any,
    tmp_path: Path,
    export_root: Path,
) -> None:
    shas = _init_git_repo_with_commits(
        tmp_path, [("a.txt", "v1\n"), ("a.txt", "v2\n")]
    )
    _load_project(plugin, "u1", str(tmp_path))
    r1 = await _call(monkeypatch, plugin, {"path": "a.txt", "ref": shas[0]})
    r2 = await _call(monkeypatch, plugin, {"path": "a.txt", "ref": shas[0]})
    assert r1["data"]["abs_path"] == r2["data"]["abs_path"]
    # 同目标覆盖:导出目录里只有这一个文件
    files = [p for p in export_root.rglob("a.txt")]
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "v1\n"


async def test_export_unicode_path(
    monkeypatch: pytest.MonkeyPatch,
    plugin: Any,
    tmp_path: Path,
    export_root: Path,
) -> None:
    shas = _init_git_repo_with_commits(tmp_path, [("中文目录/文件.txt", "内容\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call(
        monkeypatch, plugin, {"path": "中文目录/文件.txt", "ref": shas[0]}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert Path(d["abs_path"]).read_text(encoding="utf-8") == "内容\n"


async def test_export_binary_preserves_bytes(
    monkeypatch: pytest.MonkeyPatch,
    plugin: Any,
    tmp_path: Path,
    export_root: Path,
) -> None:
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", "-b", "main"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    payload = b"\x00\x01\x02\xff" * 8
    (tmp_path / "bin.dat").write_bytes(payload)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "b", "-q"], cwd=tmp_path, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call(monkeypatch, plugin, {"path": "bin.dat", "ref": sha})
    d = r["data"]
    assert d["success"] is True, d
    assert d["is_binary"] is True
    assert Path(d["abs_path"]).read_bytes() == payload


# ─── failure paths ───────────────────────────────────────────────────


async def test_missing_path_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call(monkeypatch, plugin, {"ref": "HEAD"})
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_missing_ref_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call(monkeypatch, plugin, {"path": "a.txt"})
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_ref_not_found(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call(monkeypatch, plugin, {"path": "a.txt", "ref": "nope"})
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "ref_not_found"


async def test_path_unsafe(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call(monkeypatch, plugin, {"path": "../x.txt", "ref": "HEAD"})
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "path_unsafe"


async def test_blob_missing_at_ref(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    shas = _init_git_repo_with_commits(
        tmp_path, [("old.txt", "o\n"), ("new.txt", "n\n")]
    )
    _load_project(plugin, "u1", str(tmp_path))
    # new.txt 在 shas[0] 时还不存在
    r = await _call(monkeypatch, plugin, {"path": "new.txt", "ref": shas[0]})
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "file_missing_at_ref"


async def test_no_project_loaded(
    monkeypatch: pytest.MonkeyPatch, plugin: Any
) -> None:
    r = await _call(monkeypatch, plugin, {"path": "a.txt", "ref": "HEAD"})
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "no_project_loaded"
