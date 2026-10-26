# Changelog

spcode 工具箱的版本变更记录。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。本文件由 AstrBot Dashboard 插件页的
「更新日志」按钮直接读取并渲染。

## [v2.31.3] - 2026-10-17

AGENTS.md 自动生成质量改造：提示词按 Codex `/init` 风格重写、生成时采集真实提交历史、
并给自动生成加了长度护栏。

### 新增

- **生成时采集 git 提交历史**：`/agentsmd init` 与 `/agentsmd update` 在生成前读取该目录
  所属仓库最近 20 条提交（`git log --no-merges --pretty=format:%h %s`），作为「提交与 PR 规范」
  章节的事实依据，避免 LLM 凭空编造 `type(scope):` 之类的约定。
  - 单条主题截断到 200 字、总量 4096 字封顶、子进程 3 秒超时；
  - 任何失败（目录不是仓库 / 未安装 git / 超时 / 非零退出）**静默跳过**，不影响生成；
  - 采集范围遵循 `git -C <dir>` 语义：目录是子目录时得到的是整仓历史，这是 Git 的固有行为。
- **配置项 `agentsmd_max_chars`**（`int`，默认 `10000`）：`/agentsmd init` 与 `/agentsmd update`
  自动生成内容的长度上限（字符数）。`0`（或负数）= 不限制。

### 变更

- **模型提示词按 Codex `/init` 风格重写**（两套模板都改）：
  - 代码目录模板给出可增删的章节清单：项目结构与模块组织 / 构建、测试与本地开发 /
    代码风格与命名约定 / 测试规范 / 提交与 PR 规范 / 其他；
  - 文档目录模板同样收敛为：目录用途 / 内容组织 / 维护约定 / 工具与脚本 /
    其他；
  - 新增「只写仓库中实际看到的事实，不要编造不存在的命令、脚本或目录」「避免『保持代码整洁』
    这类空泛表述」等约束，并给出篇幅区间（代码目录 1200–2500 字，文档目录 800–2000 字）。
- **prompt 装配收口**：上下文统一由 `build_project_context()` 组装（目录扫描 + 可选提交历史），
  输出约束统一由 `build_output_constraints()` 生成，消除 `init` / `update` 各写一份尾巴导致的
  措辞漂移。
- **兜底内容一级标题**：`DEFAULT_AGENTS_MD` 首行由 `# AGENTS.md` 改为 `# 项目指南`，与生成
  模板的一级标题一致。

### 修复

- **超限自动生成不再无限增长**：超过 `agentsmd_max_chars` 时先请 LLM 压缩重写一次；若压缩失败、
  返回空、返回更长或仍超限，则回退**原始草稿**并按小节边界截断（`##` → 段落 → 行 → 硬切），
  末尾追加标注 `> （已按 agentsmd_max_chars=N 截断，原文 M 字）`。成功消息按结果追加
  「（已压缩至 N 字）」或「（已截断至 N 字，原文 M 字）」。
- **`/agentsmd update` 不再误伤手工内容**：LLM 空产出（内容过滤、静默超时）时原样保留现有
  AGENTS.md，不再把它送进长度护栏——此前会把用户手工维护的文件静默截断，且写盘无备份。

### 边界与可逆性

- 长度护栏**只作用于自动生成**。手工编辑的 AGENTS.md 不受限制，`on_llm_request` 注入侧
  零改动（长文件仍按用户意图完整注入）。
- `agentsmd_max_chars = 0` 完全回到改造前行为：不检测、不压缩、不截断、不追加消息后缀。
- 自定义 `init_template` 仍然全局覆盖模板选择，但同样受 `agentsmd_max_chars` 约束
  （约束文本独立追加，不依赖模板内容）。

## [v2.31.2] - 2026-10-07

### 变更

- 注入到 system_prompt 的指引文本（项目路径 / 激活的 worktree / vivado）统一为英文。

### 修复

- codegraph 安装目录查找优先使用配置目录，避免误用 standalone 版本。
- `git-file-export` 的临时导出文件改放到 `plugin_data` 下，遵循插件数据目录约定。
- Web API 读取 git blob 时保留原始字节，并支持以 index 为基线（未提交的改动也能取到内容）。

## 更早版本

v2.31.0 及更早的版本说明见 `metadata.yaml` 的「版本历程」段（Dashboard 插件页的插件描述里
同样可见），或 `git log`。
