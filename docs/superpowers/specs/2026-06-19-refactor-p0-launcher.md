# Refactor P0 Implementation — Agent Launcher

> **For agentic workers:** 本文件是给"将要开始实施 P0 的新 agent"的开机 prompt。
> 不是 spec 的一部分(不要混入 spec 评审);spec 在同目录的
> `2026-06-19-refactor-pr-a-extension-design.md`。
>
> **使用方式**:用户/调度者把本文档内容**原样**贴给一个新 agent session(无历史记忆的那种),
> 该 agent 即可按本文件自启动 P0 工作。读完后**回放 P0 流程即可**,不需要再问"接下来干什么"。

**状态**: 🚀 待 P0 实施使用
**作者**: elecvoid243 + 主代理
**日期**: 2026-06-19
**目标分支**: `refactor/pr-a-tool-registration`
**目标阶段**: P0(详见 spec §6)

---

## 1. 你的处境

你是一个全新的 agent,**没有**之前的记忆。任务是把 `refactor/pr-a-tool-registration` 分支从"基线落后"补到"与 main HEAD 功能对齐",并为后续 P1/P2 大块抽取扫清障碍。

## 2. 路径速查

- **项目根**:`F:\github\astrbot_plugin_spcode_toolkit`
- **工作目录(worktree)**:`F:\github\astrbot_plugin_spcode_toolkit\.worktrees\refactor-pr-a-tool-registration`
- **当前分支**:`refactor/pr-a-tool-registration`,tip = `6c2bc1d`
- **主分支(参考源)**:`main`,tip = `7fd7e91`
- **运行时 OS**:Windows + cmd.exe(别用 `cat`/`ls`/`grep`,用 `type`/`dir`/`findstr`)

## 3. 必读文档(按顺序,不要跳)

| 序 | 文档 | 目的 |
|---|------|------|
| 1 | 项目根的 `AGENTS.md` | 项目规范(目录结构、命名、commit 风格、工具优先级) |
| 2 | `docs/superpowers/specs/2026-06-19-refactor-pr-a-extension-design.md` | **本次任务的唯一 spec**,4 阶段全部写在里面 |
| 3 | `docs/superpowers/specs/2026-06-18-file_remove-trash-injection-design.md` | P0-1 `_file_remove_inject_guidance` 的设计依据 |
| 4 | `docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md` | P0-1 `handle_get_git_worktrees` + git 辅助函数的设计依据 |

读完 2 后,你已经知道 **每个阶段做什么、按什么顺序、什么 commit message**;3 和 4 是补充"为什么这么设计"。

## 4. 必看代码(在 main,不在 worktree)

| 位置 | 关键符号 / 段 | 用途 |
|------|---------------|------|
| `git show main:main.py` 段 1820-1893 | `_plan_mode_active` / `_plan_mode_active_count` / `handle_get_plan_mode` | P0-1 要搬过来 |
| `git show main:main.py` 段 1894-2041 | `handle_get_git_worktrees` + 用到的常量 | P0-1 要搬过来 |
| `git show main:main.py` 段 2940-2961 | `_file_remove_inject_guidance` | P0-1 要搬过来 |
| `git show main:main.py` 段 96-130 | `_make_git_worktrees_empty_envelope` | P0-1 常量 |
| `git show main:main.py` 段 215-225 | `_FILE_REMOVE_GUIDANCE` / `_FILE_REMOVE_GUIDANCE_MARKER` | P0-1 常量 |
| `git show main:main.py` 段 1453-1500 | `_project_load_step` + `_ProjectLoadAbort` | **P0-2 重点:回归修复** |
| `git show main:tools/_helpers.py` | 整个文件(148 行新增) | P0-1 git 辅助函数 |
| `git show main:main.py` 段 1089-1100 | `SPCodeToolkit` 类头 | 理解 `__init__` 哪些字段在用 |

## 5. 工作流(严格按 spec §12.2 的命令序列)

```bash
cd F:\github\astrbot_plugin_spcode_toolkit\.worktrees\refactor-pr-a-tool-registration
# P0-1:搬 web API / plan helpers / file_remove hook
# P0-2:恢复 _project_load_step + _ProjectLoadAbort
# P0-3:git checkout main -- tests/test_*.py 拉 5 个 test 文件
# P0-4:同步 _conf_schema.json
# 每步: pytest 对应测试 -v → 绿 → commit
```

**绝对不要**:

- 全量 `git checkout main -- main.py`(会覆盖 refactor 已抽出的 13 个工具类;**必须**逐段复制)
- 直接 `git rebase main`(会引起大量冲突,spec §11 明确禁用)
- 跳到 P1(P0 没做完,P1 没法做)
- 用 `rm` / `del` / `os.unlink` 删文件(用 `astrbot_file_remove`,优先级见 AGENTS.md)

**绝对要**:

- 每完成 P0 一个子任务,**先跑测试,后 commit**
- 4 个子任务 = 4 个 commit
- `docs/` 目录被 .gitignore,加文件用 `git add -f`
- 完成后用 `wc -l main.py` 报行数

## 6. 关键决策点(动手前想清楚)

- **从 main 复制代码**:用 `git show main:main.py | findstr /n "..."` 定位行号,再 `git show main:main.py > /tmp/extract.py` 整文件取出后用 Python 切片精确复制段。**不要 sed,不要整文件覆盖**。
- **`_project_load_step` 的检测逻辑**:它查 `msg.chain[0].text.startswith("❌")`,refactor 的压扁版可能简化了,**逐行**对齐 main 的实现。
- **常量归属**:`_FILE_REMOVE_GUIDANCE` 等常量从 main.py 搬到 `tools/_config.py`,跟 `_PROJECT_CODEGRAPH_GUIDANCE` 同处。

## 7. 何时停下问用户

遇到这些情况,停下来报告,**不要自作主张**:

- 5 个 P0 缺失 test 在 main 上跑也失败(说明不是 refactor 问题,需要 user 决定)
- `@filter.on_llm_request()` 不支持类实例方法(影响后续 P1-3,提前验证)
- 复制 main 代码段时,refactor 已有的同名函数冲突
- 任何 pytest 出现 `ImportError` 或 `ModuleNotFoundError`(环境问题,不是代码问题)

## 8. 交付物(P0 完成后报回)

- [ ] 4 个 commit 的 hash 列表 + 各自的 message
- [ ] `pytest tests/test_plan_mode.py tests/test_file_remove_injection.py tests/test_git_worktrees.py tests/test_git_diff_worktree.py tests/test_helpers_git.py -v` 全部 PASS
- [ ] `pytest tests/test_project_cmd.py -k "abort" -v` 全部 PASS
- [ ] `ruff check .` 无 error
- [ ] `wc -l main.py`(预期 ~1700,跟 P0 前持平)
- [ ] `git log --oneline -6`

P0 全部绿后,用户会给你下达 P1 任务(同样读 spec §7)。**不要主动开始 P1**。

---

## 9. 一句话总结

> 从 main 精确复制 5 段代码 + 1 个 helper 文件 + 5 个 test 文件 + 1 个 config 到 refactor worktree,
> 恢复 `_project_load_step`,按 P0-1/2/3/4 顺序做 4 个 commit,每步测试全绿。

---

**End of launcher.** 祝你顺利。
