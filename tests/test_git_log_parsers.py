"""Tests for _parse_log_format and _parse_log_shortstat."""

from tools.webapi.git_log import _parse_log_format, _parse_log_shortstat


def test_parse_log_format_typical():
    """3 commits 全部正确解析。"""
    raw = (
        f"{'a' * 40}\x00{'a' * 7}\x00an\x00ae\x00cn\x00ce\x00aI\x00cI\x00s0\x00b0\x00p1\x00"
        f"{'b' * 40}\x00{'b' * 7}\x00an\x00ae\x00cn\x00ce\x00aI\x00cI\x00s1\x00b1\x00p2\x00"
        f"{'c' * 40}\x00{'c' * 7}\x00an\x00ae\x00cn\x00ce\x00aI\x00cI\x00s2\x00b2\x00\x00"
    )
    commits = _parse_log_format(raw)
    assert len(commits) == 3
    assert commits[0]["sha"] == "a" * 40
    assert commits[1]["sha"] == "b" * 40
    assert commits[2]["sha"] == "c" * 40
    assert commits[0]["subject"] == "s0"
    assert commits[0]["body"] == "b0"
    assert commits[0]["parents"] == ["p1"]
    assert commits[2]["parents"] == []  # 最后一条 parents 字段空 → split 后 []


def test_parse_log_format_skips_invalid_sha():
    """首字段不是有效 SHA → 跳过该组(防御)。"""
    raw = (
        f"notasahashinvalidsha123456789012345678\x00{'a' * 7}\x00an\x00ae\x00cn\x00ce\x00aI\x00cI\x00s\x00b\x00p\x00"
        f"{'a' * 40}\x00{'a' * 7}\x00an\x00ae\x00cn\x00ce\x00aI\x00cI\x00s\x00b\x00p\x00"
    )
    commits = _parse_log_format(raw)
    # 第 1 组被跳过,第 2 组保留
    assert len(commits) == 1
    assert commits[0]["sha"] == "a" * 40


def test_parse_log_format_empty_returns_empty_list():
    """空 raw → 空 commits 列表。"""
    assert _parse_log_format("") == []
    assert _parse_log_format("\n\n\n") == []  # 只有空行


def test_parse_log_format_no_parents():
    """root commit 无 parents。"""
    sha = "a" * 40
    raw = f"{sha}\x00{'a' * 7}\x00an\x00ae\x00cn\x00ce\x00aI\x00cI\x00subj\x00body\x00\x00"
    commits = _parse_log_format(raw)
    assert len(commits) == 1
    assert commits[0]["parents"] == []


def test_parse_log_format_empty_body_becomes_none():
    """body 字段为空 → None(不是 '')。"""
    sha = "a" * 40
    raw = (
        f"{sha}\x00{'a' * 7}\x00an\x00ae\x00cn\x00ce\x00aI\x00cI\x00subj\x00\x00p1\x00"
    )
    commits = _parse_log_format(raw)
    assert commits[0]["body"] is None


def test_parse_log_shortstat_typical():
    raw = " 2 files changed, 10 insertions(+), 3 deletions(-)"
    stats = _parse_log_shortstat(raw)
    assert stats == [{"files": 2, "additions": 10, "deletions": 3}]


def test_parse_log_shortstat_single_file():
    raw = " 1 file changed, 5 insertions(+), 0 deletions(-)"
    stats = _parse_log_shortstat(raw)
    assert stats == [{"files": 1, "additions": 5, "deletions": 0}]


def test_parse_log_shortstat_unparseable_returns_empty():
    """v3.8 (2026-06-24) 行为变更:不匹配的输入不再 emit ``{0,0,0}`` entry。

    历史背景:旧 parser 把每个非空行都 push 成 ``{0,0,0}``,导致
    ``shortstats[:n]`` 把大量噪声行误作 stat,真实 stat 行被错位到错误的
    commit 上(dashboard 显示全 0)。新 parser 只对真正匹配
    ``N files changed`` 模式的行 emit entry,不匹配行 → 跳过。

    此测试覆盖"无法解析"的边界:无 stat 行的输入应返回空列表(由 handler
    路径走 sentinel-based 合并解析器负责统计)。
    """
    raw = " something weird"
    stats = _parse_log_shortstat(raw)
    assert stats == []


def test_parse_log_shortstat_empty_input_returns_empty():
    """空 raw → 空列表(原行为已正确,保留回归覆盖)。"""
    assert _parse_log_shortstat("") == []
    assert _parse_log_shortstat("\n\n\n") == []  # 只有空行


# ──────────────────────────────────────────────────────────
# Regression tests for the git-log shortstat alignment bug.
#
# Background (2026-06-24): `_parse_log_shortstat` previously appended a
# `{files:0,additions:0,deletions:0}` entry for EVERY non-blank line,
# not just lines that matched the "N files changed" pattern. As a
# result, the entry list grew by ~5-8x (commit header + author + date +
# message + file|stat-line per commit), and ``shortstats[:n]`` truncated
# to mostly-zero entries. Subsequent per-index assignment to commits
# then mis-aligned real stat lines with the wrong commit. Symptoms:
# some commits showed correct {files,additions,deletions}, most showed
# all zeros. Fix: parser should only emit entries for lines that match
# the shortstat regex, and the integration handler must handle a
# shorter-than-expected list (merge commits have no stat line).
# ──────────────────────────────────────────────────────────


def test_parse_log_shortstat_realistic_multi_commit():
    """Real ``git log --shortstat`` output for a merge + 2 commits.

    Each commit's block in ``git log --shortstat`` output contains:
      - commit header line(s): commit <sha> / Merge: / Author: / Date:
      - empty line
      - commit message line(s) (indented 4 spaces)
      - empty line
      - `` <file> | N +-`` line(s) (one per file with diff)
      - `` N files changed, M insertions(+), K deletions(-)`` summary
      - blank line separator to next commit
    Merge commits have NO stat block at all (git omits it for merges
    by default — there is no diff summary for the merge itself).

    The parser must emit EXACTLY one entry per non-merge commit (2 here,
    not the 14+ it currently produces by treating each non-blank line as
    a stat line). The integration handler relies on the resulting list
    being aligned with commits in the same order.
    """
    raw = (
        "commit " + "a" * 40 + "\n"
        "Merge: " + "f" * 40 + " " + "e" * 40 + "\n"
        "Author: Foo <foo@bar.com>\n"
        "Date:   Mon Jan 1 12:00:00 2024 +0000\n"
        "\n"
        "    Merge branch 'feature'\n"
        "\n"
        "commit " + "b" * 40 + "\n"
        "Author: Foo <foo@bar.com>\n"
        "Date:   Mon Jan 1 12:00:01 2024 +0000\n"
        "\n"
        "    docs: update README\n"
        "\n"
        " README.md | 4 ++--\n"
        " 1 file changed, 2 insertions(+), 2 deletions(-)\n"
        "\n"
        "commit " + "c" * 40 + "\n"
        "Author: Foo <foo@bar.com>\n"
        "Date:   Mon Jan 1 12:00:02 2024 +0000\n"
        "\n"
        "    feat: add widget\n"
        "\n"
        " widget.py | 10 +++++++++++\n"
        " 1 file changed, 10 insertions(+)\n"
    )
    stats = _parse_log_shortstat(raw)
    # Only 2 stat lines match — merge commit has no stat block.
    assert len(stats) == 2, (
        f"expected 2 entries (merge has no stat), got {len(stats)}: {stats}"
    )
    assert stats[0] == {"files": 1, "additions": 2, "deletions": 2}
    assert stats[1] == {"files": 1, "additions": 10, "deletions": 0}


def test_parse_log_shortstat_zero_changes_line_is_kept():
    """`` 0 files changed`` line should still be captured (real stat, even if zero)."""
    raw = " 0 files changed"
    stats = _parse_log_shortstat(raw)
    assert stats == [{"files": 0, "additions": 0, "deletions": 0}]


def test_parse_log_shortstat_zero_changes_with_insertions():
    """`` 0 files changed, 0 insertions(+), 0 deletions(-)`` edge case."""
    raw = " 0 files changed, 0 insertions(+), 0 deletions(-)"
    stats = _parse_log_shortstat(raw)
    assert stats == [{"files": 0, "additions": 0, "deletions": 0}]


def test_parse_log_shortstat_insertions_only():
    """`` N files changed, M insertions(+)`` (no deletions clause)."""
    raw = " 3 files changed, 42 insertions(+)"
    stats = _parse_log_shortstat(raw)
    assert stats == [{"files": 3, "additions": 42, "deletions": 0}]


def test_parse_log_shortstat_deletions_only():
    """`` N files changed, K deletions(-)`` (no insertions clause — rare but legal)."""
    raw = " 2 files changed, 5 deletions(-)"
    stats = _parse_log_shortstat(raw)
    assert stats == [{"files": 2, "additions": 0, "deletions": 5}]
