# git-log 图形参数 + 侧边栏分支树 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AstrBot dashboard 的历史视图能画出分支树：插件 `git-log` 增加跨 ref / 拓扑排序两个参数，前端在 commit 行内左侧渲染 lane gutter，作用域入口寄生在现有「分支」选择器。

**Architecture:** 后端只加两个白名单布尔 query 参数（`--all` / `--topo-order`）并入 ETag fingerprint，不新增端点；前端拆成"纯布局函数 → 渲染组件 → 列表接线 → 入口与参数透传"四层，布局是纯函数、可独立测试。

**Tech Stack:** Python 3.10 / AstrBot Web API / pytest；Vue 3 + TypeScript + Vuetify 3 + vitest（`@vue/test-utils`）。

**Spec:** `docs/superpowers/specs/2026-10-05-git-log-graph-and-branch-tree-design.md`

## Global Constraints

- 两个仓库：插件 `G:/github/astrbot_plugin_spcode_toolkit`（后端，Task P1）；前端 `G:/github/AstrBot_for_spzx/dashboard`（Tasks M1–M4）。
- 插件测试命令（仓库根）：`"G:/github/AstrBot_for_spzx/venv/python.exe" -m pytest tests/test_git_log.py -q`
- 前端测试命令：`cd G:/github/AstrBot_for_spzx/dashboard && npx vitest run <spec 路径>`
- 插件 lint：`ruff check .`（仓库根）；前端类型检查：`npm run typecheck`（dashboard 根）
- 插件版本保持 **2.29.3**，本计划不 bump、不改 `_conf_schema.json`、不改 `metadata.yaml`。
- Reason code 复用既有 `invalid_param`，不新增码（`tools/webapi/_helpers.py:320`）。
- 视觉常量（唯一真源在 `gitGraphLayout.ts`）：`LANE_W = 13`、`GUTTER_PAD = 4`、`NODE_CY = 17`、`BAND = 34`；`gutterWidth(n) = n > 1 ? GUTTER_PAD * 2 + n * LANE_W : 0`。
- lane 调色板 6 色，CSS 变量名固定 `--spcode-graph-l0` … `--spcode-graph-l5`，含浅色主题覆盖块。
- i18n 新键必须同时落到 `zh-CN` / `en-US` / `ru-RU`：`spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.allRefs`。
- 每个任务结束提交一次；提交信息用 `feat(graph): …` / `test(graph): …` 风格。

## Review Focus

1. `all=true` + 1MB 截断：窗口内大量父提交指向窗口外 → 整屏淡化短线。必须在 M1 由 `dangling` 用例钉住行为，M3 用 `truncated` 横幅并存表达。
2. `?all=ture` 之类拼写错误：静默当 false 会让用户以为功能坏了 → P1 必须返回 `invalid_param`。
3. 单分支仓库（lanes = 1）：gutter 必须**零占用**，否则窄侧边栏白掉 13px → M1 `gutterWidth` + M3 线性回归用例。
4. 选择模式（squash / changelog）与 gutter 并存：复选框与 lane 节点重叠会导致点不中 → M3 断言 `--gw` 参与 `left` / `padding-left`。
5. 三语 i18n 漏一个 → M4 的 completeness 用例必须覆盖 zh / en / ru。

---

### Task 1 (P1): git-log 支持 `all` / `topo`

**Files:**
- Modify: `tools/webapi/git_log.py`（query 解析在 `handle` 内 `_qget` 段之后；`query_fingerprint` 组装处；`log_args` 组装处）
- Test: `tests/test_git_log.py`（追加到文件末尾）

**Interfaces:**
- Consumes: `ReasonCode.INVALID_PARAM`、`_make_envelope`、`_run_git_async`、测试夹具 `_init_git_repo` / `_load_project` / `_call_with_query` / `make_web_request_mock`（均已在 `tests/test_git_log.py` 内）。
- Produces: query 参数 `all` / `topo`；模块级常量 `_BOOL_TRUE = ("1", "true")`、`_BOOL_FALSE = ("", "0", "false")`；模块级函数 `_parse_tristate_bool(raw: str | None) -> bool | None`（`None` 表示非法值）。

- [ ] **Step 1: 写 4 个失败测试**

追加到 `tests/test_git_log.py` 末尾（沿用文件内既有的真实 git 仓库手法）：

```python
# ──────────────────────────────────────────────────────────
# 2026-10-05 (elecvoid243): all / topo —— 分支树（lane gutter）所需
# ──────────────────────────────────────────────────────────


async def test_log_all_includes_unmerged_branch_commit(
    monkeypatch, plugin, tmp_path: Path
):
    """未合并分支的提交只在 all=true 时出现；all=0 / 缺省时不可见。"""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=tmp_path, check=True)
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "feature only"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_path, check=True)
    _load_project(plugin, "u:m", str(tmp_path))

    default = await _call_with_query(monkeypatch, plugin)
    assert "feature only" not in [c["subject"] for c in default["data"]["commits"]]

    off = await _call_with_query(monkeypatch, plugin, all="0")
    assert "feature only" not in [c["subject"] for c in off["data"]["commits"]]

    on = await _call_with_query(monkeypatch, plugin, all="true")
    assert "feature only" in [c["subject"] for c in on["data"]["commits"]]


async def test_log_all_invalid_value_invalid_param(monkeypatch, plugin, tmp_path: Path):
    """拼写错误不能被静默当 false —— 否则用户以为开关坏了。"""
    _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    for bad in ("ture", "yes", "2"):
        result = await _call_with_query(monkeypatch, plugin, all=bad)
        assert result["data"]["success"] is False, bad
        assert result["data"]["reason"] == "invalid_param", bad

    result = await _call_with_query(monkeypatch, plugin, topo="ture")
    assert result["data"]["reason"] == "invalid_param"


async def test_log_topo_order_places_child_before_parent(
    monkeypatch, plugin, tmp_path: Path
):
    """topo=true 时任一 commit 必须排在它的父提交之前。

    构造时钟偏移：子提交 C 的 committer date 早于其父 P。git 默认顺序
    （commit-date）会把 P 排在 C 之前，--topo-order 保证 C 在前。
    """
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-05T00:00:00+00:00",
           "GIT_COMMITTER_DATE": "2026-01-05T00:00:00+00:00"}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "P"], cwd=tmp_path, env=env, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=tmp_path, check=True)
    old = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-02T00:00:00+00:00",
           "GIT_COMMITTER_DATE": "2026-01-02T00:00:00+00:00"}
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "C", "-q"], cwd=tmp_path, env=old, check=True)
    subprocess.run(["git", "checkout", "-q", "-"], cwd=tmp_path, check=True)

    _load_project(plugin, "u:m", str(tmp_path))
    result = await _call_with_query(monkeypatch, plugin, topo="true")
    commits = result["data"]["commits"]
    order = {c["sha"]: i for i, c in enumerate(commits)}
    for c in commits:
        for parent in c["parents"]:
            if parent in order:
                assert order[c["sha"]] < order[parent], (
                    f"child {c['sha']} must precede parent {parent}"
                )


async def test_log_etag_changes_when_all_flag_changes(
    monkeypatch, plugin, tmp_path: Path
):
    """all/topo 必须进 ETag fingerprint，否则两种模式互相 304 回放快照。"""
    from tools.webapi import git_log as _m
    from astrbot.api import web

    _init_git_repo(tmp_path, n_commits=2)
    _load_project(plugin, "u:m", str(tmp_path))

    _m._LOG_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_LOG_ETAG_TTL", 0.0)

    monkeypatch.setattr(web, "request", make_web_request_mock(query={}))
    r1 = await _gl.handle(plugin)
    etag_default = r1.headers.get("etag")
    assert etag_default, f"first response missing ETag: {dict(r1.headers)}"

    monkeypatch.setattr(web, "request", make_web_request_mock(query={"all": "true"}))
    r2 = await _gl.handle(plugin)
    etag_all = r2.headers.get("etag")
    assert etag_all and etag_all != etag_default
```

- [ ] **Step 2: 跑测试确认失败**

Run: `"G:/github/AstrBot_for_spzx/venv/python.exe" -m pytest tests/test_git_log.py -q -k "all_includes or all_invalid or topo_order or all_flag"`
Expected: 4 failed（`all=true` 未生效、`ture` 未报 `invalid_param`、ETag 相同）。

- [ ] **Step 3: 实现**

在 `tools/webapi/git_log.py` 顶部常量区加：

```python
# 2026-10-05: all / topo 是白名单布尔 —— 只接受固定字面量，永不拼接用户字符串。
_BOOL_TRUE = ("1", "true")
_BOOL_FALSE = ("", "0", "false")


def _parse_tristate_bool(raw: str | None) -> bool | None:
    """True / False / None（None = 非法值，调用方返回 invalid_param）。"""
    value = (raw or "").strip().lower()
    if value in _BOOL_TRUE:
        return True
    if value in _BOOL_FALSE:
        return False
    return None
```

在 `handle` 的 ref 解析之后（`ref = _qget("ref") or "HEAD"` 一带）解析并校验：

```python
    all_refs_raw = _qget("all")
    all_refs = _parse_tristate_bool(all_refs_raw)
    topo = _parse_tristate_bool(_qget("topo"))
    if all_refs is None or topo is None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            loaded=False,
            umo=umo,
            worktree=worktree,
        )
```

`log_args` 组装后立刻追加旗标（旗标先于 grep/author/ref，顺序固定便于阅读）：

```python
    if topo:
        log_args.append("--topo-order")
    if all_refs:
        log_args.append("--all")
```

`query_fingerprint` 追加两个维度：

```python
    query_fingerprint = (
        f"{ref or 'HEAD'}|{n}|{path or ''}|{author or ''}|{since or ''}"
        f"|{until or ''}|{grep or ''}|{int(all_refs)}|{int(topo)}"
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `"G:/github/AstrBot_for_spzx/venv/python.exe" -m pytest tests/test_git_log.py -q`
Expected: 全绿（含既有 40+ 用例无回归）。

- [ ] **Step 5: lint + 提交**

```bash
ruff check .
git add tools/webapi/git_log.py tests/test_git_log.py
git commit -m "feat(log): support all/topo flags for branch-tree lanes"
```

---

### Task 2 (M1): 纯布局函数 `gitGraphLayout.ts`

**Files:**
- Create: `dashboard/src/composables/gitGraphLayout.ts`
- Test: `dashboard/src/composables/gitGraphLayout.spec.ts`

**Interfaces:**
- Consumes: 无（纯函数，零依赖）。
- Produces（M2 / M3 依赖这些确切名字）：
  ```ts
  export const LANE_W: number;        // 13
  export const GUTTER_PAD: number;    // 4
  export const NODE_CY: number;       // 17
  export const BAND: number;          // 34
  export function gutterWidth(lanes: number): number;
  export interface GraphCommit { sha: string; parents: string[] }
  export interface GraphRow {
    lane: number; ins: number[]; passIn: number[]; outs: number[];
    cont: boolean; dangling: boolean;
  }
  export interface GraphLayout { rows: GraphRow[]; lanes: number }
  export function layoutGraph(commits: GraphCommit[]): GraphLayout;
  ```

- [ ] **Step 1: 写 6 个失败用例（1 个 `gutterWidth` + 5 个 `layoutGraph`）**

创建 `dashboard/src/composables/gitGraphLayout.spec.ts`：

```ts
import { describe, expect, it } from "vitest";
import { gutterWidth, layoutGraph } from "./gitGraphLayout";

const c = (sha: string, parents: string[] = []) => ({ sha, parents });

describe("gutterWidth", () => {
  it("is zero for a single lane (linear history must cost nothing)", () => {
    expect(gutterWidth(0)).toBe(0);
    expect(gutterWidth(1)).toBe(0);
    expect(gutterWidth(2)).toBe(34); // 4*2 + 2*13
    expect(gutterWidth(4)).toBe(60);
  });
});

describe("layoutGraph", () => {
  it("keeps a linear history on lane 0 and marks the out-of-window parent", () => {
    const { rows, lanes } = layoutGraph([c("c3", ["c2"]), c("c2", ["c1"]), c("c1", ["gone"])]);
    expect(lanes).toBe(1);
    expect(rows.map((r) => r.lane)).toEqual([0, 0, 0]);
    expect(rows.every((r) => r.ins.length === 0 && r.passIn.length === 0 && r.outs.length === 0)).toBe(true);
    expect(rows[2]).toMatchObject({ cont: true, dangling: true });
    expect(rows[0]).toMatchObject({ cont: true, dangling: false });
  });

  it("forks the second parent of a merge commit into a new lane", () => {
    const { rows, lanes } = layoutGraph([
      c("m", ["a", "b"]), // merge
      c("a", ["a0"]),
      c("b", ["a0"]),
      c("a0", []),
    ]);
    expect(lanes).toBe(2);
    expect(rows[0]).toMatchObject({ lane: 0, outs: [1] });
    expect(rows[1]).toMatchObject({ lane: 0, passIn: [1] });
    expect(rows[2]).toMatchObject({ lane: 1, ins: [] });
  });

  it("converges two children of the same parent into the parent's lane", () => {
    const { rows } = layoutGraph([
      c("m", ["a", "b"]),
      c("a", ["p"]),
      c("b", ["p"]),
      c("p", []),
    ]);
    // 第二子女行进 p 所在列时，另一列必须收敛进来
    expect(rows[3].lane).toBe(0);
    expect(rows[3].ins).toEqual([1]);
  });

  it("releases the node lane when the first parent already owns a lane", () => {
    const { rows } = layoutGraph([
      c("m", ["a", "b"]),
      c("b", ["p"]),
      c("a", ["p"]),
      c("p", []),
    ]);
    // rows[2] 的第一父 p 已在 lane 1 上 → 本行下段不再续，画汇出曲线
    expect(rows[2]).toMatchObject({ lane: 0, cont: false, outs: [1] });
  });

  it("reuses freed lanes instead of growing the lane count", () => {
    const { lanes } = layoutGraph([
      c("m", ["a", "b"]),
      c("a", ["a0"]),
      c("b", ["a0"]),
      c("a0", []),
    ]);
    expect(lanes).toBe(2);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd G:/github/AstrBot_for_spzx/dashboard && npx vitest run src/composables/gitGraphLayout.spec.ts`
Expected: FAIL — `Failed to resolve import "./gitGraphLayout"`。

- [ ] **Step 3: 实现 `layoutGraph`**

`dashboard/src/composables/gitGraphLayout.ts`：按 spec §4.3 的 5 步逐行推进，lane 只复用不重排。要点（算法本身不由签名决定，故给出）：

```
laneSha[]      // 每列期望的下一个 sha（null = 空闲）
laneDangling[] // 该列期望的 sha 不在窗口内
index = Map(sha -> 行号)

逐行：
  before = laneSha.slice()
  lane = laneSha.indexOf(c.sha)；找不到则取最左空位（无空位则 push 新列）
  ins    = before 中 sha === c.sha 且 index !== lane 的列   → 这些列在此行释放
  passIn = before 中非空、非 lane、且不属于 ins 的列
  outs   = []
  对 c.parents 逐个：
    目标列 tgt = laneSha.indexOf(p)
    tgt < 0 时：第一个父提交写回本列（cont 续），其余分配新列后 outs.push
    tgt >= 0 且 tgt !== lane 时：outs.push(tgt)；若是第一个父提交则本列释放（cont = false）
  无父提交 → 本列释放
  cont     = laneSha[lane] != null
  dangling = cont && laneDangling[lane]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd G:/github/AstrBot_for_spzx/dashboard && npx vitest run src/composables/gitGraphLayout.spec.ts`
Expected: PASS，5 passed。

- [ ] **Step 5: 提交**

```bash
git add dashboard/src/composables/gitGraphLayout.ts dashboard/src/composables/gitGraphLayout.spec.ts
git commit -m "feat(graph): pure lane layout for the commit DAG"
```

---

### Task 3 (M2): 渲染组件 `GitLogGraphGutter.vue`

**Files:**
- Create: `dashboard/src/components/chat/message_list_comps/GitLogGraphGutter.vue`
- Test: `dashboard/src/components/chat/message_list_comps/GitLogGraphGutter.spec.ts`

**Interfaces:**
- Consumes: `GraphRow`、`lanes` 宽高常量、`gutterWidth`（Task M1）。
- Produces: 组件 props `{ row: GraphRow; lanes: number; isHead?: boolean }`；DOM 契约（M3 的测试依赖）：
  - 根 `.git-log-gutter`，`style.width = gutterWidth(lanes)px`
  - 贯穿列 `<span class="git-log-gutter-v" data-seg="pass" :data-lane="l">`
  - 节点上段 `data-seg="node-up"`、下段 `data-seg="node-down"`（`cont` 为真才渲染，`:data-dangling="String(row.dangling)"`）
  - 曲线/节点层 `<svg class="git-log-gutter-svg">`，节点 `<circle>` 位于 `cy = NODE_CY`；`isHead` 时额外渲染外环 circle

- [ ] **Step 1: 写失败测试**

```ts
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import GitLogGraphGutter from "./GitLogGraphGutter.vue";
import { NODE_CY, gutterWidth, type GraphRow } from "@/composables/gitGraphLayout";

const row = (over: Partial<GraphRow> = {}): GraphRow => ({
  lane: 0, ins: [], passIn: [], outs: [], cont: false, dangling: false, ...over,
});

describe("GitLogGraphGutter", () => {
  it("renders one vertical per pass-through lane and keeps width from gutterWidth", () => {
    const w = mount(GitLogGraphGutter, { props: { row: row({ passIn: [1, 2] }), lanes: 3 } });
    expect(w.findAll('[data-seg="pass"]').map((v) => v.attributes("data-lane"))).toEqual(["1", "2"]);
    expect(w.attributes("style")).toContain(`width: ${gutterWidth(3)}px`);
  });

  it("draws the node at NODE_CY and only renders the lower half when it continues", () => {
    const w = mount(GitLogGraphGutter, { props: { row: row({ cont: true }), lanes: 2 } });
    expect(w.find("circle").attributes("cy")).toBe(String(NODE_CY));
    expect(w.find('[data-seg="node-down"]').exists()).toBe(true);

    const stopped = mount(GitLogGraphGutter, { props: { row: row({ cont: false }), lanes: 2 } });
    expect(stopped.find('[data-seg="node-down"]').exists()).toBe(false);
  });

  it("marks an out-of-window parent so the lower half can fade", () => {
    const w = mount(GitLogGraphGutter, {
      props: { row: row({ cont: true, dangling: true }), lanes: 2 },
    });
    expect(w.find('[data-seg="node-down"]').attributes("data-dangling")).toBe("true");
  });

  it("draws one path per in/out edge and a head ring when asked", () => {
    const w = mount(GitLogGraphGutter, {
      props: { row: row({ ins: [1], outs: [2], cont: true }), lanes: 3 },
    });
    expect(w.findAll("path")).toHaveLength(2);

    const head = mount(GitLogGraphGutter, {
      props: { row: row({ cont: true }), lanes: 2, isHead: true },
    });
    expect(head.findAll("circle")).toHaveLength(3); // 衬底 + 外环 + 内点
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd G:/github/AstrBot_for_spzx/dashboard && npx vitest run src/components/chat/message_list_comps/GitLogGraphGutter.spec.ts`
Expected: FAIL — 组件文件不存在。

- [ ] **Step 3: 实现组件**

`<script setup lang="ts">` + 计算 `x(l) = GUTTER_PAD + l * LANE_W + LANE_W / 2`；竖线用绝对定位 span（`top:0;bottom:0`，`width:1.5px`，`border-radius:1px`），节点上/下段分别 `top:0;height:NODE_CY` 与 `top:NODE_CY;bottom:0`；曲线用两条三次贝塞尔（收敛 `ins`：`col@0 → lane@NODE_CY`；汇出 `outs`：`lane@NODE_CY → col@BAND`），`stroke-width:1.5`、`stroke-linecap:round`、`fill:none`；节点 `<circle cx=x(lane) cy=NODE_CY r=3.3>`，`isHead` 时补 `r=5.3` 的衬底 + 外环。样式块里定义 `--spcode-graph-l0..l5` 与 `.v-theme--light` 覆盖块；dangling 时下段 `opacity: .32`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd G:/github/AstrBot_for_spzx/dashboard && npx vitest run src/components/chat/message_list_comps/GitLogGraphGutter.spec.ts`
Expected: PASS，4 passed。

- [ ] **Step 5: 提交**

```bash
git add dashboard/src/components/chat/message_list_comps/GitLogGraphGutter.vue \
        dashboard/src/components/chat/message_list_comps/GitLogGraphGutter.spec.ts
git commit -m "feat(graph): lane gutter renderer for log rows"
```

---

### Task 4 (M3): GitLogView 行内接入

**Files:**
- Modify: `dashboard/src/components/chat/message_list_comps/GitLogView.vue`
  - 模板：`.git-log-list` 内的 `.git-log-item`（`v-for="c in commits"`）与其内部 `.git-log-item-select` / `mdi-source-commit` 图标
  - 脚本：`commits` computed 附近新增 `graph` / `graphRows` / `graphLanes` / `gw`
  - 样式：`.git-log-item`、`.git-log-list.squash-selecting .git-log-item`、`.git-log-item-select` 三处
- Test: `dashboard/src/components/chat/message_list_comps/GitLogView.graphGutter.spec.ts`

**Interfaces:**
- Consumes: `layoutGraph` / `gutterWidth`（M1）、`GitLogGraphGutter`（M2）、既有 prop `headSha`、既有 computed `commits`。
- Produces: 行内 `--gw` CSS 变量（供后续选择模式与任何 padding 计算复用）；DOM 契约 `.git-log-gutter` 在 `.git-log-item` 内、位于 `.git-log-item-select` 之前。

- [ ] **Step 1: 写失败测试**

新建 `GitLogView.graphGutter.spec.ts`，**mount 手法与 props 列表直接照抄** `GitLogView.branchPicker.spec.ts` 的 `mountView()`（heavy-stub：`v-icon` / `v-text-field` / `v-autocomplete` / `GitStatsPanel`），只替换 snapshot：

```ts
// makeSnapshot() 里给两个提交：merge 提交 (sha "m1", parents ["a1","b1"]) + 普通提交 (sha "a1", parents ["b1"])
it("renders the gutter and drops the commit icon when the history forks", async () => {
  const w = mountView({ state: { kind: "ok", snapshot: makeForkedSnapshot() } });
  await nextTick();
  expect(w.find(".git-log-gutter").exists()).toBe(true);
  expect(w.find(".git-log-item").attributes("style")).toContain("--gw: 34px");
  expect(w.find(".git-log-item-icon").exists()).toBe(false);
});

it("stays out of the way for a linear history", async () => {
  const w = mountView({ state: { kind: "ok", snapshot: makeLinearSnapshot() } });
  await nextTick();
  expect(w.find(".git-log-gutter").exists()).toBe(false);
  expect(w.find(".git-log-item").attributes("style")).toContain("--gw: 0px");
  expect(w.find(".git-log-item-icon").exists()).toBe(true);
});

it("keeps the gutter while the squash selection UI is armed", async () => {
  // 进入选择模式的唯一真实入口是 toolbar 的 squash 按钮 —— 逐字照抄
  // GitLogView.squash.spec.ts 里既有的 arm 手法（同一个 mountView + 同一个
  // 按钮 selector），不要另造 props。
  const w = mountView({ state: { kind: "ok", snapshot: makeForkedSnapshot() } });
  await armSquashSelection(w); // ← 复制 GitLogView.squash.spec.ts 的现成 helper
  expect(w.find(".git-log-list").classes()).toContain("squash-selecting");
  expect(w.find(".git-log-gutter").exists()).toBe(true);
});
```

> 第三个用例的第一步是**读** `GitLogView.squash.spec.ts`，把它 arm 选择模式的代码逐字搬进本 spec 的 helper；断言只保留"class 在 + gutter 仍在"。若该文件用的是别的机制（例如给 `q` 之类的外部状态），则以真实机制为准 —— 本用例的价值是钉住"选择模式不隐藏 gutter"，不是钉住 arm 手法。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd G:/github/AstrBot_for_spzx/dashboard && npx vitest run src/components/chat/message_list_comps/GitLogView.graphGutter.spec.ts`
Expected: FAIL — `.git-log-gutter` 不存在。

- [ ] **Step 3: 接线**

脚本：
```ts
const graph = computed(() => layoutGraph(commits.value.map((c) => ({ sha: c.sha, parents: c.parents }))));
const graphRows = computed(() => new Map(graph.value.rows.map((r, i) => [commits.value[i].sha, r])));
const graphLanes = computed(() => graph.value.lanes);
const gw = computed(() => gutterWidth(graphLanes.value));
```
模板（`.git-log-item` 上）：
```html
:style="{ '--gw': gw + 'px' }"
```
在 `.git-log-item-select` 之前插入：
```html
<GitLogGraphGutter v-if="gw > 0" :row="graphRows.get(c.sha)!" :lanes="graphLanes" :is-head="c.sha === headSha" />
```
`mdi-source-commit` 图标加 `v-if="gw === 0"`。

样式三处：
```css
.git-log-item { padding: 8px 12px 8px calc(12px + var(--gw, 0px)); }
.git-log-list.squash-selecting .git-log-item { padding-left: calc(32px + var(--gw, 0px)); }
.git-log-item-select { left: calc(8px + var(--gw, 0px)); }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd G:/github/AstrBot_for_spzx/dashboard && npx vitest run src/components/chat/message_list_comps/GitLogView.graphGutter.spec.ts src/components/chat/message_list_comps/GitLogView.branchPicker.spec.ts src/components/chat/message_list_comps/GitLogView.tags.spec.ts src/components/chat/message_list_comps/GitLogView.reset.spec.ts`
Expected: 全绿（既有 GitLogView 用例零回归）。

- [ ] **Step 5: 提交**

```bash
git add dashboard/src/components/chat/message_list_comps/GitLogView.vue \
        dashboard/src/components/chat/message_list_comps/GitLogView.graphGutter.spec.ts
git commit -m "feat(graph): render lane gutter inside the history rows"
```

---

### Task 5 (M4): 「所有分支」入口 + 参数透传 + i18n

**Files:**
- Modify: `dashboard/src/composables/useSpcodeGitLog.ts`（`LogFilter` 类型、`etagKey`、`refresh` 的 params）
- Modify: `dashboard/src/components/chat/GitDiffSidebar.vue`（`branchPickerItems` computed、`onLogApply`）
- Modify: `dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU}/features/chat.json`
- Modify: `dashboard/src/i18n/i18n.completeness.spec.ts`
- Test: `dashboard/src/composables/useSpcodeGitLog.allRefs.spec.ts`

**Interfaces:**
- Consumes: P1 的 `all` / `topo` 参数语义。
- Produces: `export const ALL_REFS_SENTINEL = "__spcode_all_refs__"`（`useSpcodeGitLog.ts`）；`LogFilter.allRefs?: boolean`；渲染出的请求参数 `all=true&topo=true` 且不带 `ref`。

- [ ] **Step 1: 写失败测试（参数透传）**

新建 `useSpcodeGitLog.allRefs.spec.ts`：mock `@/api/v1` 的 `pluginExtensionApi.get`（照抄 `useSpcodeGitBranches.spec.ts` 的 `vi.mock("@/api/v1", …)` 手法），断言：

```ts
it("sends all+topo and omits ref when allRefs is on", async () => {
  const { refresh, filter } = useSpcodeGitLog({ umo, worktree });
  filter.value.allRefs = true;
  await refresh();
  const [, opts] = getMock.mock.calls.at(-1)!;
  expect(opts.params).toMatchObject({ all: "true", topo: "true" });
  expect(opts.params.ref).toBeUndefined();
});

it("buckets a separate ETag for the allRefs mode", async () => {
  // 第一次 refresh（allRefs off）返回带 etag 的 200；切到 allRefs 后
  // 第二次 refresh 必须**不带** If-None-Match —— 断言 headers 里没有该键
  // （若复用同一 bucket，这里会带上默认模式的 ETag 并 304 回放旧快照）。
  const first = getMock.mock.calls.at(-1)!;
  expect(first[1].headers["If-None-Match"]).toBeUndefined();
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd G:/github/AstrBot_for_spzx/dashboard && npx vitest run src/composables/useSpcodeGitLog.allRefs.spec.ts`
Expected: FAIL — params 里没有 `all`。

- [ ] **Step 3: 实现透传 + 入口 + 文案**

1. `useSpcodeGitLog.ts`：`LogFilter` 加 `allRefs?: boolean`；`etagKey` 的返回数组加一项 `f.allRefs ? "ALL" : ""`；`refresh` 内
   ```ts
   const effectiveRef = filter.value.allRefs ? "" : filter.value.rev || filter.value.ref;
   ...
   ...(filter.value.allRefs ? { all: "true", topo: "true" } : {}),
   ```
   并导出 `export const ALL_REFS_SENTINEL = "__spcode_all_refs__";`。
2. `GitDiffSidebar.vue`：`branchPickerItems` 首项改为哨兵条目（在 `{ title: "HEAD", value: "HEAD" }` 之前）：
   ```ts
   { title: tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.allRefs"), value: ALL_REFS_SENTINEL }
   ```
   `onLogApply(filter)` 内翻译哨兵：
   ```ts
   const isAll = filter.ref === ALL_REFS_SENTINEL;
   void gitLog.refresh({ ...filter, ref: isAll ? undefined : filter.ref, rev: isAll ? null : filter.rev, allRefs: isAll });
   ```
3. 三个 locale 的 `spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter` 下加 `allRefs`：zh-CN `所有分支`、en-US `All branches`、ru-RU `Все ветки`。
4. `i18n.completeness.spec.ts` 追加一个 `it`，断言三语都能取到该键路径（照抄文件内既有 `filter.apply + .reset` 用例的取键写法）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd G:/github/AstrBot_for_spzx/dashboard && npx vitest run src/composables/useSpcodeGitLog.allRefs.spec.ts src/i18n/i18n.completeness.spec.ts src/components/chat/message_list_comps/GitLogView.branchPicker.spec.ts`
Expected: 全绿。

- [ ] **Step 5: 类型检查 + 提交**

Run: `cd G:/github/AstrBot_for_spzx/dashboard && npm run typecheck`
Expected: 无错误。

```bash
git add dashboard/src/composables/useSpcodeGitLog.ts dashboard/src/components/chat/GitDiffSidebar.vue \
        dashboard/src/i18n/locales dashboard/src/i18n/i18n.completeness.spec.ts \
        dashboard/src/composables/useSpcodeGitLog.allRefs.spec.ts
git commit -m "feat(graph): all-branches scope entry in the history branch picker"
```

---

### Task 6 (Z1): 收尾验证

**Files:** 无新增；两个仓库各一次全量验证。

- [ ] **Step 1: 插件全量**

Run: `cd G:/github/astrbot_plugin_spcode_toolkit && ruff check . && "G:/github/AstrBot_for_spzx/venv/python.exe" -m pytest tests/ -q`
Expected: ruff 无 error；pytest 全绿。

- [ ] **Step 2: 前端全量**

Run: `cd G:/github/AstrBot_for_spzx/dashboard && npx vitest run && npm run typecheck`
Expected: 全绿 + 类型无误。

- [ ] **Step 3: 手工验证矩阵（记入交接说明）**

在真实实例里逐个走：① 单分支仓库（gutter 不出现、无空白）；② 有未合并分支 + 「所有分支」；③ 展开一个提交（竖线贯穿展开区）；④ squash 选择模式（复选框与节点不重叠）；⑤ 窗口被截断的仓库（末端淡化短线 + truncated 横幅）。
