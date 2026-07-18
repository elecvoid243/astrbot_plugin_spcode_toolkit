### Task 2: ETag helper + `handle()` + 路由注册 + 端级测试

**Files:**
- Modify: `tools/webapi/git_stats.py`（追加 `_compute_stats_etag` 与 `handle`）
- Modify: `tools/webapi/__init__.py`（import + ROUTES 一行）
- Modify: `tests/test_webapi_end_to_end.py:389`（35 → 36）
- Test: `tests/test_git_stats.py`（追加 handler 测试）

**Interfaces:**
- Consumes: Task 1 的 `_parse_stats_log_output` / `_aggregate_stats` / `_PRETTY`；`_helpers._git_endpoint_preflight` 返回 `(err, ctx)`，`ctx = {"directory", "umo", "worktree"}`
- Produces: `handle(plugin: "SPCodeToolkit", *, umo: str | None = None, worktree: str | None = None) -> dict`，envelope data 字段见 spec §3.2；`_gs._STATS_ETAG_CACHE` / `_gs._STATS_ETAG_TTL`（测试用来清空/调零，镜像 `_LOG_ETAG_CACHE` 用法）；`_compute_stats_etag(...) -> tuple[str, str]` 返回 `(etag, head_sha)`，缓存值三元组 `(etag, head_sha, ts)`

- [ ] **Step 1: 写失败 handler 测试（追加到 `tests/test_git_stats.py` 末尾）**

```python
# ── Task 2: handler e2e tests (real git) ──


async def test_handle_aggregation_e2e(monkeypatch, plugin, tmp_path: Path):
    """2 commits across 2 days → days/totals/hot_files/range 精确匹配。"""
    _init_git_repo(tmp_path)
    _commit(tmp_path, {"a.py": "1\n2\n"}, "2026-07-10T10:00:00+08:00")
    _commit(
        tmp_path,
        {"a.py": "1\n2\n3\n", "b.md": "x\n"},
        "2026-07-11T10:00:00+08:00",
    )
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    data = result["data"]
    assert data["reason"] is None
    assert data["loaded"] is True
    assert data["totals"] == {
        "commits": 2,
        "additions": 4,
        "deletions": 0,
        "files_changed": 2,
    }
    assert data["days"] == [
        {"date": "2026-07-10", "commits": 1, "additions": 2, "deletions": 0},
        {"date": "2026-07-11", "commits": 1, "additions": 2, "deletions": 0},
    ]
    assert data["hot_files"][0] == {
        "path": "a.py",
        "commits": 2,
        "additions": 3,
        "deletions": 0,
    }
    assert data["range"] == {"first": "2026-07-10", "last": "2026-07-11"}
    assert data["truncated"] is False
    assert data["max_commits"] == 5000


async def test_handle_merge_commit_counted_with_zero_lines(
    monkeypatch, plugin, tmp_path: Path
):
    """merge commit 计入 commits、行数为 0(关键不变量:无 numstat 行)。"""
    _init_git_repo(tmp_path)
    _commit(tmp_path, {"a.py": "base\n"}, "2026-07-10T10:00:00+08:00")
    _git(tmp_path, "checkout", "-q", "-b", "feat")
    _commit(tmp_path, {"b.py": "x\n"}, "2026-07-11T10:00:00+08:00", message="feat")
    _git(tmp_path, "checkout", "-q", "main")
    _commit(
        tmp_path, {"c.py": "y\n"}, "2026-07-12T10:00:00+08:00", message="main-work"
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-13T10:00:00+08:00",
        "GIT_COMMITTER_DATE": "2026-07-13T10:00:00+08:00",
    }
    _git(tmp_path, "merge", "--no-ff", "-m", "merge feat", "feat", env=env)
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    data = result["data"]
    assert data["totals"]["commits"] == 4  # base + feat + main-work + merge
    day_0713 = next(d for d in data["days"] if d["date"] == "2026-07-13")
    assert day_0713["commits"] == 1
    assert day_0713["additions"] == 0
    assert day_0713["deletions"] == 0


async def test_handle_binary_file_zero_lines(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    _git(tmp_path, "add", ".")
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-10T10:00:00+08:00",
        "GIT_COMMITTER_DATE": "2026-07-10T10:00:00+08:00",
    }
    _git(tmp_path, "commit", "-q", "-m", "bin", env=env)
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    data = result["data"]
    assert data["totals"]["commits"] == 1
    assert data["totals"]["additions"] == 0
    assert data["totals"]["deletions"] == 0
    assert data["hot_files"] == [
        {"path": "logo.png", "commits": 1, "additions": 0, "deletions": 0}
    ]


async def test_handle_rename_counted_as_delete_add(
    monkeypatch, plugin, tmp_path: Path
):
    """--no-renames 下 rename 按删旧+增新计入(旧路径 del、新路径 add)。"""
    _init_git_repo(tmp_path)
    _commit(tmp_path, {"old.py": "1\n2\n3\n"}, "2026-07-10T10:00:00+08:00")
    _git(tmp_path, "mv", "old.py", "new.py")
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-11T10:00:00+08:00",
        "GIT_COMMITTER_DATE": "2026-07-11T10:00:00+08:00",
    }
    _git(tmp_path, "commit", "-q", "-m", "rename", env=env)
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    data = result["data"]
    by_path = {f["path"]: f for f in data["hot_files"]}
    # 新路径: rename commit 的 +3;旧路径: 初始 +3 与 rename 的 -3
    assert by_path["new.py"]["additions"] == 3
    assert by_path["old.py"]["deletions"] == 3


async def test_handle_max_commits_truncation(monkeypatch, plugin, tmp_path: Path):
    """max_commits=2 对 3 commits → truncated=true,只统计最新 2 条。"""
    _init_git_repo(tmp_path)
    for i in range(3):
        _commit(
            tmp_path, {"f.py": f"{i}\n"}, f"2026-07-{10 + i}T10:00:00+08:00"
        )
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(
        monkeypatch, plugin, query={"max_commits": "2"}
    )
    data = result["data"]
    assert data["truncated"] is True
    assert data["max_commits"] == 2
    assert data["totals"]["commits"] == 2
    assert data["range"]["first"] == "2026-07-11"  # 最老一条被丢弃


async def test_handle_since_until_passthrough(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    for i in range(3):
        _commit(
            tmp_path, {"f.py": f"{i}\n"}, f"2026-07-{10 + i}T10:00:00+08:00"
        )
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(
        monkeypatch,
        plugin,
        query={"since": "2026-07-11T00:00:00", "until": "2026-07-11T23:59:59"},
    )
    data = result["data"]
    assert data["totals"]["commits"] == 1
    # 07-11 的 commit 把 f.py 从 "0\n" 改写为 "1\n": 一行删+一行增
    assert data["days"] == [
        {"date": "2026-07-11", "commits": 1, "additions": 1, "deletions": 1}
    ]


async def test_handle_invalid_params(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    _commit(tmp_path, {"a.py": "1\n"}, "2026-07-10T10:00:00+08:00")
    _load_project(plugin, "test:umo", str(tmp_path))

    for query in (
        {"max_commits": "abc"},
        {"max_commits": "0"},
        {"max_commits": "20001"},
        {"top_files": "0"},
        {"top_files": "51"},
        {"top_files": "x"},
        {"ref": "-n"},  # 选项注入防御
        {"since": "not-a-date"},
        {"until": "2026/07/10"},
    ):
        result = await _call_with_query(monkeypatch, plugin, query=query)
        assert result["data"]["reason"] == "invalid_param", (
            f"query={query} should be invalid_param, got {result['data']}"
        )


async def test_handle_no_project_loaded(monkeypatch, plugin):
    result = await _call_with_query(monkeypatch, plugin, umo="ghost:umo")
    assert result["data"]["reason"] == "no_project_loaded"


async def test_handle_not_a_git_repo(monkeypatch, plugin, tmp_path: Path):
    non_git = tmp_path / "plain"
    non_git.mkdir()
    _load_project(plugin, "test:umo", str(non_git))
    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["reason"] == "not_a_git_repo"


async def test_handle_empty_repository(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path)  # 无 commit
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["reason"] == "empty_repository"


async def test_handle_etag_304_short_circuit(monkeypatch, plugin, tmp_path: Path):
    """同 key 二次请求带 If-None-Match → 304;TTL=0 强制重算。"""
    _init_git_repo(tmp_path)
    _commit(tmp_path, {"a.py": "1\n"}, "2026-07-10T10:00:00+08:00")
    _load_project(plugin, "test:umo", str(tmp_path))
    _gs._STATS_ETAG_CACHE.clear()
    monkeypatch.setattr(_gs, "_STATS_ETAG_TTL", 0.0)

    r1 = await _call_with_query(monkeypatch, plugin)
    assert r1.status_code == 200
    etag = r1.headers.get("etag")
    assert etag, f"first response missing ETag: {dict(r1.headers)}"

    r2 = await _call_with_query(
        monkeypatch, plugin, headers={"If-None-Match": etag}
    )
    assert r2.status_code == 304
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_git_stats.py -x -q`
Expected: FAIL — `AttributeError: module 'tools.webapi.git_stats' has no attribute 'handle'`（或 `_STATS_ETAG_CACHE`）

- [ ] **Step 3: 实现 ETag helper 与 `handle()`（追加到 `tools/webapi/git_stats.py`）**

在 `_aggregate_stats` 之后追加：

```python
# ──────────────────────────────────────────────────────────
# ETag 计算(镜像 git_log._compute_log_etag,独立缓存实例)
# ──────────────────────────────────────────────────────────


async def _compute_stats_etag(
    git_bin: str,
    directory: str,
    *,
    query_fingerprint: str = "",
) -> tuple[str, str]:
    """Compute a weak ETag for the git-stats endpoint.

    Args:
        git_bin: Resolved git executable path.
        directory: Worktree root.
        query_fingerprint: Stable ``|``-joined fingerprint of the query
            params (ref/max_commits/top_files/since/until) so any param
            change yields a different ETag (no 304 staleness).

    Returns:
        ``(etag, head_sha)`` — weak ETag string plus the resolved HEAD
        sha (``"no-head"`` when unresolvable). head_sha doubles as the
        envelope's ``resolved_sha`` field, sparing a second subprocess.
    """
    cache_key = (
        f"{directory}\x00{query_fingerprint}" if query_fingerprint else directory
    )
    now = _time.monotonic()
    cached = _STATS_ETAG_CACHE.get(cache_key)
    if cached is not None and (now - cached[2]) < _STATS_ETAG_TTL:
        _STATS_ETAG_CACHE.move_to_end(cache_key)
        return cached[0], cached[1]

    head_sha = "no-head"
    try:
        head_result = await _run_git_async(
            [git_bin, "-C", directory, "rev-parse", "HEAD"],
            timeout=5.0,
            encoding="utf-8",
        )
        if head_result.get("ok") and head_result.get("stdout"):
            head_sha = head_result["stdout"].strip()
    except Exception:
        pass

    wt_mtime = 0
    try:
        wt_mtime = int(Path(directory).stat().st_mtime)
    except OSError:
        pass

    idx_mtime = 0
    try:
        idx_mtime = int((Path(directory) / ".git" / "index").stat().st_mtime)
    except OSError:
        pass

    if query_fingerprint:
        etag = f'W/"{head_sha}-{wt_mtime}-{idx_mtime}-{query_fingerprint}"'
    else:
        etag = f'W/"{head_sha}-{wt_mtime}-{idx_mtime}"'

    _STATS_ETAG_CACHE[cache_key] = (etag, head_sha, now)
    while len(_STATS_ETAG_CACHE) > _STATS_ETAG_CACHE_MAX:
        _STATS_ETAG_CACHE.popitem(last=False)
    return etag, head_sha


# ──────────────────────────────────────────────────────────
# Handler
# ──────────────────────────────────────────────────────────


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> dict:
    """GET /spcode/git-stats handler.

    Spec: docs/superpowers/specs/2026-07-18-git-stats-endpoint-design.md
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. Query 参数解析 ──
    from astrbot.api import web

    query = web.request.query if hasattr(web, "request") else {}

    def _qget(key: str, default: str | None = None) -> str | None:
        try:
            v = query.get(key)
            return v if v else default
        except Exception:
            return default

    def _invalid() -> dict:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            loaded=False,
            umo=umo,
            worktree=worktree,
        )

    max_commits_raw = _qget("max_commits")
    if max_commits_raw is not None:
        try:
            max_commits = int(max_commits_raw)
        except ValueError:
            return _invalid()
        if not (1 <= max_commits <= MAX_COMMITS_HARD):
            return _invalid()
    else:
        max_commits = MAX_COMMITS_DEFAULT

    top_files_raw = _qget("top_files")
    if top_files_raw is not None:
        try:
            top_files = int(top_files_raw)
        except ValueError:
            return _invalid()
        if not (1 <= top_files <= TOP_FILES_HARD):
            return _invalid()
    else:
        top_files = TOP_FILES_DEFAULT

    ref = _qget("ref") or "HEAD"
    since = _qget("since")
    until = _qget("until")

    # 长度 + 选项注入 + ISO 校验
    if len(ref) > MAX_PARAM_LENGTH or ref.startswith("-"):
        return _invalid()
    for val in (since, until):
        if val and (len(val) > MAX_PARAM_LENGTH or not _ISO_PARAM_RE.match(val)):
            return _invalid()

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    # ── 3. ETag 检查(query 指纹纳入,防 304 误判) ──
    query_fingerprint = (
        f"{ref}|{max_commits}|{top_files}|{since or ''}|{until or ''}"
    )
    etag, resolved_sha = await _compute_stats_etag(
        plugin._git_binary(),
        directory,
        query_fingerprint=query_fingerprint,
    )
    cache_headers = _common_cache_headers(etag)
    if _get_if_none_match() == etag:
        return _make_304_response(cache_headers)

    # ── 4. 单次 git log 调用(max+1 判 truncated) ──
    git_bin = plugin._git_binary()
    log_args = [
        git_bin,
        "-C",
        directory,
        "-c",
        "color.ui=never",
        "log",
        f"--pretty={_PRETTY}",
        "--numstat",
        "--no-renames",
        f"-n{max_commits + 1}",
    ]
    if since:
        log_args.append(f"--since={since}")
    if until:
        log_args.append(f"--until={until}")
    log_args.append(ref)

    raw_result = await _run_git_async(log_args, encoding="utf-8")
    if not raw_result["ok"]:
        stderr = raw_result.get("stderr", "")
        if "does not have any commits" in stderr or "ambiguous" in stderr.lower():
            reason = ReasonCode.EMPTY_REPOSITORY
        else:
            reason = ReasonCode.GIT_ERROR
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr,
        )

    raw = raw_result["stdout"]
    if len(raw) > MAX_STATS_BYTES:
        return _make_envelope(
            success=False,
            reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"git output exceeded {MAX_STATS_BYTES} bytes",
        )

    # ── 5. 解析 + 截断 + 聚合 ──
    commits = _parse_stats_log_output(raw)
    truncated = len(commits) > max_commits
    if truncated:
        commits = commits[:max_commits]  # git log 新→旧,丢弃最老的
    agg = _aggregate_stats(commits, top_files)

    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            loaded=True,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            ref=ref,
            resolved_sha=resolved_sha,
            days=agg["days"],
            hot_files=agg["hot_files"],
            totals=agg["totals"],
            range=agg["range"],
            truncated=truncated,
            max_commits=max_commits,
        ),
        status_code=200,
        headers=cache_headers,
    )
```

- [ ] **Step 4: 注册路由**

`tools/webapi/__init__.py`：
1. import 区（按字母序，在 `git_show` 之后）加 `git_stats,`；
2. docstring 端点清单追加 ``* ``/spcode/git-stats``       (GET)   # v2.21 (2026-07-18)``；
3. `ROUTES` 列表（建议放在 `git-show` 条目之后）追加：

```python
    (
        "/spcode/git-stats",  # v2.21 (2026-07-18)
        ["GET"],
        git_stats.handle,
        "获取已加载项目的变更统计(按日聚合 + 热点文件,供 stats 面板调用)",
    ),
```

`tests/test_webapi_end_to_end.py:389`：`assert plugin.context.register_web_api.call_count == 35` → `== 36`。

- [ ] **Step 5: 运行新测试确认通过**

Run: `python -m pytest tests/test_git_stats.py tests/test_webapi_end_to_end.py -q`
Expected: 全部 passed（18 + end_to_end 既有用例）

- [ ] **Step 6: 全量回归 + lint**

Run: `python -m pytest tests/ -q`
Expected: 全部 passed（基线 1318 + 新增 18 ≈ 1336，0 failed）

Run: `ruff format tools/webapi/git_stats.py tests/test_git_stats.py && ruff check tools/webapi/git_stats.py tests/test_git_stats.py`
Expected: 无改动/无告警（如有自动修复则复跑 Step 5）

- [ ] **Step 7: Commit**

```bash
git add tools/webapi/git_stats.py tools/webapi/__init__.py tests/test_git_stats.py tests/test_webapi_end_to_end.py
git commit -m "feat: add GET /spcode/git-stats endpoint with server-side aggregation"
```

---
