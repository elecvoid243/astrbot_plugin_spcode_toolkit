"""C/C++ tree-sitter parser loader and AST helpers for codegraph.

The tree-sitter-cpp grammar handles both C and C++ source. Pure C code
parses correctly in the vast majority of cases. If a project surfaces
a grammar conflict, swap in a dedicated tree-sitter-c here.

Known limitations (see README for details):
- Macros: preprocessor expansion is invisible to the AST
- Templates: instantiation calls are not in the AST
- Operator overloading: parsed as binary_expression, not a call
- Overloads: name field is un-mangled; collision is possible
- #ifdef dead code: indexed as if it would compile
"""

from __future__ import annotations

import re

try:
    import tree_sitter
    import tree_sitter_cpp as ts_cpp

    HAS_CPP = True
except ImportError:
    HAS_CPP = False


_CPP_PARSER: "tree_sitter.Parser | None" = None


def get_cpp_parser():
    """Return a cached tree-sitter Parser for C/C++, or None if unavailable."""
    global _CPP_PARSER
    if not HAS_CPP:
        return None
    if _CPP_PARSER is None:
        _CPP_PARSER = tree_sitter.Parser(tree_sitter.Language(ts_cpp.language()))
    return _CPP_PARSER


def extract_cpp(filepath: str) -> tuple[list[dict], list[dict]]:
    """Extract symbols and edges from a C or C++ source file.

    返回 (symbols, edges) —— 与 Python 抽取器相同的字段约定。
    """
    parser = get_cpp_parser()
    if parser is None:
        return [], []
    with open(filepath, "rb") as f:
        source = f.read()
    tree = parser.parse(source)
    symbols: list[dict] = []
    edges: list[dict] = []
    walk_cpp(tree.root_node, source, symbols, edges, scope="")
    return symbols, edges


def _text(node, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def walk_cpp(node, source: bytes, symbols: list, edges: list, scope: str):
    """递归遍历 C/C++ AST,``scope`` 用 ``::`` 分隔命名空间/类限定。"""
    t = node.type

    if t == "function_definition":
        name = _extract_function_name(node, source)
        if name and name != "?":
            qualified = f"{scope}::{name}" if scope else name
            symbols.append(
                {
                    "name": qualified,
                    "kind": "c_function" if not scope else "function",
                    "line": node.start_point[0] + 1,
                    "signature": _text(node, source).split("\n")[0][:120],
                }
            )
            body = node.child_by_field_name("body")
            if body:
                _extract_calls_in_body(body, source, qualified, edges)
            return  # 已抽到函数体内部调用,不再按子节点重复发现

    elif t in ("class_specifier", "struct_specifier"):
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node else "?"
        qualified = f"{scope}::{name}" if scope else name
        symbols.append(
            {
                "name": qualified,
                "kind": "cpp_class" if t == "class_specifier" else "cpp_struct",
                "line": node.start_point[0] + 1,
                "signature": _text(node, source).split("\n")[0][:120],
            }
        )
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                walk_cpp(child, source, symbols, edges, scope=qualified)
        return

    elif t == "namespace_definition":
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source) if name_node else ""
        new_scope = f"{scope}::{name}" if scope else name
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                walk_cpp(child, source, symbols, edges, scope=new_scope)
        return

    elif t == "field_declaration":
        # 类内成员函数声明 (``void foo();`` 这种无实现的);定义走上面 function_definition 分支
        for child in node.children:
            if child.type == "function_declarator":
                declarator = child.child_by_field_name("declarator")
                if declarator is not None and declarator.type == "field_identifier":
                    fname = _text(declarator, source)
                    qualified = f"{scope}::{fname}" if scope else fname
                    symbols.append(
                        {
                            "name": qualified,
                            "kind": "c_method",
                            "line": node.start_point[0] + 1,
                            "signature": _text(node, source).split("\n")[0][:120],
                        }
                    )
                break  # 一个 field_declaration 至多一个函数声明

    elif t == "preproc_include":
        path_node = node.child_by_field_name("path")
        if path_node:
            edges.append(
                {
                    "from": scope or "(file)",
                    "to": _text(path_node, source),
                    "kind": "imports",
                    "line": node.start_point[0] + 1,
                }
            )

    elif t == "template_declaration":
        # 模板只是外壳,内部才是真实定义
        for child in node.children:
            walk_cpp(child, source, symbols, edges, scope=scope)
        return

    for child in node.children:
        walk_cpp(child, source, symbols, edges, scope=scope)


def _extract_function_name(node, source: bytes) -> str:
    """从 function_definition 节点中拆出函数名。"""
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return "?"
    cur = declarator
    # 穿过指针/引用/括号修饰,直到 function_declarator
    while cur is not None and cur.type in (
        "pointer_declarator",
        "reference_declarator",
        "parenthesized_declarator",
    ):
        cur = cur.child_by_field_name("declarator")
    if cur is None:
        text = _text(declarator, source)
        m = re.search(r"([A-Za-z_]\w*)\s*$", text)
        return m.group(1) if m else "?"
    if cur.type == "function_declarator":
        inner = cur.child_by_field_name("declarator")
        if inner is None:
            return "?"
        return _text(inner, source)
    return _text(cur, source)


def _extract_calls_in_body(body_node, source: bytes, caller: str, edges: list):
    """在函数体里做迭代式 DFS,遇到 call_expression 就记录一条边。"""
    stack = [body_node]
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            fn_node = n.child_by_field_name("function")
            if fn_node is not None:
                edges.append(
                    {
                        "from": caller,
                        "to": _text(fn_node, source),
                        "kind": "calls",
                        "line": n.start_point[0] + 1,
                        "lang": "c",  # 标记为 C/C++ 边,_resolve_references 会跳过
                    }
                )
        stack.extend(n.children)
