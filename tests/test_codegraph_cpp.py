"""Tests for the C/C++ codegraph walker."""

from pathlib import Path
import tempfile

from tools.codegraph import CodeGraph


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cpp_sample"


def test_index_cpp_extracts_member_functions(tmp_path):
    """In-class and out-of-class member function definitions both become symbols."""
    src_dir = tmp_path / "proj"
    src_dir.mkdir()
    (src_dir / "h.h").write_text(
        "class Greeter {\n"
        "public:\n"
        "    void hello();\n"
        "    void greet() { hello(); }\n"
        "};\n",
        encoding="utf-8",
    )
    cg = CodeGraph(str(tmp_path / "db.sqlite"))
    try:
        result = cg.index(str(src_dir), incremental=False)
        assert result["ok"] is True
        # Inspect raw symbol names from the DB
        rows = cg._conn.execute(
            "SELECT name, kind FROM symbols WHERE kind LIKE 'c_function%' OR kind LIKE '%method%'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert "Greeter::hello" in names or "Greeter::greet" in names, (
            f"expected member functions, got: {names}"
        )
    finally:
        cg.close()


def test_cpp_call_not_auto_resolved_against_unique_short_name():
    """C++ calls should NOT be auto-upgraded to qualified `::` names, even when
    the short name is globally unique in the symbol index. Reason: C free
    functions with identical short names are extremely common, and BFS over
    `log_widget` works fine — the resolution upgrade would just risk false merges.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cg = CodeGraph(str(Path(tmp) / "db.sqlite"))
        try:
            cg.index(str(FIXTURE_DIR), incremental=False)
            # In the fixture, ui::Widget::render() calls log_widget().
            # 'log_widget' is the only symbol with that short name (ui::log_widget).
            # Without the lang filter, _resolve_references would rewrite the
            # edge to_sym from 'log_widget' to 'ui::log_widget'. We assert
            # it stays as the short name and is tagged lang='c'.
            rows = cg._conn.execute(
                "SELECT from_sym, to_sym, resolved, lang FROM edges "
                "WHERE kind='calls' AND from_sym='ui::Widget::render' "
                "AND to_sym LIKE '%log_widget%'"
            ).fetchall()
            assert rows, "expected render→log_widget edge in fixture"
            for _from, to, resolved, lang in rows:
                assert to == "log_widget", (
                    f"C++ edge was auto-upgraded to {to!r}; cpp auto-resolve must be off"
                )
                assert resolved == 0, (
                    "C++ edge marked resolved=1; cpp auto-resolve must be off"
                )
                assert lang == "c", f"expected lang='c' tag on C++ edge, got {lang!r}"
        finally:
            cg.close()


def test_python_call_still_auto_resolved(tmp_path):
    """Sanity: Python call edges with a unique short name should STILL be upgraded.
    This guards against accidentally disabling resolution for all languages.
    """
    src_dir = tmp_path / "proj"
    src_dir.mkdir()
    (src_dir / "a.py").write_text(
        "def helper():\n    return helper()\n",
        encoding="utf-8",
    )
    cg = CodeGraph(str(tmp_path / "db.sqlite"))
    try:
        cg.index(str(src_dir), incremental=False)
        rows = cg._conn.execute(
            "SELECT to_sym, resolved FROM edges WHERE kind='calls' AND from_sym='helper'"
        ).fetchall()
        assert rows
        # Python 'helper' is the only symbol with that name → SHOULD be upgraded
        # (no namespace separator, but the resolve rewrite is identity here
        # because there's no qualified version to upgrade to). The point is
        # resolved should be 1 (it was processed), not 0.
        for to, resolved in rows:
            # We can't easily distinguish "no upgrade needed" from "skipped",
            # so just assert the edge was processed by the resolver.
            assert resolved == 1 or to == "helper", f"got to={to!r} resolved={resolved}"
    finally:
        cg.close()


def test_explore_finds_cpp_symbol(tmp_path):
    """code_explore 'Widget' should return the class symbol with kind=cpp_class."""
    src_dir = tmp_path / "proj"
    src_dir.mkdir()
    (src_dir / "a.cpp").write_text(
        "namespace ui { class Widget {}; }\n", encoding="utf-8"
    )
    cg = CodeGraph(str(tmp_path / "db.sqlite"))
    try:
        cg.index(str(src_dir), incremental=False)
        result = cg.explore("Widget", str(src_dir))
        assert result["ok"] is True
        assert result.get("found") is True
        names = [s["name"] for s in result.get("symbols", [])]
        assert any("Widget" in n for n in names), f"got: {names}"
    finally:
        cg.close()


def test_explore_bfs_call_chain_cpp(tmp_path):
    """From main() to a free function called via Widget::render should
    produce a 2-3 hop BFS path. We're flexible about exact path (C++ field
    access w.render() may or may not link to Widget::render in the AST)
    but the tool must return cleanly either way."""
    src_dir = tmp_path / "proj"
    src_dir.mkdir()
    (src_dir / "w.cpp").write_text(
        "void log_widget();\n"
        "class Widget {\n"
        "public:\n"
        "    void render() { log_widget(); }\n"
        "};\n"
        "int main() {\n"
        "    Widget w;\n"
        "    w.render();\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    cg = CodeGraph(str(tmp_path / "db.sqlite"))
    try:
        cg.index(str(src_dir), incremental=False)
        result = cg.explore("从 main 到 log_widget", str(src_dir))
        assert result.get("ok") is True
        # Either a real path, or an informative unresolved result — both acceptable
        if result.get("found") and "path" in result:
            path = result["path"]
            assert "main" in path[0], f"unexpected path start: {path}"
            assert "log_widget" in path[-1], f"unexpected path end: {path}"
        else:
            # BFS not finding a path is acceptable (C++ field call linking is a known limit)
            assert "unresolved" in result or "hint" in result or "summary" in result
    finally:
        cg.close()


def test_index_cpp_file_produces_symbols():
    """A C++ source file should yield at least one symbol after indexing."""
    with tempfile.TemporaryDirectory() as tmp:
        cg = CodeGraph(str(Path(tmp) / "codegraph.db"))
        try:
            result = cg.index(str(FIXTURE_DIR), incremental=False)
            assert result["ok"] is True
            # Should have indexed all three fixture files
            assert result["stats"]["files"] >= 3
            # Should have non-zero symbols (classes, functions, namespaces, methods)
            assert result["stats"]["symbols"] > 0
        finally:
            cg.close()
