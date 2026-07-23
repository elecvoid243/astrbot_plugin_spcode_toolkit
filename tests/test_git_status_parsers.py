"""Tests for git-status pure parser helpers.

Separate from :mod:`tests.test_git_status` to avoid the asyncio mark
warning for sync test methods — mirrors the
``test_git_log_parsers`` ↔ ``test_git_log`` split.
"""

from __future__ import annotations

from tools.webapi import git_status as _gs


# ──────────────────────────────────────────────────────────
# _classify_file_scope — X/Y 列 → scope 分类
# ──────────────────────────────────────────────────────────


class TestClassifyFileScope:
    """X/Y 列 → scope 分类。覆盖 porcelain v1 全部典型组合。"""

    def test_untracked(self) -> None:
        assert _gs._classify_file_scope("?", "?") == "untracked"

    def test_intent_to_add(self) -> None:
        # X=' ' Y='A' — git add -N 后的特殊状态
        assert _gs._classify_file_scope(" ", "A") == "intent_to_add"

    def test_pure_staged(self) -> None:
        # X='M' Y=' ' — git add 后无 worktree 改动
        assert _gs._classify_file_scope("M", " ") == "staged"
        assert _gs._classify_file_scope("A", " ") == "staged"
        assert _gs._classify_file_scope("D", " ") == "staged"

    def test_pure_unstaged(self) -> None:
        # X=' ' Y='M' — 仅 worktree 改动未暂存
        assert _gs._classify_file_scope(" ", "M") == "unstaged"
        assert _gs._classify_file_scope(" ", "D") == "unstaged"

    def test_modified_both(self) -> None:
        # X='M' Y='M' — 已暂存 + worktree 也有改动
        assert _gs._classify_file_scope("M", "M") == "modified_both"

    def test_conflict_uu(self) -> None:
        assert _gs._classify_file_scope("U", "U") == "conflict"

    def test_conflict_aa(self) -> None:
        assert _gs._classify_file_scope("A", "A") == "conflict"

    def test_conflict_du(self) -> None:
        assert _gs._classify_file_scope("D", "U") == "conflict"


# ──────────────────────────────────────────────────────────
# _parse_porcelain_v1 — porcelain v1 文本 → file list
# ──────────────────────────────────────────────────────────


class TestParsePorcelainV1:
    """git status --porcelain 文本 → file list。"""

    def test_empty(self) -> None:
        assert _gs._parse_porcelain_v1("") == []

    def test_whitespace_only(self) -> None:
        # ``.rstrip("\r\n")`` 后剩余空行应被忽略
        assert _gs._parse_porcelain_v1("\n\n\n") == []

    def test_single_unstaged(self) -> None:
        files = _gs._parse_porcelain_v1(" M src/main.py")
        assert len(files) == 1
        assert files[0]["path"] == "src/main.py"
        assert files[0]["x_status"] == " "
        assert files[0]["y_status"] == "M"
        assert files[0]["scope"] == "unstaged"

    def test_single_staged(self) -> None:
        files = _gs._parse_porcelain_v1("M  src/main.py")
        assert files[0]["scope"] == "staged"
        assert files[0]["path"] == "src/main.py"

    def test_untracked(self) -> None:
        files = _gs._parse_porcelain_v1("?? new.txt")
        assert files[0]["path"] == "new.txt"
        assert files[0]["scope"] == "untracked"

    def test_intent_to_add(self) -> None:
        files = _gs._parse_porcelain_v1(" A intent.py")
        assert files[0]["scope"] == "intent_to_add"

    def test_mixed(self) -> None:
        raw = (
            " M src/main.py\n"  # unstaged
            "M  src/lib.py\n"  # staged
            "?? new.txt\n"  # untracked
            " A intent.py\n"  # intent-to-add
            "UU conflict.txt\n"  # conflict
        )
        files = _gs._parse_porcelain_v1(raw)
        assert len(files) == 5
        scopes = {f["path"]: f["scope"] for f in files}
        assert scopes == {
            "src/main.py": "unstaged",
            "src/lib.py": "staged",
            "new.txt": "untracked",
            "intent.py": "intent_to_add",
            "conflict.txt": "conflict",
        }

    def test_skips_too_short_lines(self) -> None:
        # < 4 字符的损坏行必须跳过(避免 IndexError)
        files = _gs._parse_porcelain_v1(" M\n?? \n M ok.py")
        assert len(files) == 1
        assert files[0]["path"] == "ok.py"


# ──────────────────────────────────────────────────────────
# _parse_ahead_behind — rev-list 输出 → (ahead, behind) 元组
# ──────────────────────────────────────────────────────────


class TestParseAheadBehind:
    """git rev-list --left-right --count 输出 → (ahead, behind) 元组。"""

    def test_valid(self) -> None:
        assert _gs._parse_ahead_behind("3\t5") == (3, 5)

    def test_zero_zero(self) -> None:
        assert _gs._parse_ahead_behind("0\t0") == (0, 0)

    def test_too_few_columns(self) -> None:
        assert _gs._parse_ahead_behind("3") == (0, 0)
        assert _gs._parse_ahead_behind("") == (0, 0)

    def test_non_numeric(self) -> None:
        assert _gs._parse_ahead_behind("abc\tdef") == (0, 0)

    def test_with_whitespace(self) -> None:
        assert _gs._parse_ahead_behind("  2 \t 7  ") == (2, 7)


# ──────────────────────────────────────────────────────────
# _parse_porcelain_v1_z — NUL-delimited porcelain v1 parser
# Author: elecvoid243
# Date: 2026-07-23
# Replaces _parse_porcelain_v1 for ``git status --porcelain=v1 -z``.
# Backwards compatible with the old line-based helper (still used by
# other tests). Both parsers share _classify_file_scope.
# ──────────────────────────────────────────────────────────


class TestParsePorcelainV1Z:
    """NUL-delimited ``git status --porcelain=v1 -z`` parser tests."""

    def test_empty(self) -> None:
        assert _gs._parse_porcelain_v1_z("") == []

    def test_single_unstaged(self) -> None:
        files = _gs._parse_porcelain_v1_z(" M src/main.py\0")
        assert files == [
            {
                "path": "src/main.py",
                "x_status": " ",
                "y_status": "M",
                "scope": "unstaged",
            }
        ]

    def test_single_staged(self) -> None:
        files = _gs._parse_porcelain_v1_z("M  src/main.py\0")
        assert files[0]["path"] == "src/main.py"
        assert files[0]["scope"] == "staged"

    def test_preserves_unicode_and_spaces(self) -> None:
        path = "docs/中文 文档.txt"
        files = _gs._parse_porcelain_v1_z(f"?? {path}\0")
        assert files[0]["path"] == path
        assert files[0]["scope"] == "untracked"

    def test_preserves_tabs_and_newlines(self) -> None:
        path = "docs/tab\tline\nname.txt"
        files = _gs._parse_porcelain_v1_z(f"?? {path}\0")
        assert files[0]["path"] == path

    def test_intent_to_add(self) -> None:
        files = _gs._parse_porcelain_v1_z(" A intent.py\0")
        assert files[0]["scope"] == "intent_to_add"

    def test_mixed(self) -> None:
        raw = (
            " M src/main.py\0M  src/lib.py\0?? new.txt\0 A intent.py\0UU conflict.txt\0"
        )
        files = _gs._parse_porcelain_v1_z(raw)
        scopes = {item["path"]: item["scope"] for item in files}
        assert scopes == {
            "src/main.py": "unstaged",
            "src/lib.py": "staged",
            "new.txt": "untracked",
            "intent.py": "intent_to_add",
            "conflict.txt": "conflict",
        }

    def test_rename_uses_destination_and_consumes_source(self) -> None:
        raw = "R  新名称.txt\0旧名称.txt\0?? next.txt\0"
        files = _gs._parse_porcelain_v1_z(raw)
        assert [item["path"] for item in files] == [
            "新名称.txt",
            "next.txt",
        ]
        assert files[0]["scope"] == "staged"

    def test_copy_uses_destination_and_consumes_source(self) -> None:
        raw = " C copied.txt\0source.txt\0"
        files = _gs._parse_porcelain_v1_z(raw)
        assert [item["path"] for item in files] == ["copied.txt"]

    def test_skips_malformed_records(self) -> None:
        files = _gs._parse_porcelain_v1_z("bad\0?? ok.txt\0")
        assert [item["path"] for item in files] == ["ok.txt"]
