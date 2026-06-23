"""tools.security.admin 测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

from tools.security.admin import build_allowed_ids, check_is_admin


def test_build_allowed_ids_empty():
    """空配置返回空 set。"""
    assert build_allowed_ids({}) == set()


def test_build_allowed_ids_extra_comma_separated():
    """英文逗号分隔。"""
    assert build_allowed_ids({"allowed_ids": "a, b, c"}) == {"a", "b", "c"}


def test_build_allowed_ids_extra_chinese_comma():
    """中文逗号分隔。"""
    assert build_allowed_ids({"allowed_ids": "x，y，z"}) == {"x", "y", "z"}


def test_build_allowed_ids_extra_mixed_separators():
    """中英文逗号混合 + 空白容忍。"""
    assert build_allowed_ids({"allowed_ids": " a, b，c , d,,"}) == {"a", "b", "c", "d"}


def test_check_is_admin_true():
    """event.is_admin() 返回 True → 透传。"""
    ev = MagicMock()
    ev.is_admin.return_value = True
    assert check_is_admin(ev) is True


def test_check_is_admin_false():
    """event.is_admin() 返回 False → False。"""
    ev = MagicMock()
    ev.is_admin.return_value = False
    assert check_is_admin(ev) is False


def test_check_is_admin_exception():
    """老版本 AstrBot 没有 is_admin() 或抛异常 → 视为非管理员。"""
    ev = MagicMock()
    ev.is_admin.side_effect = AttributeError("no is_admin")
    assert check_is_admin(ev) is False
