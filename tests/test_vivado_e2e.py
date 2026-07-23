"""tests/test_vivado_e2e.py — vivado-mcp 集成测试 (skipif 未装)。

PR-5 (2026-07-23): E2E 验证 vivado-mcp 安装与基本可调用。
"""

from __future__ import annotations

import importlib.util

import pytest

_SKIP_REASON = "vivado-mcp 未安装, 跳过 E2E"


@pytest.fixture
def skip_if_no_vivado_mcp():
    if importlib.util.find_spec("vivado_mcp") is None:
        pytest.skip(_SKIP_REASON)


@pytest.mark.skipif(
    importlib.util.find_spec("vivado_mcp") is None,
    reason=_SKIP_REASON,
)
class TestVivadoE2E:
    """vivado-mcp 真启动验证 (CI 默认 skip, 本地装了才跑)。"""

    def test_vivado_mcp_imports(self, skip_if_no_vivado_mcp):
        import vivado_mcp  # noqa: F401

        assert hasattr(vivado_mcp, "server")

    def test_config_find_vivado_works(self, skip_if_no_vivado_mcp):
        from vivado_mcp import config

        # 真 Vivado 未装应抛 FileNotFoundError
        with pytest.raises(FileNotFoundError):
            config.find_vivado()
