"""Pytest config — skip entire module if tree-sitter-cpp unavailable."""

import pytest

try:
    import tree_sitter  # noqa: F401
    import tree_sitter_cpp  # noqa: F401

    _HAS_CPP = True
except ImportError:
    _HAS_CPP = False


def pytest_collection_modifyitems(config, items):
    if _HAS_CPP:
        return
    skip = pytest.mark.skip(reason="tree-sitter-cpp not installed")
    for item in items:
        if "test_codegraph_cpp" in item.nodeid:
            item.add_marker(skip)
