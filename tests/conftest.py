import os

import pytest


@pytest.fixture(autouse=True)
def isolate_core_dirs(tmp_path, monkeypatch, request):
    for key, suffix in (
        ("HOME", "home"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
    ):
        path = tmp_path / suffix
        path.mkdir()
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv("DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS", "1")
    monkeypatch.setenv("RUST_LOG", "error")
    if request.node.get_closest_marker("render") is None:
        monkeypatch.delenv("DISPLAY", raising=False)


def pytest_configure(config):
    os.environ.setdefault("RUST_LOG", "error")
