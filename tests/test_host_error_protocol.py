"""Host-free checks of real driver error frames and bridge session reuse."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

import dcc_mcp_paraview
from dcc_mcp_paraview import bridge, contracts
from dcc_mcp_paraview.contracts import OperationError


@pytest.mark.parametrize(
    "error_kind,expected_code",
    [
        ("xml_parse", "host_error"),
        (None, "host_error"),
        ("", "host_error"),
        ("x" * 101, "host_error"),
        (False, "host_error"),
        ("missing", "host_error"),
        ("x", "x"),
        ("x" * 100, "x" * 100),
        ("verification_failed", "verification_failed"),
    ],
)
def test_error_frame_preserves_pipeline_and_next_request(tmp_path, monkeypatch, error_kind, expected_code):
    monkeypatch.setitem(sys.modules, "contracts", contracts)
    path = Path(dcc_mcp_paraview.__file__).with_name("host_driver.py")
    spec = importlib.util.spec_from_file_location("paraview_host_error_tests", path)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)

    error = RuntimeError("recoverable host failure")
    if error_kind == "xml_parse":
        with pytest.raises(ElementTree.ParseError) as parsed:
            ElementTree.fromstring("<broken>")
        error = parsed.value
        assert isinstance(error.code, int)
    elif error_kind != "missing":
        error.code = error_kind

    class ControlledHost:
        version = "5.13.2"

        def __init__(self, root):
            self.pipeline = {"sources": ["RetainedSphere"]}
            self.requests = 0

        def call(self, operation, params):
            assert operation == "inspect_pipeline" and params == {}
            self.requests += 1
            if self.requests == 1:
                raise error
            return self.pipeline

    requests = [
        {"id": digit * 32, "job_id": None, "operation": "inspect_pipeline", "params": {}} for digit in ("a", "b")
    ]
    incoming = io.BytesIO(b"".join((json.dumps(row) + "\n").encode() for row in requests))
    wire = io.StringIO()
    # Run the actual main loop without native imports, real stdout changes or a host process.
    with monkeypatch.context() as driver_patch:
        driver_patch.setattr(driver, "Host", ControlledHost)
        driver_patch.setattr(driver.sys, "argv", [str(path), str(tmp_path)])
        driver_patch.setattr(driver.sys, "stdout", sys.stdout)
        driver_patch.setattr(driver.os, "dup", lambda descriptor: descriptor)
        driver_patch.setattr(driver.os, "fdopen", lambda descriptor, mode, **kwargs: wire if mode == "w" else incoming)
        driver.main()

    lines = wire.getvalue().splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["ready"] is True
    frames = iter(lines[1:])
    session = bridge.ParaViewSession(tmp_path, executable=sys.executable)
    identities = iter(row["id"] for row in requests)
    monkeypatch.setattr(bridge.uuid, "uuid4", lambda: SimpleNamespace(hex=next(identities)))
    sent = []
    monkeypatch.setattr(session, "start", lambda: None)
    monkeypatch.setattr(session, "_write_frame", lambda frame, deadline: sent.append(json.loads(frame)))
    monkeypatch.setattr(session, "_read_frame", lambda deadline: bridge._strict_json(next(frames)))
    try:
        with pytest.raises(OperationError) as received:
            session.request("inspect_pipeline", {})
        assert received.value.code == expected_code
        assert not session._closed
        assert session.request("inspect_pipeline", {}) == {"sources": ["RetainedSphere"]}
        assert not session._closed and sent == requests
    finally:
        session.close()
