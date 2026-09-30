"""tests/test_stdio_allowlist.py — stdio 白名单探针(2026-09-30)。

WHY: 旧实现无条件把 {codegraph,node} / {python,pythonw,vivado_mcp} 写进
``ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS``。而 AstrBot 的白名单解析是
"env(非空) > mcp_settings.stdio_allowlist(非空) > 内置默认",两处都是整体
替换而非 union —— 于是"用户什么都没设"的场景下,内置默认 17 项被挤成 2~5 项,
Dashboard 上配置的名单也被一并遮蔽。

本文件锁定新语义:探针驱动,能过就不写;且**永不写 env**。
"""

from __future__ import annotations

import logging
import os

import pytest

from tools import _stdio_allowlist

ENV = "ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"

# 取一份"内置默认"的样子(core 里 17 项的精简版,够覆盖断言即可)
DEFAULTS = frozenset(
    {"python", "pythonw", "python3", "node", "codegraph", "npx", "uv", "uvx"}
)


def _normalize(name: str) -> str:
    """与 core ``_normalize_stdio_command_name`` 同语义的简化版。"""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _policy(*, allowed=(), denied=(), defaults=DEFAULTS):
    return _stdio_allowlist._Policy(
        normalize=_normalize,
        allowlist=lambda: set(allowed),
        denylist=lambda: set(denied),
        defaults=frozenset(defaults),
    )


@pytest.fixture
def env_clean(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    return ENV


@pytest.fixture
def config_state(monkeypatch):
    """把 config 读写换成内存实现,并记录写入。"""
    state: dict = {"items": [], "writes": [], "ok": True}

    monkeypatch.setattr(
        _stdio_allowlist, "_read_config_allowlist", lambda: list(state["items"])
    )

    def _write(items):
        state["writes"].append(list(items))
        if state["ok"]:
            state["items"] = list(items)
        return state["ok"]

    monkeypatch.setattr(_stdio_allowlist, "_write_config_allowlist", _write)
    return state


@pytest.fixture
def install_policy(monkeypatch):
    def _install(**kwargs):
        p = _policy(**kwargs)
        monkeypatch.setattr(_stdio_allowlist, "_load_policy", lambda: p)
        return p

    return _install


# ── 零写入:命令已经在生效白名单里 ──────────────────────────


def test_allowed_command_writes_nothing(env_clean, config_state, install_policy):
    install_policy(allowed={"node"})
    assert _stdio_allowlist.ensure_stdio_command(r"C:\npm\node.exe") is True
    assert config_state["writes"] == []
    assert ENV not in os.environ


def test_allowed_command_keeps_user_env_untouched(
    monkeypatch, env_clean, config_state, install_policy
):
    monkeypatch.setenv(ENV, "uv,npx")
    install_policy(allowed={"uv", "npx"})
    assert _stdio_allowlist.ensure_stdio_command("uv") is True
    assert os.environ[ENV] == "uv,npx"
    assert config_state["writes"] == []


# ── 黑名单优先:写了也没用,直接拒 ────────────────────────────


def test_denylisted_command_refused(env_clean, config_state, install_policy):
    install_policy(allowed=set(), denied={"bash"})
    assert _stdio_allowlist.ensure_stdio_command("/bin/bash") is False
    assert config_state["writes"] == []
    assert ENV not in os.environ


# ── 用户 pin 了 env:只告警,绝不自作主张拓宽 ─────────────────


def test_user_pinned_env_is_not_rewritten(
    monkeypatch, env_clean, config_state, install_policy, caplog
):
    monkeypatch.setenv(ENV, "uv")
    install_policy(allowed={"uv"})  # 缺 node

    with caplog.at_level(logging.WARNING):
        assert _stdio_allowlist.ensure_stdio_command(r"C:\npm\node.exe") is False

    assert os.environ[ENV] == "uv"  # 原样不动
    assert config_state["writes"] == []  # 也不去动 config
    assert any(ENV in r.message for r in caplog.records)


# ── 用户在 Dashboard 配过:追加到他的名单 ────────────────────


def test_config_list_gets_command_appended(env_clean, config_state, install_policy):
    config_state["items"] = ["uv"]
    install_policy(allowed={"uv"})

    assert _stdio_allowlist.ensure_stdio_command(r"C:\npm\node.exe") is True
    assert config_state["writes"] == [["uv", "node"]]
    assert ENV not in os.environ  # env 永不写


def test_config_append_is_idempotent(env_clean, config_state, install_policy):
    config_state["items"] = ["uv"]
    install_policy(allowed={"uv"})

    assert _stdio_allowlist.ensure_stdio_command("node") is True
    # 第二次:config 已含 node(用写入结果当作新的生效名单)
    install_policy(allowed=set(config_state["items"]))
    assert _stdio_allowlist.ensure_stdio_command("node") is True
    assert len(config_state["writes"]) == 1


# ── 用户未表态:把"内置默认 ∪ 命令"落成 Dashboard 名单 ────────


def test_empty_config_materializes_defaults_plus_command(
    env_clean, config_state, install_policy
):
    install_policy(allowed=set(), defaults={"uv", "npx"})

    assert _stdio_allowlist.ensure_stdio_command("node") is True
    assert config_state["writes"] == [sorted({"uv", "npx", "node"})]
    assert ENV not in os.environ


def test_defaults_are_preserved_not_replaced(env_clean, config_state, install_policy):
    """关键回归:写入后生效集合不能比原来更窄(旧实现就是这么丢默认项的)。"""
    install_policy(allowed=set(), defaults=DEFAULTS)
    assert _stdio_allowlist.ensure_stdio_command("mycmd") is True

    effective = set(config_state["writes"][0])
    assert DEFAULTS <= effective
    assert "mycmd" in effective


def test_write_failure_returns_false(env_clean, config_state, install_policy):
    config_state["ok"] = False
    install_policy(allowed=set(), defaults={"uv"})
    assert _stdio_allowlist.ensure_stdio_command("node") is False


# ── 老 core 没有白名单机制 ──────────────────────────────────


def test_old_core_without_mechanism_is_noop(
    env_clean, config_state, monkeypatch
):
    monkeypatch.setattr(_stdio_allowlist, "_load_policy", lambda: None)
    assert _stdio_allowlist.ensure_stdio_command("node") is True
    assert config_state["writes"] == []
    assert ENV not in os.environ


def test_blank_command_is_not_written(env_clean, config_state, install_policy):
    install_policy(allowed=set(), defaults={"uv"})
    assert _stdio_allowlist.ensure_stdio_command("") is True
    assert _stdio_allowlist.ensure_stdio_command("   ") is True
    assert config_state["writes"] == []


# ── 集成:探针必须真的走 core 的解析入口 ─────────────────────


def test_probe_reads_real_core_resolver(env_clean, config_state, monkeypatch):
    core = pytest.importorskip("astrbot.core.agent.mcp_client")
    if not hasattr(core, "_get_stdio_command_allowlist"):
        pytest.skip("stub astrbot:无真实白名单机制")

    monkeypatch.setattr(
        core, "_normalize_stdio_command_name", lambda c: "node", raising=False
    )
    monkeypatch.setattr(
        core, "_get_stdio_command_allowlist", lambda: {"node"}, raising=False
    )
    monkeypatch.setattr(
        core, "_get_stdio_command_denylist", lambda: set(), raising=False
    )

    assert _stdio_allowlist.ensure_stdio_command(r"C:\x\node.exe") is True
    assert config_state["writes"] == []
    assert ENV not in os.environ


def test_real_launcher_commands_need_no_write(monkeypatch, env_clean):
    """回归:插件两个真实启动命令都被内置默认覆盖 → 零写入、env 不变。

    (旧实现就是在这里无条件写 env,把内置默认 17 项挤成 2~5 项。)
    """
    import sys

    core = pytest.importorskip("astrbot.core.agent.mcp_client")
    if not hasattr(core, "_DEFAULT_STDIO_COMMAND_ALLOWLIST"):
        pytest.skip("stub astrbot:无内置默认名单")
    if _stdio_allowlist._read_config_allowlist():
        pytest.skip("本机 Dashboard 已配 stdio_allowlist,不作零写入断言")

    written: list = []
    monkeypatch.setattr(
        _stdio_allowlist, "_write_config_allowlist", lambda items: written.append(items)
    )

    for command in (r"C:\npm\node_modules\x\node.exe", "/usr/local/bin/codegraph", sys.executable):
        assert _stdio_allowlist.ensure_stdio_command(command) is True

    assert written == []
    assert ENV not in os.environ
