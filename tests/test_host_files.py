"""Host-free publication/path invariants; Host initialization is intentionally not called."""

import importlib.util
import sys
from pathlib import Path

import pytest

import dcc_mcp_paraview
from dcc_mcp_paraview import contracts
from dcc_mcp_paraview.contracts import OperationError


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "contracts", contracts)
    path = Path(dcc_mcp_paraview.__file__).with_name("host_driver.py")
    spec = importlib.util.spec_from_file_location("paraview_host_file_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.Host.__new__(module.Host)
    result.root = tmp_path
    return result


def test_concurrent_destination_creation_is_preserved_and_stage_removed(host, tmp_path):
    output = host.path("result.pvsm", ".pvsm", output=True)

    def writer(staged):
        Path(staged).write_text("new data")

    def verifier(staged):
        output.write_text("concurrent winner")
        return {}

    with pytest.raises(OperationError) as error:
        host.publish(output, writer, verifier)
    assert error.value.code == "output_exists"
    assert output.read_text() == "concurrent winner"
    assert list(tmp_path.glob(".result.*.pvsm")) == []


def test_verification_failure_never_publishes_and_cleans_stage(host, tmp_path):
    output = host.path("result.pvsm", ".pvsm", output=True)

    def verifier(staged):
        raise OperationError("verification_failed", "controlled failure")

    with pytest.raises(OperationError):
        host.publish(output, lambda path: Path(path).write_text("invalid"), verifier)
    assert not output.exists()
    assert list(tmp_path.glob(".result.*.pvsm")) == []


def test_oversized_input_fails_before_parser(host, tmp_path):
    file = tmp_path / "large.vti"
    with file.open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    with pytest.raises(OperationError) as error:
        host.path("large.vti", ".vti")
    assert error.value.code == "invalid_input"
