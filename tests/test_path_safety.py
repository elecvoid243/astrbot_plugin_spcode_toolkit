"""tools/_path_safety.py 单元测试(v2.4 新增)。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools._path_safety import (  # noqa: E402
    is_path_safe,
)


# ── is_path_safe 基础行为 ─────────────────────────


def test_safe_path_returns_true():
    ok, reason = is_path_safe("/tmp/some/innocent/path")
    assert ok is True
    assert reason == ""


def test_nonexistent_path_is_ok():
    """不存在的路径也允许(用户可能还没创建)。"""
    ok, _ = is_path_safe("/tmp/definitely/does/not/exist/xyz")
    assert ok is True


# ── 系统黑名单(Windows 路径) ──────────────────────


def test_windows_path_blocked():
    ok, reason = is_path_safe("C:/Windows/System32")
    assert ok is False
    assert "系统敏感目录" in reason


def test_windows_path_case_insensitive():
    """大小写不敏感:C:/windows 也要被拒。"""
    ok, reason = is_path_safe("C:/windows/system32")
    assert ok is False


def test_windows_program_files_blocked():
    ok, _ = is_path_safe("C:/Program Files (x86)/Some App")
    assert ok is False


def test_windows_normal_path_ok():
    ok, _ = is_path_safe("C:/Users/me/Documents/project")
    assert ok is True


# ── 系统黑名单(POSIX 路径) ──────────────────────────


def test_posix_etc_blocked():
    ok, reason = is_path_safe("/etc/passwd")
    assert ok is False
    assert "系统敏感目录" in reason


def test_posix_usr_bin_blocked():
    ok, _ = is_path_safe("/usr/bin/something")
    assert ok is False


def test_posix_proc_blocked():
    ok, _ = is_path_safe("/proc/1/status")
    assert ok is False


def test_posix_normal_path_ok():
    ok, _ = is_path_safe("/home/user/myproject")
    assert ok is True


# ── 关键修复: /etc-backup 不应被误判为 /etc ──────────


def test_etc_backup_not_blocked():
    """朴素 startswith 的 bug: /etc-backup 会被误判为 /etc。
    修复后只匹配 /etc 或 /etc/...。"""
    ok, _ = is_path_safe("/etc-backup")
    assert ok is True


def test_etc_subpath_blocked():
    """/etc 本身和 /etc/xxx 子路径要拒。"""
    ok1, _ = is_path_safe("/etc")
    ok2, _ = is_path_safe("/etc/passwd")
    assert ok1 is False
    assert ok2 is False


def test_usr_subpath_blocked():
    """/usr 本身 + 所有 /usr/xxx 子路径都拒。"""
    ok1, _ = is_path_safe("/usr/bin/foo")
    ok2, _ = is_path_safe("/usr/local/bin")
    ok3, _ = is_path_safe("/usr")
    assert ok1 is False
    assert ok2 is False  # 也在 /usr 黑名单下
    assert ok3 is False


def test_usrbackup_not_blocked():
    """/usrbackup 不以 /usr/ 开头(注意 /usr 后跟的不是 / ),不应被拒。"""
    ok, _ = is_path_safe("/usrbackup")
    assert ok is True


# ── 用户自定义黑名单 ────────────────────────────


def test_user_blacklist_blocks(tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    ok, reason = is_path_safe(str(secret), user_blacklist=[str(tmp_path)])
    assert ok is False
    assert "用户黑名单" in reason


def test_user_blacklist_unrelated_ok(tmp_path):
    other = tmp_path / "ok_project"
    other.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()
    ok, _ = is_path_safe(str(other), user_blacklist=[str(secret)])
    assert ok is True


def test_user_blacklist_resolve_handles_nonexistent(tmp_path):
    """黑名单前缀本身不存在时,跳过(不报错)。"""
    fake_prefix = str(tmp_path / "does_not_exist")
    target = tmp_path / "anything"
    target.mkdir()
    ok, _ = is_path_safe(str(target), user_blacklist=[fake_prefix])
    assert ok is True  # 不存在的黑名单 prefix 被跳过


# ── 跨平台路径分隔符 ─────────────────────────────


def test_forward_slash_normalized_to_os_sep(tmp_path):
    """传入 C:/Windows 应被识别为系统目录(Windows 上)。"""
    ok, _ = is_path_safe("C:/Windows/System32")
    assert ok is False  # 黑名单里有 c:\windows


def test_windows_etc_subpath_with_forward_slash_blocked(tmp_path):
    """POSIX 路径,传入 /etc/hosts 用 / 也能匹配。"""
    ok, _ = is_path_safe("/etc/hosts")
    assert ok is False
