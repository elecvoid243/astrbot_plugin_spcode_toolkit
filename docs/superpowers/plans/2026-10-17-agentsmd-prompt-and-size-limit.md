# agentsmd 生成提示词与长度护栏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 Codex `/init` 风格重写两套 AGENTS.md 生成模板、把 `git log` 并入生成上下文，并为 `/agentsmd init` / `update` 的自动生成加一条"超限 → 压缩一次 → 仍超限则按小节截断并标注"的长度护栏。

**Architecture:** 全部改动收在 `tools/agentsmd/` 子系统内：`_core.py` 新增纯函数（模板文本、`collect_git_history`、`build_project_context`、`build_output_constraints`、`resolve_max_chars`、`truncate_at_section`）与一个带 LLM 的 `fit_generated_content`；`_handlers.py` 只负责把这两段接到 `init` / `update` 的写盘之前。注入侧（`on_llm_request`）零改动。

**Tech Stack:** Python 3.10+（`list[str]` / `X | None`）、asyncio 子进程、AstrBot Provider `text_chat`、pytest（`unittest.mock.AsyncMock` + 真实临时 git 仓库）、ruff。

**Spec:** `docs/superpowers/specs/2026-10-17-agentsmd-prompt-and-size-limit-design.md`

## Global Constraints

- 只改 `tools/agentsmd/_core.py`、`tools/agentsmd/_handlers.py`、`tools/agentsmd/__init__.py`、`main.py`（仅 `_DEFAULT_CONFIG`）、`_conf_schema.json`（仅 `agentsmd.items`）、`tests/conftest.py`、`tests/test_agentsmd.py`、`tests/test_agentsmd_subsystem.py`。**不碰** `tools/_config.py::DEFAULT_CONFIG`（会造出第二个事实源）。
- 版本号保持 **v2.31.2**：不改 `metadata.yaml`，不改 `README.md`，不 bump。
- 上限量纲 = **字符数**，默认 `agentsmd_max_chars = 10000`，`0` = 不限制。
- 约束只作用于 `init` / `update` 的**自动生成**。`on_llm_request` 注入侧不截断、不裁剪、不改签名。
- `generate_agents_md_via_llm` / `fit_generated_content` 的所有新参数必须有默认值：旧调用（含 12 个既有 `test_generate_*` 用例）不传即保持旧行为。
- git 采集**无配置开关**；任何失败（非仓库 / 无 git / 非零退出 / 3s 超时 / 输出为空）一律静默返回 `""`，不抛异常、不写 ERROR 日志。
- 不新增 Web API 端点、不改 ReasonCode、不改 codegraph / project 子系统。
- 只做**本地提交**：`git commit` 即可，禁止 `git push`、禁止发 PR。
- 每个 Task 结束前必须：`ruff check .` 无 error + 相关 pytest 全绿。
- 测试命令（仓库根，Windows 首选显式解释器）：
  - `pytest tests/test_agentsmd.py tests/test_agentsmd_subsystem.py -q`
  - 若 `python` 被解析成 Windows Store 桩，改用 `D:/anaconda3/envs/astrbot/python.exe -m pytest tests/test_agentsmd.py tests/test_agentsmd_subsystem.py -q`
  - 基线：本计划开始前该命令为 **108 passed**。

## Review Focus

1. **`plugin._git_binary()` 在假插件上是 MagicMock**：`tests/test_agentsmd_subsystem.py` 里两个真走 provider 的 init 用例（`test_init_picks_template_by_dir_kind`、`test_init_custom_template_overrides_dir_kind`）若不被显式补上 `fake_plugin._git_binary = lambda: "git"`，`asyncio.create_subprocess_exec` 会拿到 MagicMock 作 argv[0] 抛 `TypeError`（不是 `OSError`，捕获不到）→ Task 7 必须同步修这两处。
2. **截断预算必须把标注长度算进去**：先扣标注再切正文，否则 `len(out) > max_chars`，护栏形同虚设。Task 4 的 `test_truncate_hard_cut_when_no_boundary` 断言长度**精确等于**上限。
3. **压缩失败必须回退原文**：压缩返回空 / 更长 / 仍超限，都要拿**原始草稿**去截断，不能把半成品写盘。Task 5 断言 `out.startswith("A")`。
4. **压缩 prompt 不能用 `str.format`**：草稿正文里可能有 `{}`（JSON / 代码块），`.format()` 会抛 `KeyError`。用 f-string 或函数拼接。
5. **`max_chars = 0` 全链路直通**：`build_output_constraints(0)` 不出数字、`fit_generated_content(..., 0)` 不调 LLM。同时这是 `generate_agents_md_via_llm` 的默认值，保证旧调用行为不变。
6. **provider 为 None 时不能碰 git**：`generate_agents_md_via_llm` 的 `if provider is None: return DEFAULT_AGENTS_MD` 必须排在 `build_project_context` 之前（既有用例 `test_generate_returns_default_when_provider_none` 传的是不存在的 `/tmp`）。

## File Structure

| 文件 | 责任 | 本计划涉及 |
|---|---|---|
| `tools/agentsmd/_core.py` | 模板文本、目录扫描、git 历史采集、prompt 装配、长度护栏纯逻辑 | 重写 3 段文本；新增 7 个函数 / 常量；`generate_agents_md_via_llm` 加 2 个 kwarg |
| `tools/agentsmd/_handlers.py` | `/agentsmd` 4 个子命令 + 注入钩子 | `init` / `update` 接护栏、消息后缀、`_fit_suffix` 助手 |
| `tools/agentsmd/__init__.py` | 包门面重导出 | 导出 7 个新符号并进 `__all__` |
| `main.py` | 插件入口与默认配置 | `_DEFAULT_CONFIG` 加 `agentsmd_max_chars`（仅 1 行） |
| `_conf_schema.json` | 配置 schema | `agentsmd.items` 加 `agentsmd_max_chars` |
| `tests/conftest.py` | 共享 fixture | `plugin._config` 补默认键（1 行） |
| `tests/test_agentsmd.py` | `_core` 单测（823 行，与 `tools/agentsmd/_core.py` 对应） | 模板 / git / prompt / 护栏 / 截断用例 |
| `tests/test_agentsmd_subsystem.py` | `_state` / `_handlers` / 门面单测（646 行） | handler 级超限用例 + 修 2 个既有用例的 `_git_binary` |

---

### Task 1: 按 Codex 风格重写两套模板

**Files:**
- Modify: `tools/agentsmd/_core.py:36-45`（`DEFAULT_INIT_TEMPLATE`）、`:46-56`（`DEFAULT_INIT_TEMPLATE_GENERAL`）、`:58-85`（`DEFAULT_AGENTS_MD` 首行）
- Test: `tests/test_agentsmd.py`（追加到 `test_constants_not_empty` 之后）

**Interfaces:**
- Consumes: 无（纯文本）
- Produces: `DEFAULT_INIT_TEMPLATE` / `DEFAULT_INIT_TEMPLATE_GENERAL` / `DEFAULT_AGENTS_MD` —— **符号名不变**，只有文本变。`pick_init_template` / `resolve_init_template` 签名与选择逻辑不变。

- [ ] **Step 1: 写失败的模板结构测试**

追加到 `tests/test_agentsmd.py` 的 `test_constants_not_empty` 之后：

```python
# ── 2026-10-17: Codex 风格模板(章节清单 + 反编造 + 篇幅) ──


def test_code_template_has_section_checklist():
    """代码目录模板必须列出可增删的章节清单(Codex prompt 的核心结构)。"""
    for heading in (
        "## 项目结构与模块组织",
        "## 构建、测试与本地开发",
        "## 代码风格与命名约定",
        "## 测试规范",
        "## 提交与 PR 规范",
    ):
        assert heading in DEFAULT_INIT_TEMPLATE, heading


def test_code_template_states_language_length_and_no_fabrication():
    assert "# 项目指南" in DEFAULT_INIT_TEMPLATE
    assert "1200–2500" in DEFAULT_INIT_TEMPLATE
    assert "不要编造" in DEFAULT_INIT_TEMPLATE
    assert "提交历史" in DEFAULT_INIT_TEMPLATE  # 明确要求依据 git log 推断约定


def test_general_template_has_section_checklist():
    for heading in ("## 目录用途", "## 内容组织", "## 维护约定"):
        assert heading in DEFAULT_INIT_TEMPLATE_GENERAL, heading
    assert "# 目录指南" in DEFAULT_INIT_TEMPLATE_GENERAL
    assert "800–2000" in DEFAULT_INIT_TEMPLATE_GENERAL
    assert "不要编造" in DEFAULT_INIT_TEMPLATE_GENERAL


def test_default_agents_md_first_line_matches_template_heading():
    """兜底内容的一级标题与生成模板一致(都叫 项目指南)。"""
    assert DEFAULT_AGENTS_MD.splitlines()[0] == "# 项目指南"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agentsmd.py -k "template or default_agents_md" -v`
Expected: 4 个用例 FAIL（`assert '## 项目结构与模块组织' in '请分析此代码库并创建一个 AGENTS.md 文件,包含:...'`）

- [ ] **Step 3: 替换三段文本**

把 `tools/agentsmd/_core.py` 的 `DEFAULT_INIT_TEMPLATE`（当前 `:36-45`）整体替换为：

```python
# 默认的 AGENTS.md 生成 prompt 模板(代码项目目录)。
# 2026-10-17: 按 Codex `/init` 风格重写 —— 章节清单 + 反编造 + 篇幅约束。
# 有意不抄 Codex 的"检查文件是否已存在":handler 已硬性拒绝覆盖,写进 prompt 只会浪费一次判断。
DEFAULT_INIT_TEMPLATE = """请分析当前代码库，并生成一份 AGENTS.md，作为在此仓库中工作的贡献者指南。

写作要求：
- 首行使用一级标题「# 项目指南」
- 使用 Markdown 标题分层，全文控制在 1200–2500 字
- 每条说明都要具体、可执行，避免"保持代码整洁"这类空泛表述
- 只写你在仓库中实际看到的事实；不要编造不存在的命令、脚本或目录
- 文件使用与用户相同的语言，默认中文
- 直接输出文件内容，不要额外说明，也不要用 markdown 代码块包裹全文

建议章节（按实际情况增删，不适用的直接省略）：
## 项目结构与模块组织
## 构建、测试与本地开发
- 构建、运行、跑测试的命令；特别是"只跑单个测试"的命令
## 代码风格与命名约定
- 缩进、导入顺序、格式化与 lint 工具、命名习惯、错误处理约定
## 测试规范
- 测试框架、用例命名、新增代码的测试要求
## 提交与 PR 规范
- 依据下方提供的提交历史，总结真实的提交信息约定（例如是否有 type(scope) 前缀）
- 若历史中看不出约定，写"无明确约定"，不要虚构
## 其他（可选）
- 安全与配置注意事项、架构要点、容易踩的坑
"""
```

把 `DEFAULT_INIT_TEMPLATE_GENERAL`（当前 `:46-56`，含上方三行注释）整体替换为：

```python
# 非代码目录(文档库/资料库/笔记库等)的生成 prompt 模板。
# WHY: init 不再要求目录含代码文件,纯文档目录走代码模板会让 LLM
# 编造不存在的构建命令;改用面向内容组织的指令集。
# 2026-10-17: 同上按 Codex 风格重写,篇幅上限低于代码模板。
DEFAULT_INIT_TEMPLATE_GENERAL = """请分析当前目录，并生成一份 AGENTS.md，作为在此目录中工作的代理指南。

写作要求：
- 首行使用一级标题「# 目录指南」
- 使用 Markdown 标题分层，全文控制在 800–2000 字
- 只描述目录中真实存在的内容；不要编造不存在的构建/测试命令
- 文件使用与用户相同的语言，默认中文
- 直接输出文件内容，不要额外说明，也不要用 markdown 代码块包裹全文

建议章节（按实际情况增删）：
## 目录用途
## 内容组织
- 各子目录与关键文件分别承担什么职责、命名有何规律
## 维护约定
- 写作语言、格式规范、新增内容应放在哪里
## 工具与脚本（若有）
## 其他（可选）
"""
```

把 `DEFAULT_AGENTS_MD` 的首行（当前 `:58-59` 的注释 + `"""# AGENTS.md`）改为：

```python
# AGENTS.md 默认内容(当目录为空或无法调用 LLM 时的兜底内容)。
# 措辞保持目录类型中立(代码/文档目录都适用),条目按"若适用"理解。
# 2026-10-17: 首行与生成模板的一级标题统一为「# 项目指南」。
DEFAULT_AGENTS_MD = """# 项目指南
```

（`DEFAULT_AGENTS_MD` 其余正文一字不动。）

> 注：spec 原文的模板里用三个反引号举例"不要用代码块包裹"；本计划改写成「markdown 代码块」，语义不变，避免提示词字面量与计划文档自身的 Markdown 围栏冲突。若执行时坚持保留反引号原字面，请给该代码块用四个反引号作围栏。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_agentsmd.py -q`
Expected: PASS（含既有 12 个 `test_generate_*` 与 5 个 `test_pick_init_template_*`，它们只引用常量名、不校验字面文本）

- [ ] **Step 5: lint + 提交**

```bash
ruff check .
git add tools/agentsmd/_core.py tests/test_agentsmd.py
git commit -m "docs(agentsmd): 按 Codex /init 风格重写生成模板"
```

---

### Task 2: `collect_git_history` 采集提交历史

**Files:**
- Modify: `tools/agentsmd/_core.py`（顶部 import 段 `:19-33`；新增函数放在 `_doc_excerpts_section` 之后、`generate_agents_md_via_llm` 之前）
- Test: `tests/test_agentsmd.py`（追加到 `scan` 用例段之后）

**Interfaces:**
- Consumes: `tools/_helpers.py` 的 `_NO_WINDOW_KWARGS`（Windows 无黑窗）、`safe_decode_bytes`
- Produces: `async def collect_git_history(dir_path: Path, *, git_bin: str = "git", limit: int = 20, timeout: float = 3.0) -> str` —— 返回 `"<短哈希> <主题>"` 逐行拼接的字符串，任何失败返回 `""`；每行主题截到 200 字符，总量超 4096 字符按行边界截断。

- [ ] **Step 1: 写失败的 git 采集测试**

在 `tests/test_agentsmd.py` 顶部 import 区补：

```python
import shutil
import subprocess

import pytest
```

并把它加进 `from tools.agentsmd import (...)` 的导入列表：

```python
    collect_git_history,
```

在 `scan` 段之后追加（`_init_repo` / `requires_git` 定义一次，后续 Task 复用）：

```python
# ── 2026-10-17: git 历史采集 ────────────────────────

_GIT = shutil.which("git")
requires_git = pytest.mark.skipif(_GIT is None, reason="git 未安装")


def _init_repo(path: Path, subjects: list[str]) -> None:
    """在 path 建一个真 git 仓库,按 subjects 顺序各提交一次。"""
    subprocess.run([_GIT, "init", "-q"], cwd=path, check=True)
    subprocess.run([_GIT, "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run([_GIT, "config", "user.name", "tester"], cwd=path, check=True)
    for i, subject in enumerate(subjects):
        (path / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        subprocess.run([_GIT, "add", "."], cwd=path, check=True)
        subprocess.run([_GIT, "commit", "-qm", subject], cwd=path, check=True)


@requires_git
def test_collect_git_history_returns_short_hash_and_subject(tmp_path: Path):
    """最新提交在前;每行形如 `<7 位短哈希> <主题>`。"""
    _init_repo(tmp_path, ["feat(a): first", "fix(b): second"])
    out = asyncio.run(collect_git_history(tmp_path))
    lines = out.splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("fix(b): second")
    assert lines[1].endswith("feat(a): first")
    assert all(len(line.split(" ", 1)[0]) == 7 for line in lines)


@requires_git
def test_collect_git_history_respects_limit(tmp_path: Path):
    _init_repo(tmp_path, ["c1", "c2", "c3"])
    out = asyncio.run(collect_git_history(tmp_path, limit=2))
    assert len(out.splitlines()) == 2


@requires_git
def test_collect_git_history_truncates_long_subject(tmp_path: Path):
    """单条主题截到 200 字符:短哈希 + 空格 + 200。"""
    _init_repo(tmp_path, ["x" * 300])
    out = asyncio.run(collect_git_history(tmp_path))
    assert len(out.splitlines()[0]) == 7 + 1 + 200


def test_collect_git_history_non_repo_returns_empty(tmp_path: Path):
    assert asyncio.run(collect_git_history(tmp_path)) == ""


def test_collect_git_history_missing_binary_returns_empty(tmp_path: Path):
    """git_bin 不存在时静默返回 "",不抛异常。"""
    assert asyncio.run(collect_git_history(tmp_path, git_bin="spcode-no-such-git")) == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agentsmd.py -k "collect_git_history" -v`
Expected: FAIL — `ImportError: cannot import name 'collect_git_history' from 'tools.agentsmd'`（先报 ImportError 说明符号未导出；本 Task Step 4 一并导出）

- [ ] **Step 3: 实现 `collect_git_history`**

在 `_core.py` 顶部 import 段（`from pathlib import Path` 之后）改为：

```python
import asyncio
import logging
import os
import re
from pathlib import Path

from .._code_detect import (  # noqa: F401  (re-exports)
    CODE_FILE_EXTENSIONS,
    _SKIP_DIRS,
    has_code_files,
)
from .._helpers import _NO_WINDOW_KWARGS, safe_decode_bytes
```

紧接 import 段之后、模板常量之前，加模块级 logger：

```python
logger = logging.getLogger(__name__)


# 默认的 AGENTS.md 生成 prompt 模板(代码项目目录)。
```

在 `_doc_excerpts_section` 之后、`generate_agents_md_via_llm` 之前插入：

```python
# ── git 历史采集(2026-10-17) ─────────────────────────
# 提交历史是"提交信息约定"的唯一事实依据;没有它,LLM 只能编造
# `feat(scope): ...` 之类看起来合理但仓库里并不存在的约定。
_GIT_HISTORY_MAX_SUBJECT = 200
_GIT_HISTORY_MAX_CHARS = 4096


async def collect_git_history(
    dir_path: Path,
    *,
    git_bin: str = "git",
    limit: int = 20,
    timeout: float = 3.0,
) -> str:
    """采集目录所属仓库的最近提交(短哈希 + 主题),失败返回空串。

    WHY 全静默: 生成 AGENTS.md 是主流程,git 只是"锦上添花"的事实来源。
    目录不是仓库、机器没装 git、仓库损坏、磁盘慢 —— 任一情况都不该让
    `/agentsmd init` 变成报错。

    ``--no-merges``: 合并提交(`Merge pull request #123 from ...`)对推断
    提交约定只有噪声。

    Note:
        采集范围是 ``git -C <dir> log`` 的语义,即该目录**所属仓库**。
        目录是子目录时拿到的是整仓历史 —— Git 的固有行为,不是缺陷。

    Args:
        dir_path: 目标目录(不必是仓库根)。
        git_bin: git 可执行文件路径(优先配置 ``git_path``)。
        limit: 最多取多少条提交。
        timeout: 子进程超时(秒),超时即 kill 并返回空串。

    Returns:
        逐行 ``"<短哈希> <主题>"``;无历史或失败时 ``""``。
    """
    cmd = [
        git_bin,
        "-C",
        str(dir_path),
        "log",
        "--no-merges",
        "--pretty=format:%h %s",
        "-n",
        str(limit),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
    except OSError as e:
        # FileNotFoundError(git 未安装/路径错) / WinError 2 / 权限问题
        logger.debug(f"[agentsmd] git 不可用,跳过历史采集: {e}")
        return ""

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        await proc.wait()
        logger.debug(f"[agentsmd] git log 超时({timeout}s),跳过历史采集")
        return ""

    if proc.returncode != 0:
        logger.debug(f"[agentsmd] git log 退出码 {proc.returncode},跳过历史采集")
        return ""

    text = safe_decode_bytes(stdout, preferred="utf-8")
    lines: list[str] = []
    total = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 截的是"主题":短哈希必须完整保留,否则每行就定位不到提交了
        sha, sep, subject = line.partition(" ")
        if sep and len(subject) > _GIT_HISTORY_MAX_SUBJECT:
            line = f"{sha} {subject[:_GIT_HISTORY_MAX_SUBJECT]}"
        # 按行边界截断,绝不切断半行
        if total + len(line) + 1 > _GIT_HISTORY_MAX_CHARS:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)
```

- [ ] **Step 4: 导出新符号**

`tools/agentsmd/__init__.py` 的 `from ._core import (...)` 列表按字母序插入 `collect_git_history,`（放在 `build_injection,` 之后），`__all__` 的 helpers 段同步加 `"collect_git_history",`。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_agentsmd.py -k "collect_git_history" -v`
Expected: 5 passed（无 git 环境下 `requires_git` 的 3 个 SKIP，其余 2 个 PASS）

- [ ] **Step 6: lint + 提交**

```bash
ruff check .
git add tools/agentsmd/_core.py tools/agentsmd/__init__.py tests/test_agentsmd.py
git commit -m "feat(agentsmd): 生成时采集 git 提交历史作为事实依据"
```

---

### Task 3: prompt 装配统一（上下文 + 输出约束）

**Files:**
- Modify: `tools/agentsmd/_core.py`（在 `collect_git_history` 之后插入 2 个函数；`generate_agents_md_via_llm` `:303` 起改签名与 prompt 组装）
- Test: `tests/test_agentsmd.py`

**Interfaces:**
- Consumes: `scan_project_context(dir_path)`、Task 2 的 `collect_git_history(...)`
- Produces:
  - `async def build_project_context(dir_path: Path, *, git_bin: str = "git", history_limit: int = 20) -> str`
  - `def build_output_constraints(max_chars: int) -> str`
  - `async def generate_agents_md_via_llm(provider, dir_path: Path, *, umo: str, init_template: str = "", max_chars: int = 0, git_bin: str = "git") -> str`

- [ ] **Step 1: 写失败的装配测试**

`tests/test_agentsmd.py` 的 `from tools.agentsmd import (...)` 导入列表补：

```python
    build_output_constraints,
    build_project_context,
```

追加：

```python
# ── 2026-10-17: prompt 装配 ────────────────────────


def test_build_output_constraints_includes_limit():
    text = build_output_constraints(5000)
    assert "5000" in text
    assert "不要添加任何额外说明" in text
    assert "代码块" in text  # 明确禁止用代码块包裹全文


def test_build_output_constraints_zero_omits_number():
    """max_chars=0 = 不限制:只保留"直接输出 / 不要包裹"两条。"""
    text = build_output_constraints(0)
    assert "上限" not in text
    assert "不要添加任何额外说明" in text


@requires_git
def test_build_project_context_appends_history(tmp_path: Path):
    _init_repo(tmp_path, ["chore: seed"])
    ctx = asyncio.run(build_project_context(tmp_path))
    assert "## 目录结构" in ctx
    assert "## 最近提交历史" in ctx
    assert "chore: seed" in ctx


def test_build_project_context_without_history_has_no_section(tmp_path: Path):
    """非仓库目录:上下文就是 scan 的输出,不追加空小节。"""
    ctx = asyncio.run(build_project_context(tmp_path))
    assert "## 最近提交历史" not in ctx
    assert "## 目录结构" in ctx


def _provider_returning(text: str):
    """构造一个 provider mock,text_chat 恒返回 text。"""
    provider = MagicMock()
    resp = MagicMock()
    resp.completion_text = text
    provider.text_chat = AsyncMock(return_value=resp)
    return provider


def test_generate_prompt_carries_max_chars(tmp_path: Path):
    provider = _provider_returning("# generated")
    asyncio.run(
        generate_agents_md_via_llm(
            provider, tmp_path, umo="u1", init_template="TPL", max_chars=4321
        )
    )
    prompt = provider.text_chat.call_args.kwargs["prompt"]
    assert "TPL" in prompt
    assert "4321" in prompt


def test_generate_prompt_without_max_chars_has_no_number(tmp_path: Path):
    """默认 max_chars=0:prompt 不出现数字上限(旧调用行为不变)。"""
    provider = _provider_returning("# generated")
    asyncio.run(generate_agents_md_via_llm(provider, tmp_path, umo="u1"))
    prompt = provider.text_chat.call_args.kwargs["prompt"]
    assert "硬性上限" not in prompt


@requires_git
def test_generate_prompt_includes_commit_history(tmp_path: Path):
    _init_repo(tmp_path, ["feat(git): add graph"])
    provider = _provider_returning("# generated")
    asyncio.run(generate_agents_md_via_llm(provider, tmp_path, umo="u1"))
    prompt = provider.text_chat.call_args.kwargs["prompt"]
    assert "## 最近提交历史" in prompt
    assert "feat(git): add graph" in prompt
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agentsmd.py -k "build_output_constraints or build_project_context or prompt_carries or prompt_without or prompt_includes" -v`
Expected: FAIL — `ImportError: cannot import name 'build_output_constraints'`

- [ ] **Step 3: 实现两个装配件 + 改 `generate_agents_md_via_llm`**

在 `collect_git_history` 之后插入：

```python
def build_output_constraints(max_chars: int) -> str:
    """构造 prompt 尾部的输出约束(init / update 共用,消除两份尾巴漂移)。

    ``max_chars <= 0`` 时只保留"直接输出 + 不要代码块包裹"两条,
    不出现任何数字 —— 这是"不限制"的显式语义。
    """
    parts = [
        "请直接输出 AGENTS.md 的完整内容(Markdown 格式),不要添加任何额外说明,"
        "也不要使用 markdown 代码块包裹整个内容。",
    ]
    if max_chars > 0:
        parts.append(f"全文硬性上限 {max_chars} 字,超出部分会被截断。")
    return "\n".join(parts)


async def build_project_context(
    dir_path: Path,
    *,
    git_bin: str = "git",
    history_limit: int = 20,
) -> str:
    """构造完整的生成上下文:目录扫描 + (可选)最近提交历史。

    WHY 单独一层包装: ``scan_project_context`` 是纯函数,既有 6 个 scan
    用例与第三方调用都依赖它的输出不变;历史采集是异步 / 有副作用(起子进程),
    只在包装层追加,不污染扫描函数。
    """
    context = scan_project_context(dir_path)
    history = await collect_git_history(
        dir_path, git_bin=git_bin, limit=history_limit
    )
    if not history:
        return context
    header = f"## 最近提交历史（最多 {history_limit} 条，用于推断提交信息约定）"
    return f"{context}\n\n{header}\n\n{history}"
```

把 `generate_agents_md_via_llm`（`:303` 起）整体改为：

```python
async def generate_agents_md_via_llm(
    provider,
    dir_path: Path,
    *,
    umo: str,
    init_template: str = "",
    max_chars: int = 0,
    git_bin: str = "git",
) -> str:
    """通过 AstrBot LLM Provider 自动生成 AGENTS.md 内容。

    Args:
        provider: 通过 self.context.get_using_provider(umo=...) 获取
        dir_path: 目标项目目录
        umo: unified_msg_origin(用于 session_id 隔离)
        init_template: 用户自定义模板;为空则用默认
        max_chars: 全文硬性上限(字符数);``<= 0`` 表示不追加数字约束。
            注意默认值是 0(不限制),由 handler 传入配置解析结果 ——
            这样旧调用方(含测试)不传该参数时行为完全不变。
        git_bin: git 可执行文件路径(配置 ``git_path`` > ``"git"``)

    Returns:
        生成的 AGENTS.md 文本;若 LLM 调用失败,返回 DEFAULT_AGENTS_MD
    """
    if provider is None:
        # WHY 排在 git 采集之前: provider 缺失时无需付子进程代价,
        # 也让既有用例可以安全传入不存在的目录。
        return DEFAULT_AGENTS_MD

    template = (
        init_template.strip()
        if init_template and init_template.strip()
        else DEFAULT_INIT_TEMPLATE
    )
    project_context = await build_project_context(dir_path, git_bin=git_bin)
    prompt = (
        f"{template}\n\n"
        f"以下是该目录的文件结构、文件类型统计和关键文件内容摘要:\n\n"
        f"{project_context}\n\n"
        f"{build_output_constraints(max_chars)}"
    )

    try:
        llm_resp = await provider.text_chat(
            prompt=prompt,
            session_id=umo,
            contexts=[],
            system_prompt="你是一名资深软件工程师,擅长为多种语言的项目编写规范文档。",
        )
    except Exception:
        return DEFAULT_AGENTS_MD

    content = (getattr(llm_resp, "completion_text", "") or "").strip()
    content = strip_code_fence(content)
    return content if content else DEFAULT_AGENTS_MD
```

- [ ] **Step 4: 导出新符号**

`__init__.py` 的导入列表与 `__all__` 加 `build_output_constraints` / `build_project_context`（字母序，`build_injection` 之前）。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_agentsmd.py -q`
Expected: PASS（既有 12 个 `test_generate_*` 不传新参数，行为不变）

- [ ] **Step 6: lint + 提交**

```bash
ruff check .
git add tools/agentsmd/_core.py tools/agentsmd/__init__.py tests/test_agentsmd.py
git commit -m "feat(agentsmd): 统一 prompt 装配(提交历史 + 输出约束)"
```

---

### Task 4: 长度护栏纯逻辑（解析 + 截断）

**Files:**
- Modify: `tools/agentsmd/_core.py`（在 `generate_agents_md_via_llm` 之后、`build_injection` 之前插入）
- Test: `tests/test_agentsmd.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `DEFAULT_MAX_CHARS: int = 10000`
  - `def resolve_max_chars(config: dict | None) -> int` —— 缺失 / `None` / 不可转 int → `DEFAULT_MAX_CHARS`；负数 → `0`
  - `def truncate_at_section(content: str, max_chars: int) -> str` —— 保证 `len(返回值) <= max_chars`（`max_chars <= 0` 时原样返回）

- [ ] **Step 1: 写失败的护栏测试**

`tests/test_agentsmd.py` 导入列表补：

```python
    DEFAULT_MAX_CHARS,
    resolve_max_chars,
    truncate_at_section,
```

追加：

```python
# ── 2026-10-17: 长度护栏(纯逻辑) ────────────────────


def test_resolve_max_chars_defaults():
    assert DEFAULT_MAX_CHARS == 10000
    assert resolve_max_chars(None) == 10000
    assert resolve_max_chars({}) == 10000
    assert resolve_max_chars({"agentsmd_max_chars": 1234}) == 1234
    assert resolve_max_chars({"agentsmd_max_chars": "2000"}) == 2000
    assert resolve_max_chars({"agentsmd_max_chars": None}) == 10000
    assert resolve_max_chars({"agentsmd_max_chars": "abc"}) == 10000


def test_resolve_max_chars_negative_means_unlimited():
    assert resolve_max_chars({"agentsmd_max_chars": -1}) == 0
    assert resolve_max_chars({"agentsmd_max_chars": 0}) == 0


def test_truncate_noop_within_limit_or_unlimited():
    assert truncate_at_section("abc", 10) == "abc"
    big = "A" * 500
    assert truncate_at_section(big, 0) == big


def test_truncate_prefers_section_boundary():
    """优先在小节边界(\n## )切开,标注里写明原文长度。"""
    content = "# 标题\n\n## 甲\n\n" + "A" * 40 + "\n## 乙\n\n" + "B" * 400
    out = truncate_at_section(content, 120)
    assert len(out) <= 120
    assert "## 甲" in out
    assert "## 乙" not in out
    assert "截断" in out
    assert str(len(content)) in out


def test_truncate_falls_back_to_paragraph_boundary():
    content = "A" * 40 + "\n\n" + "B" * 200
    out = truncate_at_section(content, 100)
    assert len(out) <= 100
    assert "B" not in out
    assert "截断" in out


def test_truncate_falls_back_to_line_boundary():
    # 填充字符用 "Z" 而非 "x": 标注里含 agentsmd_ma**x**_chars,
    # 用 "x" 会让 `"x" not in out` 命中标注本身(假失败)。
    content = "line1\nline2\n" + "Z" * 300
    out = truncate_at_section(content, 60)
    assert len(out) <= 60
    assert "Z" not in out
    assert "line1" in out


def test_truncate_hard_cut_when_no_boundary():
    """无任何换行时硬切;标注长度计入预算,结果精确等于上限。"""
    out = truncate_at_section("A" * 500, 60)
    assert len(out) == 60


def test_truncate_note_longer_than_limit_degrades_to_plain_cut():
    """配置值极小、标注本身放不下时退化为无标注硬切,不允许越界。"""
    out = truncate_at_section("A" * 500, 10)
    assert len(out) == 10
    assert "截断" not in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agentsmd.py -k "resolve_max_chars or truncate" -v`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_MAX_CHARS'`

- [ ] **Step 3: 实现**

在 `generate_agents_md_via_llm` 之后插入：

```python
# ── 长度护栏(2026-10-17) ───────────────────────────
# WHY: init / update 由 LLM 直接产出全文并原样写盘,一个跑偏的生成结果
# 会永久留在磁盘上,并在之后每次 LLM 请求中注入 system_prompt。
# 护栏只作用于自动生成;手工编辑的文件不受限制(用户显式意图优先)。
DEFAULT_MAX_CHARS: int = 10000


def resolve_max_chars(config: dict | None) -> int:
    """从插件 config 解析 ``agentsmd_max_chars``。

    返回值语义:
        - ``> 0``: 硬性字符上限
        - ``0``: 不限制(显式配置 0 或负数,均视为不限)
        - 缺失 / ``None`` / 无法转 int: 回退 :data:`DEFAULT_MAX_CHARS`

    WHY 负数归一成 0: 用户把开关"调过头"时按"不限"处理比按"极小值"
    处理更符合直觉,也不会把文件切成几行。
    """
    raw = (config or {}).get("agentsmd_max_chars", DEFAULT_MAX_CHARS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CHARS
    return 0 if value < 0 else value


def truncate_at_section(content: str, max_chars: int) -> str:
    """按 Markdown 边界截断,并追加一行截断标注。

    切点优先级: 小节(``\\n## ``) > 段落(``\\n\\n``) > 行(``\\n``) > 硬切。
    标注长度**计入预算**,保证返回值长度不超过 ``max_chars``;
    若 ``max_chars`` 小到放不下标注,则退化为无标注硬切。
    """
    if max_chars <= 0 or len(content) <= max_chars:
        return content

    original = len(content)
    note = f"\n\n> （已按 agentsmd_max_chars={max_chars} 截断，原文 {original} 字）"
    budget = max_chars - len(note)
    if budget <= 0:
        return content[:max_chars]

    head = content[:budget]
    cut = -1
    for sep in ("\n## ", "\n\n", "\n"):
        idx = head.rfind(sep)
        if idx > 0:
            cut = idx
            break
    body = (head[:cut] if cut > 0 else head).rstrip()
    return body + note
```

- [ ] **Step 4: 导出新符号**

`__init__.py` 导入列表与 `__all__` 加 `DEFAULT_MAX_CHARS`（常量段）、`resolve_max_chars` / `truncate_at_section`（helpers 段）。门面完整性用例 `test_package_exposes_all_expected_symbols` 的 `expected` 集合统一留到 Task 5 一起补齐（避免同一集合改三次）。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_agentsmd.py -k "resolve_max_chars or truncate" -v`
Expected: 8 passed

- [ ] **Step 6: lint + 提交**

```bash
ruff check .
git add tools/agentsmd/_core.py tools/agentsmd/__init__.py tests/test_agentsmd.py
git commit -m "feat(agentsmd): 新增长度上限解析与小节边界截断"
```

---

### Task 5: `fit_generated_content`（压缩一次 + 回退）

**Files:**
- Modify: `tools/agentsmd/_core.py`（紧接 `truncate_at_section` 之后）
- Test: `tests/test_agentsmd.py`

**Interfaces:**
- Consumes: `truncate_at_section`、`strip_code_fence`
- Produces: `async def fit_generated_content(provider, content: str, *, umo: str, max_chars: int) -> tuple[str, str]` —— 第二元素是 `"none" | "compressed" | "truncated"`

- [ ] **Step 1: 写失败的护栏 LLM 测试**

导入列表补：

```python
    fit_generated_content,
```

追加（`_provider_returning` 复用 Task 3 定义的 helper）：

```python
# ── 2026-10-17: fit_generated_content ──────────────


def test_fit_passthrough_within_limit_does_not_call_llm():
    provider = _provider_returning("should not be used")
    content = "# 项目指南\n\n" + "A" * 50
    out, action = asyncio.run(
        fit_generated_content(provider, content, umo="u", max_chars=10000)
    )
    assert (out, action) == (content, "none")
    provider.text_chat.assert_not_called()


def test_fit_zero_means_unlimited():
    provider = _provider_returning("should not be used")
    content = "A" * 50000
    out, action = asyncio.run(
        fit_generated_content(provider, content, umo="u", max_chars=0)
    )
    assert out == content
    assert action == "none"
    provider.text_chat.assert_not_called()


def test_fit_compresses_once_with_session_id():
    provider = _provider_returning("# 压缩版\n\n短")
    out, action = asyncio.run(
        fit_generated_content(provider, "A" * 200, umo="umo-x", max_chars=100)
    )
    assert (out, action) == ("# 压缩版\n\n短", "compressed")
    assert provider.text_chat.call_count == 1
    kwargs = provider.text_chat.call_args.kwargs
    assert kwargs["session_id"] == "umo-x"
    assert kwargs["contexts"] == []
    assert "100" in kwargs["prompt"]


def test_fit_falls_back_to_original_when_still_too_long():
    """压缩仍超限 → 截断**原文**,不采用压缩半成品。"""
    provider = _provider_returning("B" * 500)
    content = "A" * 300 + "\n## 二\n" + "A" * 300
    out, action = asyncio.run(
        fit_generated_content(provider, content, umo="u", max_chars=200)
    )
    assert action == "truncated"
    assert len(out) <= 200
    assert out.startswith("A")
    assert "B" not in out


def test_fit_truncates_original_when_provider_is_none():
    out, action = asyncio.run(
        fit_generated_content(None, "A" * 500, umo="u", max_chars=120)
    )
    assert action == "truncated"
    assert len(out) <= 120


def test_fit_truncates_original_when_provider_raises():
    provider = MagicMock()
    provider.text_chat = AsyncMock(side_effect=RuntimeError("boom"))
    out, action = asyncio.run(
        fit_generated_content(provider, "A" * 500, umo="u", max_chars=120)
    )
    assert action == "truncated"
    assert len(out) <= 120


def test_fit_truncates_original_when_compression_empty():
    provider = _provider_returning("")
    out, action = asyncio.run(
        fit_generated_content(provider, "A" * 500, umo="u", max_chars=120)
    )
    assert action == "truncated"
    assert len(out) <= 120


def test_fit_rejects_longer_compression():
    provider = _provider_returning("C" * 900)
    out, action = asyncio.run(
        fit_generated_content(provider, "A" * 500, umo="u", max_chars=120)
    )
    assert action == "truncated"
    assert "C" not in out


def test_fit_accepts_compression_exactly_at_limit():
    provider = _provider_returning("D" * 100)
    out, action = asyncio.run(
        fit_generated_content(provider, "A" * 500, umo="u", max_chars=100)
    )
    assert (out, action) == ("D" * 100, "compressed")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agentsmd.py -k "fit_" -v`
Expected: FAIL — `ImportError: cannot import name 'fit_generated_content'`

- [ ] **Step 3: 实现**

在 `truncate_at_section` 之后插入：

```python
# 压缩用的 system_prompt 与 init 保持一致(同一 persona,避免风格漂移)
_COMPRESS_SYSTEM_PROMPT = (
    "你是一名资深软件工程师,擅长为多种语言的项目编写规范文档。"
)


def _build_compress_prompt(max_chars: int, content: str) -> str:
    """构造压缩 prompt。

    WHY 不用 ``str.format``: 草稿正文里可能含 ``{}``(JSON 片段 / 代码块),
    ``.format`` 会把它们当占位符抛 KeyError。f-string 只解释字面量部分。
    """
    return (
        "下面是一份 AGENTS.md 草稿，长度超过上限。"
        "请在保留全部事实信息与章节结构的前提下压缩它。\n\n"
        "要求：\n"
        f"- 目标长度不超过 {max_chars} 字\n"
        "- 不新增原文没有的事实\n"
        "- 保留命令、路径、文件名、配置键名等可执行细节\n"
        "- 直接输出压缩后的 Markdown，不要任何额外说明，也不要用代码块包裹\n\n"
        f"草稿：\n{content}"
    )


async def fit_generated_content(
    provider,
    content: str,
    *,
    umo: str,
    max_chars: int,
) -> tuple[str, str]:
    """把自动生成的内容压到 ``max_chars`` 以内。

    判定顺序(短路):
        1. ``max_chars <= 0`` 或未超限 → ``(content, "none")``,**不调用 LLM**
        2. ``provider is None`` → 直接截断原文
        3. 压缩成功(非空 / 更短 / 落在上限内) → ``(new, "compressed")``
        4. 其余(异常 / 空 / 更长 / 仍超限) → 截断**原文**

    WHY 第 4 条用原文: 压缩失败的产物是不可信的半成品,拿它截断等于
    把"压缩时被删掉的事实"和"截断丢掉的小节"叠加两次损失。

    Returns:
        ``(最终内容, "none" | "compressed" | "truncated")``
    """
    if max_chars <= 0 or len(content) <= max_chars:
        return content, "none"
    if provider is None:
        return truncate_at_section(content, max_chars), "truncated"

    try:
        llm_resp = await provider.text_chat(
            prompt=_build_compress_prompt(max_chars, content),
            session_id=umo,
            contexts=[],
            system_prompt=_COMPRESS_SYSTEM_PROMPT,
        )
    except Exception as e:
        logger.warning(f"[agentsmd] 压缩重写失败,回退截断: {e}")
        return truncate_at_section(content, max_chars), "truncated"

    candidate = (getattr(llm_resp, "completion_text", "") or "").strip()
    candidate = strip_code_fence(candidate).strip()
    if 0 < len(candidate) < len(content) and len(candidate) <= max_chars:
        return candidate, "compressed"
    logger.debug(
        f"[agentsmd] 压缩结果不可用(长度 {len(candidate)} / 原文 {len(content)} "
        f"/ 上限 {max_chars}),回退截断"
    )
    return truncate_at_section(content, max_chars), "truncated"
```

- [ ] **Step 4: 导出并补门面完整性用例**

`__init__.py` 导入列表与 `__all__` 加 `fit_generated_content`；把 `test_package_exposes_all_expected_symbols` 的 `expected` 集合补齐本计划新增的 7 个符号：

```python
        "DEFAULT_MAX_CHARS",
        "build_output_constraints",
        "build_project_context",
        "collect_git_history",
        "fit_generated_content",
        "resolve_max_chars",
        "truncate_at_section",
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_agentsmd.py tests/test_agentsmd_subsystem.py -q`
Expected: PASS（含 `test_package_exposes_all_expected_symbols`）

- [ ] **Step 6: lint + 提交**

```bash
ruff check .
git add tools/agentsmd/_core.py tools/agentsmd/__init__.py tests/test_agentsmd.py
git commit -m "feat(agentsmd): 超限压缩一次,失败回退小节截断"
```

---

### Task 6: 配置项接入（schema / 默认配置 / conftest）

**Files:**
- Modify: `_conf_schema.json:289-307`（`agentsmd.items`）
- Modify: `main.py:108`（`_DEFAULT_CONFIG` 的 `agentsmd_enabled` 之后）
- Modify: `tests/conftest.py:245-252`（`plugin._config`）
- Test: `tests/test_agentsmd.py`

**Interfaces:**
- Consumes: `DEFAULT_MAX_CHARS`
- Produces: 配置键 `agentsmd_max_chars`（int，默认 10000），由 `resolve_max_chars(plugin._config)` 读取

- [ ] **Step 1: 写失败的配置测试**

`tests/test_agentsmd.py` 顶部补 `import json` 与 `import re`（若未导入），追加：

```python
# ── 2026-10-17: 配置项 ─────────────────────────────


def test_conf_schema_declares_agentsmd_max_chars():
    schema = json.loads(
        (ROOT / "_conf_schema.json").read_text(encoding="utf-8")
    )
    item = schema["agentsmd"]["items"]["agentsmd_max_chars"]
    assert item["type"] == "int"
    assert item["default"] == DEFAULT_MAX_CHARS
    assert "0 = 不限制" in item["hint"]


def test_main_default_config_declares_agentsmd_max_chars():
    """_DEFAULT_CONFIG 与 schema 默认值必须一致(两处事实源)。

    刻意不 import main.py: 该模块会拉起整个 AstrBot 框架(~7s)且有副作用;
    这条只需校验"字面量与 DEFAULT_MAX_CHARS 一致"。
    """
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    m = re.search(r'"agentsmd_max_chars":\s*(\d+)', src)
    assert m, "main.py::_DEFAULT_CONFIG 缺少 agentsmd_max_chars"
    assert int(m.group(1)) == DEFAULT_MAX_CHARS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agentsmd.py -k "agentsmd_max_chars" -v`
Expected: FAIL — `KeyError: 'agentsmd_max_chars'`

- [ ] **Step 3: 加 schema 项**

`_conf_schema.json` 的 `agentsmd.items` 中，在 `init_template` 之后插入（注意前一项补逗号）：

```json
      "agentsmd_max_chars": {
        "description": "AGENTS.md 自动生成长度上限（字符数）",
        "hint": "仅约束 /agentsmd init 与 update 的自动生成；手工编辑的文件不受限制。0 = 不限制。",
        "type": "int",
        "default": 10000
      }
```

- [ ] **Step 4: 加默认配置 + conftest 快照**

`main.py` 的 `_DEFAULT_CONFIG`，在 `"agentsmd_enabled": True,` 下一行插入：

```python
    # 2026-10-17: /agentsmd init|update 自动生成长度上限(字符);0 = 不限制
    "agentsmd_max_chars": 10000,
```

`tests/conftest.py` 的 `plugin._config` 字典，在 `"agentsmd_enabled": True,` 下一行插入：

```python
        # 2026-10-17: handler 读写此键;显式给出让 handler 用例可预测
        "agentsmd_max_chars": 10000,
```

> 为什么不在 `tools/_config.py::DEFAULT_CONFIG` 加同名键：该字典只服务 `code_check` 的 style 默认值，加进来会制造第二个事实源（与 spec 一致）。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_agentsmd.py -k "agentsmd_max_chars" -v`
Expected: 2 passed（若独立环境无 `astrbot` 包，第 2 个 SKIP）

- [ ] **Step 6: 全量回归 + 提交**

```bash
pytest tests/test_agentsmd.py tests/test_agentsmd_subsystem.py tests/test_agentsmd_injection.py -q
ruff check .
git add _conf_schema.json main.py tests/conftest.py tests/test_agentsmd.py
git commit -m "feat(agentsmd): 新增 agentsmd_max_chars 配置项"
```

---

### Task 7: handler 接入护栏与消息后缀

**Files:**
- Modify: `tools/agentsmd/_handlers.py:11-24`（import 段）、`:106-118`（`init` 生成后）、`:231-273`（`update` prompt 与写盘）
- Test: `tests/test_agentsmd_subsystem.py`

**Interfaces:**
- Consumes: `resolve_max_chars` / `fit_generated_content` / `build_project_context` / `build_output_constraints`（Task 3–5）、`plugin._git_binary()`（既有，返回 `str`）
- Produces: `init` / `update` 写盘内容 `len(content) <= max_chars`（`max_chars > 0` 时）；成功消息按 action 追加后缀

- [ ] **Step 1: 修既有测试 + 写失败的 handler 用例**

先给两个走真 provider 的既有 init 用例补 git 二进制桩（**必须**，否则 MagicMock 作 argv[0] 抛 `TypeError`）：

- `tests/test_agentsmd_subsystem.py::test_init_picks_template_by_dir_kind`（`:424` 起，`fake_plugin.context.get_using_provider = MagicMock(return_value=provider)` 之后）
- `tests/test_agentsmd_subsystem.py::test_init_custom_template_overrides_dir_kind`（`:462` 起，同一位置）

各加一行：

```python
        # 2026-10-17: handler 会把 git 二进制传给历史采集;MagicMock 会污染 argv
        fake_plugin._git_binary = lambda: "git"
```

在 `update` 用例段之后追加：

```python
# ── 2026-10-17: handler 级长度护栏 ──────────────────


def _resp(text: str) -> MagicMock:
    r = MagicMock()
    r.completion_text = text
    return r


def _make_guarded_handlers(responses: list[str], max_chars: int):
    """构造 handler + 可编排 provider mock;返回 (handlers, state manager, provider)。"""
    provider = MagicMock()
    provider.text_chat = AsyncMock(side_effect=[_resp(text) for text in responses])
    fake_plugin = MagicMock()
    fake_plugin._config = {
        "file_remove_blacklist": [],
        "agentsmd_max_chars": max_chars,
    }
    fake_plugin.context.get_using_provider = MagicMock(return_value=provider)
    fake_plugin._git_binary = lambda: "git"
    mgr = AgentsStateManager()
    h = AgentsmdHandlers(
        state=mgr,
        plugin_getter=lambda: fake_plugin,
        is_path_safe=lambda *args, **kwargs: (True, ""),
    )
    return h, mgr, provider


async def _collect_init(h, directory: Path) -> str:
    msgs = []
    async for msg in h.init(_make_event(), str(directory)):
        msgs.append(msg)
    return "".join(msgs)


async def test_init_truncates_oversized_generation(tmp_path: Path):
    """压缩一次仍超限 → 按小节截断 + 标注,写盘 ≤ 上限,消息写明截断。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("x = 1", encoding="utf-8")
    big = "# 项目指南\n\n## 一\n\n" + "A" * 600
    h, _mgr, provider = _make_guarded_handlers([big, big], max_chars=200)

    text = await _collect_init(h, proj)

    written = (proj / "AGENTS.md").read_text(encoding="utf-8")
    assert len(written) <= 200
    assert "已截断" in text
    assert "原文" in text
    assert provider.text_chat.call_count == 2  # 生成 + 压缩


async def test_init_compresses_oversized_generation(tmp_path: Path):
    proj = tmp_path / "proj2"
    proj.mkdir()
    (proj / "main.py").write_text("x = 1", encoding="utf-8")
    big = "# 项目指南\n\n" + "A" * 600
    small = "# 项目指南\n\n压缩后"
    h, _mgr, provider = _make_guarded_handlers([big, small], max_chars=200)

    text = await _collect_init(h, proj)

    assert (proj / "AGENTS.md").read_text(encoding="utf-8") == small
    assert "已压缩" in text
    assert provider.text_chat.call_count == 2


async def test_init_zero_max_chars_keeps_legacy_behavior(tmp_path: Path):
    """0 = 不限制:不压缩、不截断,原文原样落盘。"""
    proj = tmp_path / "proj3"
    proj.mkdir()
    (proj / "main.py").write_text("x = 1", encoding="utf-8")
    big = "# 项目指南\n\n" + "A" * 600
    h, _mgr, provider = _make_guarded_handlers([big], max_chars=0)

    text = await _collect_init(h, proj)

    assert (proj / "AGENTS.md").read_text(encoding="utf-8") == big
    assert provider.text_chat.call_count == 1
    assert "已截断" not in text and "已压缩" not in text


async def test_update_truncates_and_refreshes_state(tmp_path: Path):
    proj = tmp_path / "proj4"
    proj.mkdir()
    agents_md = proj / "AGENTS.md"
    agents_md.write_text("OLD", encoding="utf-8")
    big = "# 项目指南\n\n## 一\n\n" + "A" * 600
    h, mgr, _provider = _make_guarded_handlers([big, big], max_chars=200)
    mgr.set(
        "umo-1",
        AgentsState(
            path=str(agents_md),
            directory=str(proj),
            last_content="OLD",
            mtime=agents_md.stat().st_mtime,
        ),
    )

    msgs = []
    async for msg in h.update(_make_event("umo-1")):
        msgs.append(msg)

    written = agents_md.read_text(encoding="utf-8")
    assert len(written) <= 200
    assert mgr.get("umo-1").last_content == written
    assert "已截断" in "".join(msgs)


async def test_init_message_unchanged_when_within_limit(tmp_path: Path):
    """未超限不给成功消息加后缀(保持既有文案)。"""
    proj = tmp_path / "proj5"
    proj.mkdir()
    (proj / "main.py").write_text("x = 1", encoding="utf-8")
    small = "# 项目指南\n\n短"
    h, _mgr, provider = _make_guarded_handlers([small], max_chars=10000)

    text = await _collect_init(h, proj)

    assert "创建 AGENTS.md" in text
    assert "已截断" not in text and "已压缩" not in text
    assert provider.text_chat.call_count == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_agentsmd_subsystem.py -k "truncates_oversized or compresses_oversized or zero_max_chars or update_truncates or unchanged_when_within_limit" -v`
Expected: FAIL —`test_init_truncates_oversized_generation` 断言 `len(written) <= 200` 失败（当前写出 600+ 字且消息无"已截断"）

- [ ] **Step 3: 改 handler import**

`tools/agentsmd/_handlers.py` 的 `from ._core import (...)` 改为：

```python
from ._core import (
    INJECTION_MARKER,
    build_injection,
    build_output_constraints,
    build_project_context,
    fit_generated_content,
    generate_agents_md_via_llm,
    pick_init_template,
    resolve_max_chars,
    strip_code_fence,
    strip_surrounding_quotes,
)
```

（`scan_project_context` 从该 import 移除 —— update 改用 `build_project_context`；留着会被 ruff 判未使用。）

在 `logger = logging.getLogger(__name__)` 之后加助手：

```python
def _fit_suffix(action: str, final_len: int, original_len: int) -> str:
    """按护栏动作生成成功消息后缀;``"none"`` 返回空串(文案不变)。"""
    if action == "compressed":
        return f"（已压缩至 {final_len} 字）"
    if action == "truncated":
        return f"（已截断至 {final_len} 字，原文 {original_len} 字）"
    return ""
```

- [ ] **Step 4: 改 `init`**

把 `:106-118` 的生成与成功消息改为：

```python
        umo = event.unified_msg_origin
        provider = plugin.context.get_using_provider(umo=umo)
        init_template = pick_init_template(plugin._config, target)
        max_chars = resolve_max_chars(plugin._config)

        content = await generate_agents_md_via_llm(
            provider,
            target,
            umo=umo,
            init_template=init_template,
            max_chars=max_chars,
            git_bin=plugin._git_binary(),
        )
        content = strip_code_fence(content).strip()
        original_len = len(content)
        # 2026-10-17: 自动生成的长度护栏(null provider / 未超限时是纯 no-op)
        content, fit_action = await fit_generated_content(
            provider, content, umo=umo, max_chars=max_chars
        )
        suffix = _fit_suffix(fit_action, len(content), original_len)

        try:
            await asyncio.to_thread(
                agents_md_path.write_text, content, encoding="utf-8"
            )
            yield event.plain_result(
                f"✅ 已在 `{directory}` 下创建 AGENTS.md"
                f"({len(content)} 字符)。{suffix}\n"
                f"路径: {agents_md_path}\n"
                f"您可以使用 `/agentsmd load {directory}` "
                "将其加载到当前会话的系统提示词中。"
            )
        except Exception as e:
            yield event.plain_result(f"❌ 写入 AGENTS.md 失败: {e}")
```

- [ ] **Step 5: 改 `update`**

把 `:231-249` 的 prompt 组装改为（`max_chars` 提前解析，上下文换 `build_project_context`，尾巴换 `build_output_constraints`）：

```python
        max_chars = resolve_max_chars(plugin._config)
        project_context = await build_project_context(
            dir_path, git_bin=plugin._git_binary()
        )
        prompt = (
            f"{init_template}\n\n"
            "以下是该项目的文件结构和关键文件内容摘要:\n\n"
            f"{project_context}\n\n"
            "## 现有 AGENTS.md 内容\n\n"
            f"{existing_content}\n\n"
            "请比较现有实现和目录结构与 AGENTS.md 是否有差异,并更新 AGENTS.md\n\n"
            f"{build_output_constraints(max_chars)}"
        )
```

把 `:255-273` 的落盘段改为：

```python
        new_content = (getattr(llm_resp, "completion_text", "") or "").strip()
        new_content = strip_code_fence(new_content)
        new_content = new_content if new_content else existing_content
        original_len = len(new_content)
        # 2026-10-17: 与 init 同一护栏;写盘前压到上限内
        new_content, fit_action = await fit_generated_content(
            provider, new_content, umo=umo, max_chars=max_chars
        )
        suffix = _fit_suffix(fit_action, len(new_content), original_len)

        try:
            await asyncio.to_thread(
                agents_md_path.write_text, new_content, encoding="utf-8"
            )
        except Exception as e:
            yield event.plain_result(f"❌ 写入 AGENTS.md 失败: {e}")
            return

        state.update_content(new_content, agents_md_path.stat().st_mtime)

        yield event.plain_result(
            f"AGENTS.md 已更新 ({len(new_content)} 字符)。{suffix}\n"
            "后续 LLM 请求将自动使用新版本。"
        )
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_agentsmd_subsystem.py -q`
Expected: PASS（既有 40+ 用例 + 5 个新护栏用例；`test_update_no_provider_yields_error` 仍在 provider 检查处早返回，不触发 git）

- [ ] **Step 7: lint + 提交**

```bash
ruff check .
git add tools/agentsmd/_handlers.py tests/test_agentsmd_subsystem.py
git commit -m "feat(agentsmd): init/update 接入长度护栏,消息标注压缩/截断"
```

---

## 收尾验证

全部 7 个 Task 完成后，在仓库根跑：

```bash
ruff check .
pytest tests/test_agentsmd.py tests/test_agentsmd_subsystem.py tests/test_agentsmd_injection.py -q
```

Expected: ruff 无 error；pytest 全绿（新增用例数：模板 4 + git 5 + prompt 4 + 护栏 8 + fit 9 + 配置 2 + handler 5 = 37；基线 108 → 约 145）。

手工冒烟（可选，需真实 LLM Provider）：

1. `/agentsmd init <一个真 git 仓库目录>` → 生成内容含"## 提交与 PR 规范"，且能看出它引用了真实提交前缀。
2. 把 `agentsmd_max_chars` 调到 `500` 后对同一目录 init → 消息出现"（已压缩至 N 字）"或"（已截断至 N 字，原文 M 字）"，文件长度 ≤ 500。
3. 把 `agentsmd_max_chars` 设为 `0` → 行为回到改造前（无后缀、不截断）。
4. 手工编辑 AGENTS.md 塞进 30000 字 → `/agentsmd load` + 发一条消息 → 注入内容**完整**（注入侧未截断，符合显式取舍）。

## 明确不做的（避免执行时"顺手"扩范围）

- 注入侧截断 / token 预算裁剪
- 层级发现、`AGENTS.override.md`、`project_doc_fallback_filenames`
- 生成后的命令事实校验（核对 `package.json` scripts 之类）
- `git log` 的配置开关
- 触碰 `codegraph` / `project` / webapi 任何代码
- 版本号 bump 与 README 更新
