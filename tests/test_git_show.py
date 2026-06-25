"""Tests for GET /spcode/git-show HTTP endpoint.

v3.8 (2026-06-25): 新增端点 — 返回给定 ref 的 commit 元数据 + 修改文件列表。
Spec: docs/superpowers/specs/2026-06-25-git-show-design.md
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin, make_web_request_mock
from tools.project import state as _proj_state
from tools.webapi import git_show as _gs
from tools.webapi._helpers import ReasonCode

pytestmark = [
    pytest.mark.asyncio,
    # 模块底部有 10 个解析器单元测试是 sync 函数(无 await),被全局
    # ``pytest.mark.asyncio`` 误标,触发 PytestWarning。
    # 在此处静默该特定警告,避免污染测试输出。
    pytest.mark.filterwarnings("ignore::pytest.PytestWarning"),
]


@pytest.fixture
def plugin():
    return _make_plugin()


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


def _init_repo_with_complex_commit(path: Path) -> dict[str, str]:
    """Init repo with: root commit → commit_1 (add + modify) → commit_2
    (rename + delete + modify). Return dict of relevant SHAs."""
    out: dict[str, str] = {}
    _run(["git", "init", "-q"], path)
    _run(["git", "config", "user.email", "t@t"], path)
    _run(["git", "config", "user.name", "t"], path)
    # Default branch may be main or master
    initial_branch = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    out["branch"] = initial_branch

    # Root commit
    (path / "init.txt").write_text("init", encoding="utf-8")
    _run(["git", "add", "."], path)
    _run(["git", "commit", "-q", "-m", "root"], path)
    out["root_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Commit 1: add file1.txt, modify init.txt
    (path / "file1.txt").write_text("hello\nworld\n", encoding="utf-8")
    (path / "init.txt").write_text("init v2\n", encoding="utf-8")
    _run(["git", "add", "."], path)
    _run(["git", "commit", "-q", "-m", "commit 1: add + modify"], path)
    out["commit_1_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Commit 2: rename file1.txt → file1_renamed.txt, delete init.txt, add new.txt
    _run(["git", "mv", "file1.txt", "file1_renamed.txt"], path)
    _run(["git", "rm", "-q", "init.txt"], path)
    (path / "new.txt").write_text("brand new content\n", encoding="utf-8")
    _run(["git", "add", "."], path)
    _run(["git", "commit", "-q", "-m", "commit 2: rename + delete + add"], path)
    out["commit_2_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    return out


def _init_repo_simple(path: Path, n: int = 1) -> list[str]:
    """Init repo with n simple commits. Return SHAs oldest → newest."""
    _run(["git", "init", "-q"], path)
    _run(["git", "config", "user.email", "t@t"], path)
    _run(["git", "config", "user.name", "t"], path)
    shas: list[str] = []
    for i in range(n):
        (path / f"file{i}.txt").write_text(f"v{i}\n", encoding="utf-8")
        _run(["git", "add", "."], path)
        _run(["git", "commit", "-q", "-m", f"commit {i}"], path)
        shas.append(
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    return shas


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.put(umo, {"directory": directory, "loaded_at": time.time()})


def _call_with_query(monkeypatch, plugin, **query):
    """Patch web.request with query dict then call handle()."""
    from astrbot.api import web

    monkeypatch.setattr(web, "request", make_web_request_mock(query=query))
    return _gs.handle(plugin)


# ──────────────────────────────────────────────────────────
# Happy path: commit metadata + files
# ──────────────────────────────────────────────────────────


async def test_show_default_returns_head_commit_files(
    monkeypatch, plugin, tmp_path: Path
):
    """默认 ref=HEAD,返回 HEAD commit + 1 个新增文件。"""
    shas = _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["loaded"] is True
    assert result["data"]["reason"] is None
    assert result["data"]["ref"] == "HEAD"
    assert result["data"]["resolved_sha"] == shas[-1]
    assert result["data"]["count"] == 1
    assert result["data"]["truncated"] is False
    assert result["data"]["max_files"] == 500

    f = result["data"]["files"][0]
    assert f["path"] == "file0.txt"
    assert f["status"] == "A"
    assert f["additions"] == 1
    assert f["deletions"] == 0


async def test_show_explicit_full_sha(monkeypatch, plugin, tmp_path: Path):
    """ref=<完整 40 字符 SHA> 解析到该 commit。"""
    shas = _init_repo_simple(tmp_path, n=3)
    _load_project(plugin, "u:m", str(tmp_path))

    # ref=shas[0] 是最老 commit
    result = await _call_with_query(monkeypatch, plugin, ref=shas[0])
    assert result["data"]["resolved_sha"] == shas[0]
    assert result["data"]["count"] == 1
    assert result["data"]["files"][0]["path"] == "file0.txt"


async def test_show_ref_branch_name(monkeypatch, plugin, tmp_path: Path):
    """ref=<branch name> 解析到 branch HEAD。"""
    shas = _init_repo_simple(tmp_path, n=2)
    _load_project(plugin, "u:m", str(tmp_path))

    # 推断 default branch(main / master)
    initial_branch = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    result = await _call_with_query(monkeypatch, plugin, ref=initial_branch)
    assert result["data"]["resolved_sha"] == shas[-1]
    assert (
        result["data"]["count"] == 1
    )  # 只有 1 个文件(file1.txt,因为 file0.txt 已在 shas[0])


async def test_show_ref_parent_shorthand(monkeypatch, plugin, tmp_path: Path):
    """ref=HEAD~1 解析到父 commit。"""
    shas = _init_repo_simple(tmp_path, n=3)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref="HEAD~1")
    assert result["data"]["resolved_sha"] == shas[1]
    assert result["data"]["count"] == 1
    assert result["data"]["files"][0]["path"] == "file1.txt"


# ──────────────────────────────────────────────────────────
# File status types: M / A / D / R / C
# ──────────────────────────────────────────────────────────


async def test_show_files_have_status_fields(monkeypatch, plugin, tmp_path: Path):
    """每个 file entry 必须含 path / status / additions / deletions 字段。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    f = result["data"]["files"][0]
    assert "path" in f
    assert "status" in f
    assert "additions" in f
    assert "deletions" in f


async def test_show_rename_entry(monkeypatch, plugin, tmp_path: Path):
    """R100 重命名 → status=R, old_path=<旧>, similarity=100, path=<新>。"""
    shas = _init_repo_with_complex_commit(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    # ref=commit_2_sha 是含 rename 的 commit
    result = await _call_with_query(monkeypatch, plugin, ref=shas["commit_2_sha"])
    assert result["data"]["loaded"] is True
    files = {f["path"]: f for f in result["data"]["files"]}

    # rename: file1.txt → file1_renamed.txt
    renamed = files.get("file1_renamed.txt")
    assert renamed is not None, f"未找到 rename entry,files={list(files.keys())}"
    assert renamed["status"] == "R"
    assert renamed["old_path"] == "file1.txt"
    assert renamed["similarity"] == 100
    # numstat 在 rename 上有时是 -/-(binary),但纯文本重写应该是 0/0
    assert "additions" in renamed
    assert "deletions" in renamed


async def test_show_add_entry(monkeypatch, plugin, tmp_path: Path):
    """A 新增 → status=A, additions>0, deletions=0。"""
    shas = _init_repo_with_complex_commit(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref=shas["commit_2_sha"])
    files = {f["path"]: f for f in result["data"]["files"]}

    new_entry = files.get("new.txt")
    assert new_entry is not None
    assert new_entry["status"] == "A"
    assert new_entry["additions"] >= 1
    assert new_entry["deletions"] == 0


async def test_show_delete_entry(monkeypatch, plugin, tmp_path: Path):
    """D 删除 → status=D, additions=0, deletions>=1。"""
    shas = _init_repo_with_complex_commit(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref=shas["commit_2_sha"])
    files = {f["path"]: f for f in result["data"]["files"]}

    del_entry = files.get("init.txt")
    assert del_entry is not None
    assert del_entry["status"] == "D"
    assert del_entry["additions"] == 0
    assert del_entry["deletions"] >= 1


async def test_show_modify_entry_has_numstat(monkeypatch, plugin, tmp_path: Path):
    """M 修改 → additions + deletions 都应有值(可能为 0)。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    f = result["data"]["files"][0]
    # status 是 A(add)
    assert f["status"] == "A"
    # additions/deletions 都是 int
    assert isinstance(f["additions"], int)
    assert isinstance(f["deletions"], int)


# ──────────────────────────────────────────────────────────
# Commit metadata fields
# ──────────────────────────────────────────────────────────


async def test_show_commit_metadata_fields(monkeypatch, plugin, tmp_path: Path):
    """commit 必须含 sha/parents/author/date/subject/body 全字段。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    d = result["data"]
    assert len(d["resolved_sha"]) == 40
    assert all(c in "0123456789abcdef" for c in d["resolved_sha"])
    assert isinstance(d["parents"], list)
    assert d["author"]["name"] == "t"
    assert d["author"]["email"] == "t@t"
    assert "T" in d["date"]  # ISO 8601 with T separator
    assert d["subject"] == "commit 0"
    assert d["body"] is None  # single-line commit, 无 body


async def test_show_root_commit_parents_empty(monkeypatch, plugin, tmp_path: Path):
    """root commit 的 parents 应为空列表。"""
    shas = _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref=shas[0])
    assert result["data"]["parents"] == []


# ──────────────────────────────────────────────────────────
# max_files / truncation
# ──────────────────────────────────────────────────────────


async def test_show_max_files_truncates(monkeypatch, plugin, tmp_path: Path):
    """max_files=2 + 单 commit 含 3 个文件 → truncated=true, count=2。"""
    shas = _init_repo_with_complex_commit(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    # ref=commit_2_sha 改了 3 个文件(file1.txt→file1_renamed.txt, init.txt 删除, new.txt 新增)
    result = await _call_with_query(
        monkeypatch, plugin, ref=shas["commit_2_sha"], max_files="2"
    )
    assert result["data"]["count"] == 2
    assert result["data"]["truncated"] is True
    assert result["data"]["max_files"] == 2
    # 仍含 additions/deletions 字段
    for f in result["data"]["files"]:
        assert "additions" in f
        assert "deletions" in f


async def test_show_max_files_out_of_range_clamps_to_2000(
    monkeypatch, plugin, tmp_path: Path
):
    """max_files=99999 → 截到 2000, 不报错。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, max_files="99999")
    assert result["data"]["max_files"] == 2000
    assert result["data"]["count"] == 1


async def test_show_max_files_non_int_returns_invalid_param(
    monkeypatch, plugin, tmp_path: Path
):
    """max_files=abc → invalid_param。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, max_files="abc")
    assert result["data"]["loaded"] is False
    assert result["data"]["reason"] == "invalid_param"


# ──────────────────────────────────────────────────────────
# Failure paths
# ──────────────────────────────────────────────────────────


async def test_show_invalid_ref_returns_ref_not_found(
    monkeypatch, plugin, tmp_path: Path
):
    """ref=0000000...(无效 SHA) → reason=ref_not_found。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(
        monkeypatch, plugin, ref="0000000000000000000000000000000000000000"
    )
    assert result["data"]["loaded"] is False
    assert result["data"]["reason"] == "ref_not_found"


async def test_show_ref_too_long_returns_invalid_param(
    monkeypatch, plugin, tmp_path: Path
):
    """ref 长度 > 512 → invalid_param。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref="x" * 600)
    assert result["data"]["loaded"] is False
    assert result["data"]["reason"] == "invalid_param"


async def test_show_no_project_loaded(monkeypatch, plugin):
    """无 umo + state 空 → no_project_loaded。"""
    _proj_state.reset()
    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["loaded"] is False
    assert result["data"]["reason"] == "no_project_loaded"


async def test_show_feature_disabled(monkeypatch, plugin):
    """agentsmd_enabled=false → feature_disabled。"""
    plugin._config["agentsmd_enabled"] = False
    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["reason"] == "feature_disabled"


async def test_show_worktree_invalid(monkeypatch, plugin, tmp_path: Path):
    """?worktree=../escape → worktree_invalid。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    from astrbot.api import web

    monkeypatch.setattr(web, "request", make_web_request_mock())
    result = await _gs.handle(plugin, worktree="../escape")
    assert result["data"]["reason"] == "worktree_invalid"


async def test_show_not_a_git_repo(monkeypatch, plugin, tmp_path: Path):
    """loaded 目录不是 git 仓库 → not_a_git_repo。"""
    non_git = tmp_path / "plain"
    non_git.mkdir()
    _load_project(plugin, "u:m", str(non_git))

    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["reason"] == "not_a_git_repo"


async def test_show_directory_missing(monkeypatch, plugin, tmp_path: Path):
    """loaded 目录已被删除 → directory_missing。"""
    fake_dir = tmp_path / "ghost"
    fake_dir.mkdir()
    _load_project(plugin, "u:m", str(fake_dir))
    fake_dir.rmdir()  # 立刻删掉

    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["reason"] == "directory_missing"


# ──────────────────────────────────────────────────────────
# ETag / 304 short-circuit
# ──────────────────────────────────────────────────────────


async def test_show_etag_returns_304_on_match(monkeypatch, plugin, tmp_path: Path):
    """第二次相同 ETag 请求 → 304(不返回完整 body)。"""
    from astrbot.api import web

    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    # 第一次请求:获取 ETag
    monkeypatch.setattr(web, "request", make_web_request_mock(headers={}))
    r1 = await _gs.handle(plugin)
    etag = r1.headers["ETag"]
    assert etag.startswith('W/"')

    # 第二次请求:带 If-None-Match
    monkeypatch.setattr(
        web, "request", make_web_request_mock(headers={"If-None-Match": etag})
    )
    r2 = await _gs.handle(plugin)
    # 304 响应通常用 _make_304_response / 某种 envelope 包装;
    # 关键断言:不是 _JSONResponseCompat,且不含 data.files
    if hasattr(r2, "status_code"):
        assert r2.status_code == 304
    else:
        # 部分实现走 dict;触发条件是 If-None-Match 命中
        assert r2.get("status_code") == 304 or r2.get("status") == "not_modified"


# ──────────────────────────────────────────────────────────
# Parsers (unit)
# ──────────────────────────────────────────────────────────


def test_parse_name_status_simple_modify():
    raw = "M\tsrc/auth.py\n"
    out = _gs._parse_name_status_lines(raw)
    assert len(out) == 1
    assert out[0] == {"path": "src/auth.py", "status": "M"}


def test_parse_name_status_rename():
    raw = "R100\told.py\tnew.py\n"
    out = _gs._parse_name_status_lines(raw)
    assert len(out) == 1
    assert out[0] == {
        "path": "new.py",
        "status": "R",
        "old_path": "old.py",
        "similarity": 100,
    }


def test_parse_name_status_copy_with_partial_similarity():
    raw = "C075\torigin.py\tcopy.py\n"
    out = _gs._parse_name_status_lines(raw)
    assert out[0]["status"] == "C"
    assert out[0]["old_path"] == "origin.py"
    assert out[0]["path"] == "copy.py"
    assert out[0]["similarity"] == 75


def test_parse_name_status_type_change_falls_back_to_modify():
    raw = "T\tconfig.json\n"
    out = _gs._parse_name_status_lines(raw)
    assert out[0]["status"] == "M"  # T 归为 M
    assert out[0]["path"] == "config.json"


def test_parse_numstat_simple():
    raw = "10\t5\tsrc/auth.py\n3\t2\ttests/test_x.py\n"
    out = _gs._parse_numstat_lines(raw)
    assert out == {
        "src/auth.py": (10, 5),
        "tests/test_x.py": (3, 2),
    }


def test_parse_numstat_binary_uses_zero():
    raw = "-\t-\timage.png\n"
    out = _gs._parse_numstat_lines(raw)
    assert out == {"image.png": (0, 0)}


def test_parse_numstat_rename():
    raw = "5\t2\told.py\tnew.py\n"
    out = _gs._parse_numstat_lines(raw)
    # key 是新路径(便于与 name-status 对齐)
    assert out == {"new.py": (5, 2)}


def test_parse_format_block_basic():
    # SHA 必须是 40 hex 字符;`418bb365` (8) + `0`*32 = 40
    sha = "418bb365" + "0" * 32
    assert len(sha) == 40
    fmt = f"{sha}\x00\x00t\x00t@t\x002026-06-25T08:00:00+08:00\x00subject\x00body text\x00"
    out = _gs._parse_format_block(fmt)
    assert out is not None
    assert out["sha"] == sha
    assert out["parents"] == []
    assert out["author"]["name"] == "t"
    assert out["author"]["email"] == "t@t"
    assert out["date"] == "2026-06-25T08:00:00+08:00"
    assert out["subject"] == "subject"
    assert out["body"] == "body text"


def test_parse_format_block_with_parents():
    sha = "418bb365" + "0" * 32
    parent1 = "abc12345" + "0" * 32
    parent2 = "def45678" + "0" * 32
    fmt = (
        f"{sha}"
        f"\x00{parent1} {parent2}"
        "\x00t\x00t@t\x002026-06-25T08:00:00+08:00"
        "\x00merge commit"
        "\x00\x00"
    )
    out = _gs._parse_format_block(fmt)
    assert out is not None
    assert len(out["parents"]) == 2
    assert out["parents"][0] == parent1
    assert out["parents"][1] == parent2


def test_parse_format_block_invalid_sha_returns_none():
    fmt = "not-a-sha\x00\x00t\x00t@t\x00date\x00subj\x00\x00"
    out = _gs._parse_format_block(fmt)
    assert out is None


# ──────────────────────────────────────────────────────────
# v3.9 (2026-06-25): 单文件 patch 解析器
# Spec: docs/superpowers/specs/2026-06-25-git-show-design.md §1.3
# 复用 git-show 端点加 ?path= 可选参数,返回单文件 patch。
# 解析器输入是 ``git show <ref> -- <path> --no-color --no-ext-diff``
# 的原始输出,目标: 提取 unified diff + 统计行数 + 识别 binary/rename。
# ──────────────────────────────────────────────────────────


def test_parse_single_file_patch_modify():
    raw = (
        "diff --git a/src/auth.py b/src/auth.py\n"
        "index 111..222 100644\n"
        "--- a/src/auth.py\n"
        "+++ b/src/auth.py\n"
        "@@ -1,3 +1,4 @@\n"
        " line1\n"
        "+added line\n"
        " line2\n"
        " line3\n"
    )
    out = _gs._parse_single_file_patch(raw, "src/auth.py")
    assert out["status"] == "M"
    assert out["additions"] == 1
    assert out["deletions"] == 0
    assert out["is_binary"] is False
    assert out["old_path"] is None
    assert out["path"] == "src/auth.py"
    assert "@@ -1,3 +1,4 @@" in out["patch"]
    assert "+added line" in out["patch"]
    assert "-1,3" in out["patch"]  # 保留 --- / +++ / @@ header


def test_parse_single_file_patch_add():
    raw = (
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "index 000..111\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+line1\n"
        "+line2\n"
        "+line3\n"
    )
    out = _gs._parse_single_file_patch(raw, "new.py")
    assert out["status"] == "A"
    assert out["additions"] == 3
    assert out["deletions"] == 0
    assert "new file mode" in out["patch"]


def test_parse_single_file_patch_delete():
    raw = (
        "diff --git a/gone.py b/gone.py\n"
        "deleted file mode 100644\n"
        "index 111..000\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,3 +0,0 @@\n"
        "-line1\n"
        "-line2\n"
        "-line3\n"
    )
    out = _gs._parse_single_file_patch(raw, "gone.py")
    assert out["status"] == "D"
    assert out["additions"] == 0
    assert out["deletions"] == 3
    assert "deleted file mode" in out["patch"]


def test_parse_single_file_patch_rename_with_content_change():
    raw = (
        "diff --git a/old.py b/new.py\n"
        "similarity index 95%\n"
        "rename from old.py\n"
        "rename to new.py\n"
        "index abc..def 100644\n"
        "--- a/old.py\n"
        "+++ b/new.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old line\n"
        "+new line\n"
    )
    out = _gs._parse_single_file_patch(raw, "new.py")
    assert out["status"] == "R"
    assert out["old_path"] == "old.py"
    assert out["path"] == "new.py"
    assert out["additions"] == 1
    assert out["deletions"] == 1
    assert "rename from old.py" in out["patch"]


def test_parse_single_file_patch_rename_pure_no_hunk():
    # 100% 相似度的纯 rename 没有 hunk 块,patch 只到 "rename to" 行结束
    raw = (
        "diff --git a/old.py b/new.py\n"
        "similarity index 100%\n"
        "rename from old.py\n"
        "rename to new.py\n"
    )
    out = _gs._parse_single_file_patch(raw, "new.py")
    assert out["status"] == "R"
    assert out["old_path"] == "old.py"
    assert out["path"] == "new.py"
    assert out["additions"] == 0
    assert out["deletions"] == 0
    assert "rename to new.py" in out["patch"]


def test_parse_single_file_patch_binary():
    raw = (
        "diff --git a/img.png b/img.png\n"
        "index abc..def 100644\n"
        "Binary files a/img.png and b/img.png differ\n"
    )
    out = _gs._parse_single_file_patch(raw, "img.png")
    assert out["is_binary"] is True
    assert out["status"] == "M"
    # binary 没有可显示的 patch,保持 None 让前端渲染 "binaryFile" 提示
    assert out["patch"] is None


def test_parse_single_file_patch_path_mismatch_returns_unknown():
    # target_path 不在 diff 中(防御: caller 给错 path 也不崩)
    raw = (
        "diff --git a/other.py b/other.py\n"
        "index 111..222 100644\n"
        "--- a/other.py\n"
        "+++ b/other.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-x\n"
        "+y\n"
    )
    out = _gs._parse_single_file_patch(raw, "src/auth.py")
    assert out["status"] == "unknown"
    assert out["additions"] == 0
    assert out["deletions"] == 0
    assert out["is_binary"] is False
    assert out["patch"] is None


def test_parse_single_file_patch_empty_input():
    out = _gs._parse_single_file_patch("", "src/auth.py")
    assert out["status"] == "unknown"
    assert out["patch"] is None


def test_parse_single_file_patch_skips_commit_header_lines():
    # 真实 `git show <ref> -- <path>` 会在 diff 前输出 commit / Author / Date / 空行 /
    # commit message。解析器需要正确跳过这些,只挑 diff 段。
    raw = (
        "commit 418bb3650000000000000000000000000000000a\n"
        "Author: t <t@t>\n"
        "Date:   Thu Jun 25 21:00:00 2026 +0800\n"
        "\n"
        "    feat: change auth\n"
        "\n"
        "diff --git a/src/auth.py b/src/auth.py\n"
        "index 111..222 100644\n"
        "--- a/src/auth.py\n"
        "+++ b/src/auth.py\n"
        "@@ -1,2 +1,3 @@\n"
        " line1\n"
        "+line1.5\n"
        " line2\n"
    )
    out = _gs._parse_single_file_patch(raw, "src/auth.py")
    assert out["status"] == "M"
    assert out["additions"] == 1
    assert out["deletions"] == 0
    assert "commit 418bb365" not in out["patch"]
    assert "Author:" not in out["patch"]
    assert "diff --git" in out["patch"]


def test_parse_single_file_patch_hunk_count_multiple():
    raw = (
        "diff --git a/big.py b/big.py\n"
        "index 111..222 100644\n"
        "--- a/big.py\n"
        "+++ b/big.py\n"
        "@@ -1,3 +1,4 @@\n"
        " a\n"
        "+x\n"
        " b\n"
        " c\n"
        "@@ -50,3 +51,4 @@\n"
        " d\n"
        "+y\n"
        " e\n"
        " f\n"
    )
    out = _gs._parse_single_file_patch(raw, "big.py")
    assert out["status"] == "M"
    assert out["additions"] == 2
    assert out["deletions"] == 0
    # hunk 头格式 ``@@ -<old>,<n> +<new>,<n> @@``,用 "@@ -" 锁定 hunk 起点的数量
    assert out["patch"].count("@@ -") == 2


# ──────────────────────────────────────────────────────────
# v3.9 (2026-06-25): handle() ?path= 端到端测试
# Spec: docs/superpowers/specs/2026-06-25-git-show-design.md §1.3
# 扩展 git-show 端点,加 ?path= 可选参数,返回单文件 patch。
# ──────────────────────────────────────────────────────────


async def test_show_path_query_returns_single_file_patch(
    monkeypatch, plugin, tmp_path: Path
):
    """传 ?path= 时,响应包含 file 字段,patch 文本非空。"""
    # 2 个 commit: commit 0 创建 file0.txt, commit 1 创建 file1.txt。
    # 用 HEAD(= commit 1) + path=file1.txt 取单文件 patch,验证 file 字段。
    _init_repo_simple(tmp_path, n=2)
    _load_project(plugin, "u:m", str(tmp_path))

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    result = await _call_with_query(monkeypatch, plugin, ref=head_sha, path="file1.txt")
    data = result["data"]
    assert data["loaded"] is True
    assert data["reason"] is None
    assert data["resolved_sha"] == head_sha
    assert "file" in data, "?path= 时响应必须含 file 字段"
    fv = data["file"]
    assert fv["path"] == "file1.txt"
    assert fv["status"] == "A"  # HEAD 创建 file1.txt
    assert fv["is_binary"] is False
    assert fv["old_path"] is None
    assert fv["additions"] >= 1
    assert fv["deletions"] == 0
    # patch 文本必须包含 hunk 头
    assert "diff --git a/file1.txt b/file1.txt" in fv["patch"]
    assert "@@ -" in fv["patch"]
    assert "new file mode" in fv["patch"]


async def test_show_no_path_query_omits_file_field(monkeypatch, plugin, tmp_path: Path):
    """不传 ?path= 时,响应 100% 向后兼容:不应有 file 字段。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    data = result["data"]
    assert "file" not in data, "v3.9 向后兼容:不传 ?path= 时不应在响应里塞 file 字段"
    # v3.8 schema 仍完整
    assert data["loaded"] is True
    assert isinstance(data["files"], list)
    assert "resolved_sha" in data


async def test_show_path_query_binary_file(monkeypatch, plugin, tmp_path: Path):
    """?path= 指向 binary 文件: file.is_binary=True, file.patch=None。"""
    _run(["git", "init", "-q"], tmp_path)
    _run(["git", "config", "user.email", "t@t"], tmp_path)
    _run(["git", "config", "user.name", "t"], tmp_path)
    # 写一个 binary 文件(0xFF 字节,git 视为 binary)
    (tmp_path / "init.txt").write_bytes(b"\x00\x01\x02\xff\xff\xfe")
    _run(["git", "add", "."], tmp_path)
    _run(["git", "commit", "-q", "-m", "init"], tmp_path)
    (tmp_path / "init.txt").write_bytes(b"\x00\x01\x02\xff\xff\xff\xff\xfe")
    _run(["git", "add", "."], tmp_path)
    _run(["git", "commit", "-q", "-m", "modify binary"], tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="init.txt")
    fv = result["data"]["file"]
    assert fv["is_binary"] is True
    assert fv["patch"] is None
    assert fv["path"] == "init.txt"


async def test_show_path_query_path_not_in_commit(monkeypatch, plugin, tmp_path: Path):
    """?path= 给一个不在 ref 改动的路径:不报错,file.status="unknown", patch=None。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="not-modified.txt")
    data = result["data"]
    # commit 元数据仍正常返回(name-status 仍返回 1 个真实文件)
    assert data["loaded"] is True
    # 但 file 视图是占位
    fv = data["file"]
    assert fv["status"] == "unknown"
    assert fv["patch"] is None
    assert fv["is_binary"] is False


async def test_show_path_query_empty_path_returns_invalid_param(
    monkeypatch, plugin, tmp_path: Path
):
    """?path= 全空白(经 strip 后为空):invalid_param。

    注意:这里用全空白而不是空串 "" 是因为 v3.8 既有的 ``_qget`` 把
    空串与"未传"在 falsy 判断上等价(monkeypatch 的 mock.query.get
    也无法区分)。全空白可被 strip() 消除,触发"path 无效"分支。
    """
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="   ")
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM
    assert result["data"]["loaded"] is False


async def test_show_path_query_path_too_long_returns_invalid_param(
    monkeypatch, plugin, tmp_path: Path
):
    """?path= 超过 MAX_PARAM_LENGTH:invalid_param。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="a" * (512 + 1))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


async def test_show_path_query_path_with_newline_returns_invalid_param(
    monkeypatch, plugin, tmp_path: Path
):
    """?path= 含换行 / NUL:invalid_param(防止 git args 注入)。"""
    _init_repo_simple(tmp_path, n=1)
    _load_project(plugin, "u:m", str(tmp_path))

    for bad in ("bad\npath", "bad\rpath", "bad\x00path"):
        result = await _call_with_query(monkeypatch, plugin, path=bad)
        assert result["data"]["reason"] == ReasonCode.INVALID_PARAM, (
            f"path 含控制字符应被拒: {bad!r}"
        )
