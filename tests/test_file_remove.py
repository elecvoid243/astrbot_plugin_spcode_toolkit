"""file_remove 工具测试。

覆盖评审报告附录 A 的 6 个关键场景 + 修复 bug 时的回归用例。
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 把项目根加入 sys.path，便于直接 `from tools import file_remove`
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import file_remove  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_send2trash(monkeypatch):
    """默认对所有用例 monkeypatch send2trash,避免污染真实回收站。

    单文件/目录删除的成功路径用例可直接读取 `mock_send2trash` 做断言。
    失败/拦截路径用例默认不被调用(`mock_send2trash.send2trash.assert_not_called()`)。
    """
    mock_send2trash = MagicMock()
    monkeypatch.setattr(file_remove, "send2trash", mock_send2trash)
    return mock_send2trash


# ── 1. 单文件删除 ────────────────────────────────────


def test_remove_single_file(tmp_path: Path, _mock_send2trash):
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    r = file_remove.remove(str(f))
    assert r["ok"] is True
    assert r["deleted"] == 1
    _mock_send2trash.send2trash.assert_called_once_with(str(f))


# ── 2. 目录删除未确认 → proposal ──────────────────────


def test_remove_dir_requires_confirm(tmp_path: Path, _mock_send2trash):
    d = tmp_path / "dir"
    d.mkdir()
    r = file_remove.remove(str(d))  # confirm=False
    assert r["ok"] is False
    assert "proposal" in r
    assert r.get("options") == ["confirm_delete", "cancel"]
    _mock_send2trash.send2trash.assert_not_called()


# ── 3. 目录删除已确认 → 成功 ─────────────────────────


def test_remove_dir_with_confirm(tmp_path: Path, _mock_send2trash):
    d = tmp_path / "dir"
    d.mkdir()
    (d / "a").write_text("x", encoding="utf-8")
    (d / "b").write_text("y", encoding="utf-8")
    r = file_remove.remove(str(d), confirm=True)
    assert r["ok"] is True
    assert r["deleted"] == 2
    _mock_send2trash.send2trash.assert_called_once_with(str(d))


# ── 4. 系统目录拦截 ──────────────────────────────────


def test_remove_blocks_system_dir(_mock_send2trash):
    if platform.system() != "Windows":
        # Linux 平台测试 /etc（黑名单中已存在）
        r = file_remove.remove("/etc")
    else:
        r = file_remove.remove("C:/Windows/System32")
    assert r["ok"] is False
    assert "blocked_by" in r.get("evidence", {})
    _mock_send2trash.send2trash.assert_not_called()


# ── 5. 路径穿越拦截 ──────────────────────────────────


def test_remove_rejects_traversal(tmp_path: Path, _mock_send2trash):
    target = str(tmp_path / ".." / "etc")
    r = file_remove.remove(target)
    assert r["ok"] is False
    assert ".." in r["error"]
    _mock_send2trash.send2trash.assert_not_called()


# ── 6. 批量超限 → proposal ──────────────────────────


def test_remove_large_dir_returns_proposal(tmp_path: Path, _mock_send2trash):
    d = tmp_path / "big"
    d.mkdir()
    for i in range(60):
        (d / f"f{i}").write_text("x", encoding="utf-8")
    r = file_remove.remove(str(d), confirm=True, max_items=50)
    assert r["ok"] is False
    assert "proposal" in r
    assert r["evidence"]["file_count"] == 60
    assert r.get("options") == ["confirm_batch_delete", "cancel"]
    _mock_send2trash.send2trash.assert_not_called()


# ── 7. _human_size 浮点精度（回归测试） ─────────────


def test_human_size_no_precision_loss():
    # 评审 P0 修复项：整数整除导致 1500B 显示为 1KB
    # 注意：{:.1f}.replace(".0", "") 的实际行为——1.95KB 会显示为 "2KB"
    assert file_remove._human_size(512) == "512B"
    assert file_remove._human_size(1023) == "1023B"
    assert file_remove._human_size(1500) == "1.5KB"  # 修复前会显示 "1KB"
    assert file_remove._human_size(2000) == "2KB"  # 修复前会显示 "1KB"
    assert file_remove._human_size(2500) == "2.4KB"
    assert file_remove._human_size(1024 * 1024) == "1MB"


# ── 8. UNC / \\?\ 路径拦截（仅 Windows 有意义） ──────


def test_remove_unc_path_rejected(_mock_send2trash):
    if platform.system() != "Windows":
        # POSIX 上 UNC 无意义，跳过以保持跨平台可跑
        return
    for bad in (r"\\server\share\file", r"\\?\C:\Windows\System32"):
        r = file_remove.remove(bad)
        assert r["ok"] is False
        assert "UNC" in r["error"] or "扩展长度" in r["error"]
    _mock_send2trash.send2trash.assert_not_called()


# ── 9. macOS 系统目录拦截 ────────────────────────────


def test_remove_macos_system_dir_blocked(_mock_send2trash):
    # macOS 上验证 /System 拦截；其他平台跳过
    if platform.system() != "Darwin":
        return
    r = file_remove.remove("/System/Library")
    assert r["ok"] is False
    assert r.get("evidence", {}).get("blocked_by")
    _mock_send2trash.send2trash.assert_not_called()


# ── 10. Windows ProgramData 拦截 ─────────────────────


def test_remove_windows_programdata_blocked(_mock_send2trash):
    if platform.system() != "Windows":
        return
    r = file_remove.remove("C:/ProgramData/Microsoft")
    assert r["ok"] is False
    assert r.get("evidence", {}).get("blocked_by") == "C:/ProgramData"
    _mock_send2trash.send2trash.assert_not_called()


# ── 11. 用户自定义黑名单（特性新增） ─────────────────


def test_remove_user_blacklist_blocks_dir(tmp_path: Path, _mock_send2trash):
    """用户黑名单中的目录（含 confirm）应被拦截。"""
    d = tmp_path / "secret"
    d.mkdir()
    (d / "a.txt").write_text("x", encoding="utf-8")
    r = file_remove.remove(
        str(d), confirm=True, custom_blacklist=[str(tmp_path / "secret")]
    )
    assert r["ok"] is False
    assert r.get("evidence", {}).get("blocked_by", "").startswith("user:")
    assert d.exists() is True  # 目录未被删除
    _mock_send2trash.send2trash.assert_not_called()


def test_remove_user_blacklist_blocks_file(tmp_path: Path, _mock_send2trash):
    """用户黑名单中的文件应被拦截。"""
    f = tmp_path / "protected.txt"
    f.write_text("important", encoding="utf-8")
    r = file_remove.remove(str(f), custom_blacklist=[str(tmp_path / "protected.txt")])
    assert r["ok"] is False
    assert r.get("evidence", {}).get("blocked_by", "").startswith("user:")
    assert f.exists() is True
    _mock_send2trash.send2trash.assert_not_called()


def test_remove_user_blacklist_prefix_match(tmp_path: Path, _mock_send2trash):
    """黑名单前缀应能拦住子目录/子文件。"""
    parent = tmp_path / "vault"
    parent.mkdir()
    child = parent / "inner" / "leaf.txt"
    child.parent.mkdir()
    child.write_text("x", encoding="utf-8")
    r = file_remove.remove(str(child), custom_blacklist=[str(tmp_path / "vault")])
    assert r["ok"] is False
    assert r.get("evidence", {}).get("blocked_by", "").startswith("user:")
    assert child.exists() is True
    _mock_send2trash.send2trash.assert_not_called()


def test_remove_user_blacklist_empty_allows(tmp_path: Path):
    """空列表 = 不加额外限制。"""
    f = tmp_path / "ok.txt"
    f.write_text("x", encoding="utf-8")
    r = file_remove.remove(str(f), custom_blacklist=[])
    assert r["ok"] is True
    assert r["deleted"] == 1


def test_remove_user_blacklist_none_allows(tmp_path: Path):
    """None = 不加额外限制（与空列表等价）。"""
    f = tmp_path / "ok.txt"
    f.write_text("x", encoding="utf-8")
    r = file_remove.remove(str(f), custom_blacklist=None)
    assert r["ok"] is True
    assert r["deleted"] == 1


def test_remove_user_blacklist_unrelated_allows(tmp_path: Path):
    """黑名单与目标路径无关 → 允许删除。"""
    f = tmp_path / "ok.txt"
    f.write_text("x", encoding="utf-8")
    r = file_remove.remove(str(f), custom_blacklist=[str(tmp_path / "other_path")])
    assert r["ok"] is True
    assert r["deleted"] == 1


def test_remove_user_blacklist_distinguishes_from_builtin(_mock_send2trash):
    """用户黑名单 blocked_by 应与内置黑名单区分（带 'user:' 前缀）。"""
    r = file_remove.remove(
        "C:/Windows/System32",
        custom_blacklist=["C:/Windows"],  # 同时在内置和用户黑名单
    )
    assert r["ok"] is False
    # 内置黑名单应先命中（先检查内置）
    assert r.get("evidence", {}).get("blocked_by") == "C:/Windows"
    # 确认 error 提到"系统目录"而非"用户配置"
    assert "系统目录" in r["error"] or "受保护" in r["error"]
    _mock_send2trash.send2trash.assert_not_called()


def test_remove_user_blacklist_error_message(tmp_path: Path, _mock_send2trash):
    """用户黑名单拦截时，错误信息应明确提及"用户配置"。"""
    r = file_remove.remove(
        str(tmp_path / "anything"),
        custom_blacklist=[str(tmp_path / "anything")],
    )
    assert r["ok"] is False
    assert "用户" in r["error"] or "用户配置" in r["proposal"]
    _mock_send2trash.send2trash.assert_not_called()


def test_remove_returns_error_when_trash_fails(tmp_path: Path, _mock_send2trash):
    """send2trash 抛 OSError(常见:Linux 缺 trash-cli) → 返回结构化错误,不 fallback 到物理删除。"""
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    _mock_send2trash.send2trash.side_effect = OSError("trash-cli not found")

    r = file_remove.remove(str(f))

    assert r["ok"] is False
    assert "回收站不可用" in r["error"]
    assert "trash-cli" in r["error"]
    # 文件仍在原路径(没有 fallback 到 os.remove)
    assert f.exists() is True


def test_remove_returns_error_on_permission_denied(tmp_path: Path, _mock_send2trash):
    """send2trash 抛 PermissionError → 明确提示权限,不走 OSError 兜底。"""
    f = tmp_path / "locked.txt"
    f.write_text("x", encoding="utf-8")
    _mock_send2trash.send2trash.side_effect = PermissionError("Access denied")

    r = file_remove.remove(str(f))

    assert r["ok"] is False
    assert "无权限移入回收站" in r["error"]
