def test_external_tool_defaults_use_astrbot_root(monkeypatch, tmp_path):
    from tools import _external_tools

    monkeypatch.setattr(_external_tools, "get_astrbot_root", lambda: tmp_path)

    defaults = _external_tools.external_tool_defaults()

    assert defaults == {
        "es_path": str(tmp_path / "external_tools" / "Everything" / "es.exe"),
        "codegraph_install_dir": str(
            tmp_path / "external_tools" / "codegraph-win32-x64"
        ),
        "cppcheck_path": str(tmp_path / "external_tools" / "cppcheck" / "cppcheck.exe"),
    }


def test_external_tool_defaults_do_not_override_explicit_config(monkeypatch, tmp_path):
    from tools import _external_tools

    monkeypatch.setattr(_external_tools, "get_astrbot_root", lambda: tmp_path)
    explicit = {
        "es_path": "D:/custom/es.exe",
        "codegraph_install_dir": "D:/custom/codegraph",
        "cppcheck_path": "D:/custom/cppcheck.exe",
    }

    merged = _external_tools.merge_external_tool_defaults(explicit)

    assert merged == explicit
