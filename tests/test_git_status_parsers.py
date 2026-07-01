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
