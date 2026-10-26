"""发布元数据一致性守卫(2026-10-17, v2.31.3)。

WHY: 版本号散落在 `metadata.yaml` / `README.md` / `AGENTS.md` / `CHANGELOG.md` 四处,
历史上确实漂移过(README 长期停在 v2.23.2,而 metadata 已是 v2.31.2)。Dashboard 插件页的
「更新日志」按钮直接读取 `CHANGELOG.md` 并渲染,版本对不上会让用户看到错位的日志——
这里把这层契约变成可失败的断言,而不是靠发版时记得手工核对。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML 未安装(AstrBot 宿主环境自带)")

ROOT = Path(__file__).resolve().parent.parent
_SEMVER = re.compile(r"^v\d+\.\d+\.\d+$")


def _metadata() -> dict:
    return yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))


def test_metadata_version_is_semver():
    assert _SEMVER.match(_metadata()["version"])


def test_changelog_top_entry_matches_metadata_version():
    """CHANGELOG.md 的第一条版本必须是当前版本(插件页读它)。"""
    version = _metadata()["version"]
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## \[(v[\d.]+)\]", text, flags=re.MULTILINE)
    assert headings, "CHANGELOG.md 缺少 `## [vX.Y.Z] - YYYY-MM-DD` 形式的版本标题"
    assert headings[0] == version, (
        f"CHANGELOG 首条版本 {headings[0]} != metadata.yaml {version}"
    )


def test_readme_and_agents_banner_match_metadata_version():
    """README / AGENTS.md 顶部的「当前版本」不得滞后。"""
    version = _metadata()["version"]
    for name in ("README.md", "AGENTS.md"):
        head = "\n".join(
            (ROOT / name).read_text(encoding="utf-8").splitlines()[:6]
        )
        assert f"当前版本: {version}" in head, f"{name} 顶部版本未同步到 {version}"
