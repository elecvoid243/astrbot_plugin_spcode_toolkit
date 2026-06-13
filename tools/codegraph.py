"""
codegraph — 代码语义索引与查询引擎。

- Python: 纯 ast 解析（零依赖）
- JS/TS/Go/Rust: 可选 tree-sitter（pip install tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-typescript tree-sitter-go tree-sitter-rust）

特性：
- SQLite + FTS5 全文检索（系统 Python sqlite3 默认带 FTS5）
- 增量索引（mtime 比对）
- 自然语言查询路由（"从 A 到 B"、"X 在哪"）
- 调用链 BFS 追踪
"""

from __future__ import annotations

import ast as py_ast
import json
import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path

try:
    import tree_sitter
    import tree_sitter_python as ts_py

    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False

# FTS 搜索的最小词长
_FTS_MIN_LENGTH = 2

# 文件后缀 → 语言
_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".h": "cpp",
    ".c": "cpp",  # 纯 C 暂用 cpp 语法解析（参见 codegraph_cpp.py 文档）
}

# 索引时忽略的目录
_DEFAULT_IGNORE = {
    "__pycache__",
    ".git",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".eggs",
    "build",
    "dist",
    "target",
    ".codegraph",
}

# 查询路由正则（自然语言 → 查询类型）
_QUERY_ROUTES = [
    (re.compile(r"从\s+(\S+)\s+到\s+(\S+)"), "trace_closed"),
    (re.compile(r"from\s+(\S+)\s+to\s+(\S+)", re.IGNORECASE), "trace_closed"),
    (re.compile(r"(\S+)\s*→\s*(\S+)"), "trace_closed"),
    (
        re.compile(
            r"调用链|calls?\s*chain|trace|怎么走|how.*call|who.*calls?", re.IGNORECASE
        ),
        "trace_open",
    ),
    (
        re.compile(
            r"在哪|哪里|定义了?|定义在|where.*(?:is|defined)|locate|find.*symbol",
            re.IGNORECASE,
        ),
        "symbol_search",
    ),
]

# 搜索时剥离的停用词
_SEARCH_NOISE = {
    "在哪",
    "哪里",
    "定义了",
    "定义在",
    "where",
    "is",
    "find",
    "locate",
    "的",
    "怎么",
}


# ── 主类 ───────────────────────────────────────────


class CodeGraph:
    """代码语义索引核心。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._ensure_db()

    def _ensure_db(self) -> None:
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                file TEXT NOT NULL,
                line INTEGER,
                signature TEXT,
                source TEXT,
                doc TEXT,
                visibility TEXT,
                is_async INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_sym TEXT NOT NULL,
                to_sym TEXT NOT NULL,
                kind TEXT NOT NULL,
                file TEXT,
                line INTEGER,
                resolved INTEGER DEFAULT 0,
                lang TEXT
            )
        """)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sym_name ON symbols(name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sym_file ON symbols(file)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_from ON edges(from_sym)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_to ON edges(to_sym)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_kind ON edges(kind)")
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS sym_fts USING fts5(name, file, signature)"
            )
        except Exception:
            # FTS5 不可用时降级到 LIKE 搜索
            pass
        conn.commit()
        self._conn = conn

    def _conn_get(self) -> sqlite3.Connection:
        if self._conn is None:
            self._ensure_db()
            self._conn = sqlite3.connect(self._db_path)
        return self._conn

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ── index ───────────────────────────────────────

    def index(self, project_dir: str, incremental: bool = False) -> dict:
        """为项目建立（增量）语义索引。"""
        root = Path(project_dir).resolve()
        if not root.is_dir():
            return {"ok": False, "error": f"不是有效目录: {project_dir}"}

        start = time.time()
        conn = self._conn_get()
        if not incremental:
            conn.execute("DELETE FROM symbols")
            conn.execute("DELETE FROM edges")

        stats = {"files": 0, "symbols": 0, "edges": 0, "skipped": 0}
        mtimes: dict[str, float] = {}
        if incremental:
            row = conn.execute("SELECT value FROM meta WHERE key='mtimes'").fetchone()
            if row:
                try:
                    mtimes = json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    mtimes = {}

        for fpath in root.rglob("*"):
            if any(p in _DEFAULT_IGNORE for p in fpath.parts):
                continue
            if not fpath.is_file():
                continue
            suffix = fpath.suffix.lower()
            if suffix not in _LANG_MAP:
                continue

            rp = str(fpath.relative_to(root))
            fmtime = fpath.stat().st_mtime
            if incremental and rp in mtimes and mtimes[rp] == fmtime:
                continue

            if incremental:
                # 增量模式：先清该文件旧记录再插新
                conn.execute("DELETE FROM symbols WHERE file=?", (rp,))
                conn.execute("DELETE FROM edges WHERE file=?", (rp,))
            mtimes[rp] = fmtime

            try:
                symbols, edges = _extract_file(str(fpath), suffix)
            except (SyntaxError, Exception):
                stats["skipped"] += 1
                continue

            for s in symbols:
                conn.execute(
                    "INSERT INTO symbols(name,kind,file,line,signature,source,doc,visibility,is_async) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        s["name"],
                        s["kind"],
                        rp,
                        s.get("line"),
                        s.get("signature"),
                        s.get("source"),
                        s.get("doc"),
                        s.get("visibility"),
                        s.get("is_async", 0),
                    ),
                )
            for e in edges:
                conn.execute(
                    "INSERT INTO edges(from_sym,to_sym,kind,file,line,lang) VALUES(?,?,?,?,?,?)",
                    (e["from"], e["to"], e["kind"], rp, e.get("line"), e.get("lang")),
                )
            stats["symbols"] += len(symbols)
            stats["edges"] += len(edges)
            stats["files"] += 1

        # 后处理：引用消解
        try:
            _resolve_references(conn)
        except Exception:
            pass

        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('mtimes',?)",
            (json.dumps(mtimes),),
        )
        conn.commit()
        try:
            conn.execute("DELETE FROM sym_fts")
            conn.execute(
                "INSERT INTO sym_fts(name, file, signature) "
                "SELECT name, file, signature FROM symbols"
            )
        except Exception:
            pass
        elapsed = round(time.time() - start, 2)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            ("last_index", str(time.time())),
        )
        conn.commit()

        summary = (
            f"索引完成：{stats['files']} 文件, {stats['symbols']} 符号, "
            f"{stats['edges']} 边, 耗时 {elapsed}s"
        )
        if stats["skipped"]:
            summary += f", {stats['skipped']} 文件因语法错误跳过"
        return {"ok": True, "summary": summary, "stats": stats}

    # ── explore ─────────────────────────────────────

    def explore(self, query: str, project_dir: str = ".") -> dict:
        """自然语言查询：符号查找、调用链追踪、代码探索。"""
        if not query or not query.strip():
            return {
                "ok": False,
                "error": "empty_query",
                "summary": "查询为空。——请提供符号名、调用链或自然语言描述。",
            }
        conn = self._conn_get()
        row = conn.execute("SELECT value FROM meta WHERE key='last_index'").fetchone()
        if not row:
            return {
                "ok": False,
                "error": "no_index",
                "summary": "尚未建立语义索引。",
                "hint": f"运行 code_index('{os.path.abspath(project_dir)}') 建索引（首次 ~0.5-5s）。",
            }

        qtype, match = _route_query(query)

        if qtype == "trace_closed" and match and match.re.groups >= 2:
            return self._trace_path(
                conn,
                match.group(1).strip(),
                match.group(2).strip(),
                project_dir,
            )
        if qtype == "trace_open":
            return self._trace_open(conn, query, project_dir)
        if qtype == "symbol_search":
            target = query
            for kw in _SEARCH_NOISE:
                target = target.replace(kw, " ")
            target = " ".join(target.split())
            return self._search_symbol(conn, target, project_dir)
        return self._explore_fallback(conn, query, project_dir)

    def _search_symbol(self, conn, target: str, project_dir: str) -> dict:
        symbols, strategy = self._search(conn, target)
        if not symbols:
            return {
                "ok": True,
                "found": False,
                "query_type": "symbol_search",
                "summary": f"未找到符号 '{target}'。",
                "search_strategy": strategy,
            }
        callers: dict[str, list[str]] = {}
        for s in symbols[:5]:
            rows = conn.execute(
                "SELECT from_sym FROM edges WHERE to_sym=? AND kind='calls' LIMIT 5",
                (s["name"],),
            ).fetchall()
            callers[s["name"]] = [r[0] for r in rows]
        return {
            "ok": True,
            "found": True,
            "query_type": "symbol_search",
            "summary": f"找到 {len(symbols)} 个匹配 '{target}' 的符号。",
            "symbols": symbols[:10],
            "callers": callers,
            "search_strategy": strategy,
            "hint": "需要调用链？用 code_explore('从 X 到 Y') 精确追踪。",
        }

    def _trace_path(self, conn, from_sym: str, to_sym: str, project_dir: str) -> dict:
        path = _bfs_path(conn, from_sym, to_sym, max_depth=6)
        if path:
            return {
                "ok": True,
                "found": True,
                "query_type": "trace",
                "summary": f"从 {from_sym} 到 {to_sym} 的调用链 ({len(path) - 1} 跳): {' → '.join(path)}",
                "path": path,
            }
        # 长路径兜底
        fwd = _bfs_path(conn, from_sym, to_sym, max_depth=10)
        if fwd:
            return {
                "ok": True,
                "found": True,
                "query_type": "trace",
                "summary": f"(长路径 {len(fwd) - 1} 跳) {' → '.join(fwd)}",
                "path": fwd,
            }
        return {
            "ok": True,
            "found": False,
            "query_type": "trace",
            "summary": f"从 {from_sym} 到 {to_sym} BFS 未找到调用链——可能是事件/装饰器连接。",
            "unresolved": [
                {"from": from_sym, "to": to_sym, "reason": "BFS 未找到路径"}
            ],
        }

    def _trace_open(self, conn, query: str, project_dir: str) -> dict:
        symbols, _ = self._search(conn, query)
        if not symbols:
            return {
                "ok": True,
                "found": False,
                "query_type": "trace",
                "summary": f"未找到与 '{query}' 相关的符号。",
                "hint": "试试更精确的函数名或符号名。",
            }
        sname = symbols[0]["name"]
        callers = [
            r[0]
            for r in conn.execute(
                "SELECT from_sym FROM edges WHERE to_sym=? AND kind='calls' LIMIT 8",
                (sname,),
            ).fetchall()
        ]
        callees = [
            r[0]
            for r in conn.execute(
                "SELECT to_sym FROM edges WHERE from_sym=? AND kind='calls' LIMIT 8",
                (sname,),
            ).fetchall()
        ]
        return {
            "ok": True,
            "found": True,
            "query_type": "trace",
            "summary": f"{sname} 的调用关系：{len(callers)} 个调用者, {len(callees)} 个被调用者。",
            "symbol": symbols[0],
            "callers": callers,
            "callees": callees,
            "hint": f"精确追踪：code_explore('从 X → {sname}')",
        }

    def _explore_fallback(self, conn, query: str, project_dir: str) -> dict:
        symbols, strategy = self._search(conn, query)
        if not symbols or strategy == "none":
            summary = f"自然语言探索 '{query}'：未命中索引。"
        else:
            summary = f"自然语言探索 '{query}'：找到 {len(symbols)} 个相关符号。"
        return {
            "ok": True,
            "found": len(symbols) > 0,
            "query_type": "explore",
            "summary": summary,
            "symbols": symbols,
            "search_strategy": strategy,
            "hint": "缩小范围：用符号名精确搜索，或 '从 X 到 Y' 追踪调用链。",
        }

    def _search(self, conn, query: str, limit: int = 10) -> tuple[list[dict], str]:
        """三级搜索：LIKE → FTS5 → 无结果提示。"""
        try:
            # Level 1: LIKE 精确匹配
            rows = conn.execute(
                "SELECT name,kind,file,line,signature,source,visibility,is_async "
                "FROM symbols WHERE name LIKE ? LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            if rows:
                return [_row_to_dict(r) for r in rows], "like"
        except Exception:
            pass

        # Level 2: FTS5 全文搜索
        try:
            tokens = [t for t in re.split(r"\s+", query) if len(t) >= _FTS_MIN_LENGTH]
            if tokens:
                fts_query = " OR ".join(tokens)
                rows = conn.execute(
                    "SELECT name, file, signature FROM sym_fts "
                    "WHERE sym_fts MATCH ? ORDER BY rank LIMIT ?",
                    (fts_query, limit),
                ).fetchall()
                if rows:
                    fts_names = [r[0] for r in rows]
                    placeholders = ",".join(["?"] * len(fts_names))
                    sym_rows = conn.execute(
                        f"SELECT name,kind,file,line,signature,source,visibility,is_async "
                        f"FROM symbols WHERE name IN ({placeholders})",
                        fts_names,
                    ).fetchall()
                    sym_map = {r[0]: r for r in sym_rows}
                    return [
                        _row_to_dict(sym_map[r[0]]) for r in rows if r[0] in sym_map
                    ], "fts5"
        except Exception:
            pass

        return [], "none"


# ── 辅助函数 ────────────────────────────────────────


def _row_to_dict(r) -> dict:
    d = {
        "name": r[0],
        "kind": r[1],
        "file": r[2],
        "line": r[3],
        "signature": r[4],
        "source": (r[5] or "")[:300],
    }
    if r[6]:
        d["visibility"] = r[6]
    if r[7]:
        d["is_async"] = bool(r[7])
    return d


def _extract_file(filepath: str, suffix: str) -> tuple[list[dict], list[dict]]:
    lang = _LANG_MAP.get(suffix, "")
    if lang == "python":
        return _extract_python(filepath)
    if lang == "cpp":
        # 走专用 C/C++ 抽取器(``_walk_ts`` 不认识 cpp 节点名)
        from . import codegraph_cpp

        try:
            return codegraph_cpp.extract_cpp(filepath)
        except Exception:
            return [], []
    if HAS_TREE_SITTER and lang in ("javascript", "typescript", "go", "rust"):
        try:
            return _extract_ts(filepath, lang)
        except Exception:
            pass
    return [], []


# ── Python AST ──────────────────────────────────────


def _extract_python(filepath: str) -> tuple[list[dict], list[dict]]:
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()
    tree = py_ast.parse(source)
    symbols: list[dict] = []
    edges: list[dict] = []

    class Visitor(py_ast.NodeVisitor):
        def __init__(self):
            self._scope: list[str] = []
            self._current_cls = ""

        def _full_name(self, name: str) -> str:
            return ".".join(self._scope + [name]) if self._scope else name

        def visit_FunctionDef(self, node):
            fn = self._full_name(node.name)
            sig = f"def {node.name}(...)"
            try:
                sig = _py_sig(node)
            except Exception:
                pass
            symbols.append(
                {
                    "name": fn,
                    "kind": "method" if self._current_cls else "function",
                    "line": node.lineno,
                    "signature": sig,
                    "source": _get_src(source, node),
                    "doc": py_ast.get_docstring(node) or "",
                    "visibility": "public"
                    if not node.name.startswith("_")
                    else "private",
                    "is_async": 0,
                }
            )
            edges.extend(_extract_calls(node, source, fn))
            edges.extend(_extract_refs(node, source, fn))
            self._scope.append(node.name)
            self.generic_visit(node)
            self._scope.pop()

        def visit_AsyncFunctionDef(self, node):
            fn = self._full_name(node.name)
            sig = f"async def {node.name}(...)"
            try:
                sig = _py_sig(node)
            except Exception:
                pass
            symbols.append(
                {
                    "name": fn,
                    "kind": "method" if self._current_cls else "function",
                    "line": node.lineno,
                    "signature": sig,
                    "source": _get_src(source, node),
                    "doc": py_ast.get_docstring(node) or "",
                    "visibility": "public"
                    if not node.name.startswith("_")
                    else "private",
                    "is_async": 1,
                }
            )
            edges.extend(_extract_calls(node, source, fn))
            edges.extend(_extract_refs(node, source, fn))
            self._scope.append(node.name)
            self.generic_visit(node)
            self._scope.pop()

        def visit_ClassDef(self, node):
            cls = self._full_name(node.name)
            bases = []
            for b in getattr(node, "bases", []):
                if isinstance(b, py_ast.Name):
                    bases.append(b.id)
                elif isinstance(b, py_ast.Attribute):
                    bases.append(".".join(_unparse_attr(b)))
            symbols.append(
                {
                    "name": cls,
                    "kind": "class",
                    "line": node.lineno,
                    "signature": f"class {node.name}({', '.join(bases)})"
                    if bases
                    else f"class {node.name}",
                    "source": "",
                    "doc": py_ast.get_docstring(node) or "",
                    "visibility": "public"
                    if not node.name.startswith("_")
                    else "private",
                }
            )
            for bname in bases:
                edges.append(
                    {"from": cls, "to": bname, "kind": "extends", "line": node.lineno}
                )
            prev_cls = self._current_cls
            self._current_cls = node.name
            self._scope.append(node.name)
            self.generic_visit(node)
            self._scope.pop()
            self._current_cls = prev_cls

        def visit_Import(self, node):
            scope = self._scope[-1] if self._scope else "(module)"
            for alias in node.names:
                edges.append(
                    {
                        "from": scope,
                        "to": alias.name,
                        "kind": "imports",
                        "line": node.lineno,
                    }
                )

        def visit_ImportFrom(self, node):
            scope = self._scope[-1] if self._scope else "(module)"
            mod = node.module or ""
            for alias in node.names:
                name = f"{mod}.{alias.name}" if mod else alias.name
                edges.append(
                    {"from": scope, "to": name, "kind": "imports", "line": node.lineno}
                )

    Visitor().visit(tree)
    return symbols, edges


def _py_sig(node: py_ast.FunctionDef) -> str:
    args = []
    for a in node.args.args:
        arg = a.arg
        if a.annotation:
            try:
                arg += f": {py_ast.unparse(a.annotation)}"
            except Exception:
                pass
        args.append(arg)
    prefix = "async def" if isinstance(node, py_ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(args)})"


def _get_src(source: str, node) -> str:
    try:
        return (py_ast.get_source_segment(source, node) or "")[:300]
    except Exception:
        return ""


def _extract_calls(node, source: str, caller: str) -> list[dict]:
    edges: list[dict] = []
    unresolved: list[str] = []

    class CallVisitor(py_ast.NodeVisitor):
        def visit_Call(self, node):
            if isinstance(node.func, py_ast.Name):
                edges.append(
                    {
                        "from": caller,
                        "to": node.func.id,
                        "kind": "calls",
                        "line": getattr(node, "lineno", None),
                    }
                )
            elif isinstance(node.func, py_ast.Attribute):
                try:
                    target = ".".join(_unparse_attr(node.func))
                    edges.append(
                        {
                            "from": caller,
                            "to": target,
                            "kind": "calls",
                            "line": getattr(node, "lineno", None),
                        }
                    )
                except Exception:
                    unresolved.append(_get_src(source, node) or "?")
            self.generic_visit(node)

    CallVisitor().visit(node)
    for u in unresolved[:3]:
        edges.append(
            {
                "from": caller,
                "to": f"(unresolved: {u[:60]})",
                "kind": "unresolved",
            }
        )
    return edges


def _extract_refs(node, source: str, caller: str) -> list[dict]:
    """提取属性访问（obj.attr）作为 references 边；装饰器产生 overrides 边。"""
    edges: list[dict] = []

    class RefVisitor(py_ast.NodeVisitor):
        def visit_Attribute(self, node):
            if isinstance(node.value, py_ast.Name) and node.value.id == "self":
                pass
            else:
                parts = _unparse_attr(node)
                if len(parts) >= 2 and parts[-2] != "self":
                    edges.append(
                        {
                            "from": caller,
                            "to": ".".join(parts),
                            "kind": "references",
                            "line": getattr(node, "lineno", None),
                        }
                    )
            self.generic_visit(node)

    RefVisitor().visit(node)

    # 装饰器 → overrides 边
    decos = getattr(node, "decorator_list", [])
    for d in decos:
        if isinstance(d, py_ast.Name):
            edges.append(
                {
                    "from": caller,
                    "to": d.id,
                    "kind": "overrides",
                    "line": getattr(node, "lineno", None),
                }
            )
        elif isinstance(d, py_ast.Attribute):
            edges.append(
                {
                    "from": caller,
                    "to": ".".join(_unparse_attr(d)),
                    "kind": "overrides",
                    "line": getattr(node, "lineno", None),
                }
            )
    return edges


def _unparse_attr(node) -> list[str]:
    if isinstance(node, py_ast.Attribute):
        return _unparse_attr(node.value) + [node.attr]
    if isinstance(node, py_ast.Name):
        return [node.id]
    if isinstance(node, py_ast.Call):
        return _unparse_attr(node.func)
    return ["?"]


# ── reference resolution ───────────────────────────


def _resolve_references(conn):
    """把 edges 中短名字的 to_sym 匹配到 symbols 中的 qualified_name。

    短名字同时对应多个符号时保持原样，避免误消解。
    """
    name_index: dict[str, list[str]] = defaultdict(list)
    rows = conn.execute("SELECT name FROM symbols").fetchall()
    for (qn,) in rows:
        short = qn.rsplit(".", 1)[-1]
        name_index[short].append(qn)

    # lang='c' (C/C++) 边的短名→限定名自动升级被禁用:同短名自由函数太常见,易误联
    edges = conn.execute(
        "SELECT id, to_sym, kind FROM edges "
        "WHERE kind='calls' AND (lang IS NULL OR lang != 'c')"
    ).fetchall()
    updates: list[tuple] = []
    for eid, to_sym, kind in edges:
        if "." not in to_sym and to_sym in name_index:
            candidates = name_index[to_sym]
            if len(candidates) == 1:
                updates.append((candidates[0], eid))
    if updates:
        conn.executemany("UPDATE edges SET to_sym=?, resolved=1 WHERE id=?", updates)


# ── tree-sitter ─────────────────────────────────────

_TS_LANG_PARSERS: dict[str, "tree_sitter.Parser"] = {}


def _get_ts_parser(lang: str):
    if lang in _TS_LANG_PARSERS:
        return _TS_LANG_PARSERS[lang]
    try:
        if lang == "python":
            parser = tree_sitter.Parser(tree_sitter.Language(ts_py.language()))
        elif lang in ("javascript", "typescript"):
            import tree_sitter_javascript as ts_js
            import tree_sitter_typescript as ts_ts

            lang_mod = ts_ts if lang == "typescript" else ts_js
            parser = tree_sitter.Parser(tree_sitter.Language(lang_mod.language()))
        elif lang == "go":
            import tree_sitter_go as ts_go

            parser = tree_sitter.Parser(tree_sitter.Language(ts_go.language()))
        elif lang == "rust":
            import tree_sitter_rust as ts_rust

            parser = tree_sitter.Parser(tree_sitter.Language(ts_rust.language()))
        elif lang == "cpp":
            from . import codegraph_cpp

            parser = codegraph_cpp.get_cpp_parser()
            if parser is None:
                raise ImportError("tree-sitter-cpp not installed")
        else:
            raise ImportError(f"no grammar for {lang}")
        _TS_LANG_PARSERS[lang] = parser
        return parser
    except Exception:
        return None


def _extract_ts(filepath: str, lang: str) -> tuple[list[dict], list[dict]]:
    parser = _get_ts_parser(lang)
    if parser is None:
        return [], []
    with open(filepath, "rb") as f:
        source = f.read()
    tree = parser.parse(source)
    symbols: list[dict] = []
    edges: list[dict] = []
    _walk_ts(tree.root_node, source, symbols, edges)
    return symbols, edges


def _walk_ts(node, source: bytes, symbols: list, edges: list, scope: str = ""):
    def text(n):
        return source[n.start_byte : n.end_byte].decode("utf-8", errors="replace")

    if node.type == "function_declaration":
        name_node = node.child_by_field_name("name")
        name = text(name_node) if name_node else "?"
        fn = f"{scope}.{name}" if scope else name
        symbols.append(
            {
                "name": fn,
                "kind": "function",
                "line": node.start_point[0] + 1,
                "signature": text(node).split("\n")[0][:120],
            }
        )

    elif node.type == "class_declaration":
        name_node = node.child_by_field_name("name")
        name = text(name_node) if name_node else "?"
        cls = f"{scope}.{name}" if scope else name
        symbols.append(
            {
                "name": cls,
                "kind": "class",
                "line": node.start_point[0] + 1,
                "signature": text(node).split("\n")[0][:120],
            }
        )
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                _walk_ts(child, source, symbols, edges, cls)

    elif node.type == "call_expression":
        fn_node = node.child_by_field_name("function")
        if fn_node and scope:
            edges.append(
                {
                    "from": scope,
                    "to": text(fn_node),
                    "kind": "calls",
                    "line": node.start_point[0] + 1,
                }
            )

    for child in node.children:
        _walk_ts(child, source, symbols, edges, scope)


# ── BFS 路径查找 ──────────────────────────────────


def _bfs_path(conn, start: str, end: str, max_depth: int = 6) -> list[str] | None:
    """在调用图上 BFS 找最短调用链。"""
    q = deque()
    q.append((start, [start], {start}))
    while q:
        node, path, visited = q.popleft()
        if len(path) > max_depth:
            continue
        rows = conn.execute(
            "SELECT to_sym FROM edges WHERE from_sym=? AND kind IN ('calls','extends')",
            (node,),
        ).fetchall()
        for (nxt,) in rows:
            if nxt == end:
                return path + [nxt]
            if nxt not in visited:
                visited.add(nxt)
                q.append((nxt, path + [nxt], visited))
    return None


def _route_query(query: str) -> tuple[str, re.Match | None]:
    """把自然语言查询路由到具体的查询类型。"""
    for pattern, qtype in _QUERY_ROUTES:
        m = pattern.search(query)
        if m:
            return qtype, m
    return "explore", None
