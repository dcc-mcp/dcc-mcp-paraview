"""Failure cleanup using real Core and an owned protocol fixture host."""

import sys
from types import SimpleNamespace

import pytest
from dcc_mcp_core import DccServerBase
from test_bridge import scripted_host

from dcc_mcp_paraview import compatibility
from dcc_mcp_paraview import server as server_module
from dcc_mcp_paraview.contracts import OperationError
from dcc_mcp_paraview.server import ParaViewMcpServer


@pytest.mark.parametrize("old_distribution", ["dcc-mcp-core", "dcc-mcp-server"])
def test_core_version_gate_precedes_host_admission(tmp_path, monkeypatch, old_distribution):
    monkeypatch.setattr(compatibility, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(
        compatibility, "version", lambda distribution: "0.20.39" if distribution == old_distribution else "0.20.41"
    )
    calls = []

    def unexpected(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Runtime rejection must precede host admission and Core transport")

    monkeypatch.setattr(server_module, "ParaViewSession", unexpected)
    monkeypatch.setattr(DccServerBase, "__init__", unexpected)
    with pytest.raises(OperationError) as error:
        ParaViewMcpServer(workspace=tmp_path, executable="never-launched")
    assert error.value.code == "unsupported_runtime"
    assert old_distribution in str(error.value)
    assert calls == []


def test_platform_gate_precedes_version_host_and_listener(tmp_path, monkeypatch):
    monkeypatch.setattr(compatibility, "sys", SimpleNamespace(platform="win32"))
    calls = []

    def unexpected(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Platform rejection must precede versions, host admission and Core transport")

    monkeypatch.setattr(compatibility, "version", unexpected)
    monkeypatch.setattr(server_module, "ParaViewSession", unexpected)
    monkeypatch.setattr(DccServerBase, "__init__", unexpected)
    monkeypatch.setattr(DccServerBase, "start", unexpected)
    with pytest.raises(OperationError) as error:
        ParaViewMcpServer(workspace=tmp_path, executable="never-launched")
    assert error.value.code == "unsupported_platform"
    assert calls == []


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only POSIX executable protocol fixture")
def test_constructor_failure_closes_supplied_session(tmp_path, monkeypatch):
    session = scripted_host(tmp_path, "pass")
    session.start()

    def fail(self):
        raise RuntimeError("registration failed")

    monkeypatch.setattr(ParaViewMcpServer, "register_builtin_actions", fail)
    with pytest.raises(RuntimeError, match="registration failed"):
        ParaViewMcpServer(session=session, enable_file_logging=False)
    assert session.process.poll() is not None
    assert session.stderr.closed


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only adapter runtime and POSIX protocol fixture")
def test_transport_start_failure_stops_host_and_pump(tmp_path, monkeypatch):
    session = scripted_host(tmp_path, "pass")
    server = ParaViewMcpServer(session=session, enable_file_logging=False, enable_gateway_failover=False)

    def fail(self, **kwargs):
        raise RuntimeError("transport failed")

    monkeypatch.setattr(DccServerBase, "start", fail)
    with pytest.raises(RuntimeError, match="transport failed"):
        server.start()
    assert session.process.poll() is not None
    assert not server.ipc_dispatcher._thread.is_alive()
    server.stop()
    with pytest.raises(OperationError) as error:
        server.start()
    assert error.value.code == "session_closed"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only adapter runtime and POSIX protocol fixture")
def test_new_server_instance_can_restart_after_orderly_stop(tmp_path):
    for number in range(2):
        root = tmp_path / str(number)
        root.mkdir()
        session = scripted_host(root, "print(json.dumps({'id':r['id'],'job_id':r['job_id'],'result':{}}),flush=True)")
        server = ParaViewMcpServer(
            session=session,
            registry_dir=str(root / "registry"),
            enable_file_logging=False,
            enable_gateway_failover=False,
            enable_job_persistence=False,
            enable_telemetry=False,
            enable_checkpoint_persistence=False,
        )
        try:
            server.start()
            assert server.is_running
        finally:
            server.stop()
        assert not server.is_running
        assert session.process.poll() is not None
        assert not server.ipc_dispatcher._thread.is_alive()
