"""Tests for GET /spcode/git-file-diff HTTP endpoint.

Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-design.md
Author: elecvoid243, 2026-10-06
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin, make_web_request_mock
from tools.project import state as _proj_state
from tools.webapi import git_file_diff as _gfd

pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin() -> Any:
    return _make_plugin()


def _init_git_repo_with_commits(
    path: Path, contents: list[tuple[str, str]]
) -> list[str]:
    """contents = [(filename, text), ...]; returns SHAs oldest→newest."""
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", "-b", "main"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    shas: list[str] = []
    for fname, text in contents:
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


def _call_with_query(monkeypatch: pytest.MonkeyPatch, plugin: Any, **query: str) -> Any:
    from astrbot.api import web

    monkeypatch.setattr(web, "request", make_web_request_mock(query=query))
    return _gfd.handle(plugin)


# ─── Task 1: 参数校验 / preflight / ref 解析 ─────────────────────────


async def test_missing_from_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(monkeypatch, plugin, to="HEAD", path="a.txt")
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_missing_to_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "HEAD", "path": "a.txt"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_missing_path_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "HEAD", "to": "HEAD"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_ref_too_long_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "a" * 600, "to": "HEAD", "path": "a.txt"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_ref_with_newline_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "HEAD\n-x", "to": "HEAD", "path": "a.txt"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_no_project_loaded(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "HEAD", "to": "HEAD", "path": "a.txt"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "no_project_loaded"


async def test_from_ref_not_found(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch,
        plugin,
        **{"from": "nonexistent-ref", "to": "HEAD", "path": "a.txt"},
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "ref_not_found"
    assert r["data"]["failing_ref"] == "from"


async def test_to_ref_not_found(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch,
        plugin,
        **{"from": "HEAD", "to": "nonexistent-ref", "path": "a.txt"},
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "ref_not_found"
    assert r["data"]["failing_ref"] == "to"


# ─── Task 2: status 探测 / patch / base blob ─────────────────────────


async def test_modified_happy_path(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    shas = _init_git_repo_with_commits(
        tmp_path, [("a.txt", "v1\n"), ("a.txt", "v2\n")]
    )
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": shas[0], "to": shas[1], "path": "a.txt"}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["status"] == "modified"
    assert d["old_path"] is None
    assert d["is_binary"] is False
    assert d["base_content"] == "v1\n"
    assert "@@" in d["patch"]
    assert "-v1" in d["patch"] and "+v2" in d["patch"]
    assert d["additions"] == 1
    assert d["deletions"] == 1
    assert d["truncated"] is False
    assert d["from_sha"] == shas[0]
    assert d["to_sha"] == shas[1]


async def test_added_status(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    shas = _init_git_repo_with_commits(
        tmp_path, [("other.txt", "o\n"), ("new.txt", "hello\n")]
    )
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": shas[0], "to": shas[1], "path": "new.txt"}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["status"] == "added"
    assert d["base_content"] == ""
    assert d["base_size"] == 0
    assert "+hello" in d["patch"]
    assert d["additions"] == 1
    assert d["deletions"] == 0


async def test_deleted_status(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    shas = _init_git_repo_with_commits(tmp_path, [("del.txt", "bye\n")])
    (tmp_path / "del.txt").unlink()
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "del", "-q"], cwd=tmp_path, check=True
    )
    sha2 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": shas[0], "to": sha2, "path": "del.txt"}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["status"] == "deleted"
    assert d["base_content"] == "bye\n"
    assert "-bye" in d["patch"]
    assert d["deletions"] == 1
    assert d["additions"] == 0


async def test_renamed_status(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    content = "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\n"
    shas = _init_git_repo_with_commits(tmp_path, [("a.py", content)])
    subprocess.run(["git", "mv", "a.py", "b.py"], cwd=tmp_path, check=True)
    (tmp_path / "b.py").write_text(content + "line9\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "rename", "-q"], cwd=tmp_path, check=True
    )
    sha2 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": shas[0], "to": sha2, "path": "b.py"}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["status"] == "renamed"
    assert d["old_path"] == "a.py"
    # 关键:base 必须取旧路径在 from 侧的内容(Review Focus #1)
    assert d["base_content"] == content


async def test_unchanged_status(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    shas = _init_git_repo_with_commits(
        tmp_path, [("a.txt", "v1\n"), ("other.txt", "o\n")]
    )
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": shas[0], "to": shas[1], "path": "a.txt"}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["status"] == "unchanged"
    assert d["patch"] == ""
    assert d["additions"] == 0
    assert d["deletions"] == 0
    assert d["base_content"] == "v1\n"


async def test_reverse_direction(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    shas = _init_git_repo_with_commits(
        tmp_path, [("a.txt", "v1\n"), ("a.txt", "v2\n")]
    )
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": shas[1], "to": shas[0], "path": "a.txt"}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["status"] == "modified"
    assert "-v2" in d["patch"] and "+v1" in d["patch"]
    assert d["base_content"] == "v2\n"


# ─── Task 3: 截断 / 二进制 / 路径安全 ────────────────────────────────


async def test_patch_truncated(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    monkeypatch.setattr(_gfd, "MAX_PATCH_BYTES", 512)
    big_v1 = "".join(f"line-{i:04d} {'x' * 40}\n" for i in range(100))
    big_v2 = big_v1.replace("line-0005", "LINE-0005").replace("line-0095", "LINE-0095")
    shas = _init_git_repo_with_commits(
        tmp_path, [("big.txt", big_v1), ("big.txt", big_v2)]
    )
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": shas[0], "to": shas[1], "path": "big.txt"}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["truncated"] is True
    assert d["truncated_at_bytes"] == 512
    assert d["max_bytes"] == 512
    assert len(d["patch"]) <= 512


async def test_base_blob_too_large_truncated(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    """基准 blob 超上限 → 截断 + base_truncated 标志(与 git-file 截断先例一致),
    而非 file_too_large 失败——用户仍能看到 diff。"""
    monkeypatch.setattr(_gfd, "MAX_BASE_BLOB_BYTES", 100)
    big = "y" * 200 + "\n"
    shas = _init_git_repo_with_commits(
        tmp_path, [("big.txt", big), ("big.txt", big + "more\n")]
    )
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": shas[0], "to": shas[1], "path": "big.txt"}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["base_truncated"] is True
    assert d["base_size"] == 201  # 原始 blob 字节数
    assert len(d["base_content"].encode("utf-8")) <= 104  # 截断 + decode 容差


async def test_binary_file(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", "-b", "main"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02\x03" * 32)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "b1", "-q"], cwd=tmp_path, check=True)
    sha1 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (tmp_path / "bin.dat").write_bytes(b"\x00\x05\x06\x07" * 32)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "b2", "-q"], cwd=tmp_path, check=True)
    sha2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": sha1, "to": sha2, "path": "bin.dat"}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["is_binary"] is True
    assert d["patch"] is None
    assert d["base_content"] == ""
    assert d["additions"] is None
    assert d["deletions"] is None


async def test_path_unsafe_dotdot(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "HEAD", "to": "HEAD", "path": "../x.txt"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "path_unsafe"


async def test_path_unsafe_absolute(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "HEAD", "to": "HEAD", "path": "/abs/x.txt"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "path_unsafe"


# ─── Task 4: ETag / 304 / immutable ──────────────────────────────────


def _call_full(
    monkeypatch: pytest.MonkeyPatch,
    plugin: Any,
    query: dict[str, str],
    headers: dict[str, str] | None = None,
) -> Any:
    from astrbot.api import web

    monkeypatch.setattr(
        web, "request", make_web_request_mock(query=query, headers=headers)
    )
    return _gfd.handle(plugin)


async def test_etag_304(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    shas = _init_git_repo_with_commits(
        tmp_path, [("a.txt", "v1\n"), ("a.txt", "v2\n")]
    )
    _load_project(plugin, "u1", str(tmp_path))
    q = {"from": shas[0], "to": shas[1], "path": "a.txt"}
    r1 = await _call_full(monkeypatch, plugin, q)
    etag = r1.headers["ETag"]
    assert etag
    r2 = await _call_full(monkeypatch, plugin, q, {"If-None-Match": etag})
    assert r2.status_code == 304


async def test_etag_immutable_for_shas(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    shas = _init_git_repo_with_commits(
        tmp_path, [("a.txt", "v1\n"), ("a.txt", "v2\n")]
    )
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_full(
        monkeypatch, plugin, {"from": shas[0], "to": shas[1], "path": "a.txt"}
    )
    assert "immutable" in r.headers["Cache-Control"]


async def test_etag_no_cache_for_branch(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n"), ("a.txt", "v2\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_full(
        monkeypatch, plugin, {"from": "HEAD~1", "to": "main", "path": "a.txt"}
    )
    cc = r.headers["Cache-Control"]
    assert "no-cache" in cc
    assert "immutable" not in cc


# ─── Final review fixes(C1/I1/I2/I3) ────────────────────────────────


async def test_non_ascii_path_matches_name_status(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    """C1: 非 ASCII 文件名不能被 core.quotePath 转义后失配 → 误判 unchanged。"""
    shas = _init_git_repo_with_commits(
        tmp_path, [("中文文件.txt", "v1\n"), ("中文文件.txt", "v2\n")]
    )
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin,
        **{"from": shas[0], "to": shas[1], "path": "中文文件.txt"},
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["status"] == "modified"
    assert "-v1" in d["patch"] and "+v2" in d["patch"]
    assert d["additions"] == 1 and d["deletions"] == 1
    assert d["base_content"] == "v1\n"


async def test_text_to_binary_detected(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    """I1: modified 场景 text→binary 也必须置 is_binary(patch 文本信号,
    不限于 added 分支)。"""
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", "-b", "main"],
        cwd=tmp_path, check=True,
    )
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.dat").write_text("plain text\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "c1", "-q"], cwd=tmp_path, check=True)
    sha1 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (tmp_path / "f.dat").write_bytes(b"\x00\x01\x02" * 16)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "c2", "-q"], cwd=tmp_path, check=True)
    sha2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": sha1, "to": sha2, "path": "f.dat"}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["is_binary"] is True
    assert d["patch"] is None
    assert d["additions"] is None and d["deletions"] is None


async def test_query_by_old_rename_path_treated_as_deleted(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    """I2: 用重命名前的旧路径查询 → 该路径在 to 侧不存在,正确语义是
    status=deleted + old_path=None,而不是 renamed + old_path==path 自相矛盾。"""
    content = "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\n"
    shas = _init_git_repo_with_commits(tmp_path, [("a.py", content)])
    subprocess.run(["git", "mv", "a.py", "b.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "rename", "-q"], cwd=tmp_path, check=True)
    sha2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": shas[0], "to": sha2, "path": "a.py"}
    )
    d = r["data"]
    assert d["success"] is True, d
    assert d["status"] == "deleted"
    assert d["old_path"] is None
    assert d["base_content"] == content


async def test_etag_changes_after_branch_advances(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    """I3(Review Focus #4 pin):分支名作 ref 时,分支推进后同查询 ETag 必须变化
    (ETag 基于解析后 SHA,不能基于原始输入)。"""
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n"), ("a.txt", "v2\n")])
    _load_project(plugin, "u1", str(tmp_path))
    q = {"from": "HEAD~1", "to": "main", "path": "a.txt"}
    r1 = await _call_full(monkeypatch, plugin, q)
    etag1 = r1.headers["ETag"]
    # main 前进一步
    (tmp_path / "a.txt").write_text("v3\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "v3", "-q"], cwd=tmp_path, check=True)
    r2 = await _call_full(monkeypatch, plugin, q)
    etag2 = r2.headers["ETag"]
    assert etag1 != etag2
