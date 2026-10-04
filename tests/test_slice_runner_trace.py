"""Host-free checks of the opt-in graphical runner's trace and independent oracle."""

import asyncio
import copy
import io
import json

import pytest
import test_slice_render_mcp as runner
from mcp import ClientSession, types
from test_slice_render_mcp import TracedClient, assert_analytic_geometry


def test_trace_preserves_complete_sdk_request_and_result(monkeypatch):
    response = types.CallToolResult(
        content=[types.TextContent(type="text", text='{"job_id":"j","status":"running"}')],
        structuredContent={"job_id": "j", "status": "running", "context": {"arbitrary": [1, 2, 3]}},
        isError=False,
    )

    async def send(self, request, result_type, *args, **kwargs):
        return response

    monkeypatch.setattr(ClientSession, "send_request", send)
    stream = io.StringIO()
    client = TracedClient(None, None, stream)
    request = types.ClientRequest(
        types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name="capture_current_view", arguments={"path": "x.png"}),
        )
    )
    assert asyncio.run(client.send_request(request, types.CallToolResult)) is response
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert events[0]["request"] == request.model_dump(mode="json", by_alias=True)
    assert events[1]["result"] == response.model_dump(mode="json", by_alias=True)
    assert events[0]["sequence"] == events[1]["sequence"]
    assert client.operation_names() == ["capture_current_view"]


def test_trace_flushes_failed_request_and_original_job_polls(monkeypatch):
    async def fail(self, request, result_type, *args, **kwargs):
        raise RuntimeError("transport interrupted")

    monkeypatch.setattr(ClientSession, "send_request", fail)
    stream = io.StringIO()
    client = TracedClient(None, None, stream)
    request = types.ClientRequest(
        types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name="jobs_get_status", arguments={"job_id": "original"}),
        )
    )
    with pytest.raises(RuntimeError, match="transport interrupted"):
        asyncio.run(client.send_request(request, types.CallToolResult))
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert events[0]["request"]["params"]["arguments"] == {"job_id": "original"}
    assert events[1]["event"] == "exception" and events[1]["type"] == "RuntimeError"
    assert client.operation_names() == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("points", 50),
        ("cells", 73),
        ("bounds", [0, 0, -2, 3, -3, 3]),
        ("point_arrays", [{"name": "RadiusSquared", "components": 1, "range": [0, 27]}]),
    ],
)
def test_independent_analytic_oracle_rejects_wrong_sections(field, value):
    volume = {
        "points": 343,
        "cells": 216,
        "bounds": [-3, 3, -3, 3, -3, 3],
        "point_arrays": [{"name": "RadiusSquared", "components": 1, "range": [0, 27]}],
    }
    section = {
        "points": 49,
        "cells": 72,
        "bounds": [0, 0, -3, 3, -3, 3],
        "point_arrays": [{"name": "RadiusSquared", "components": 1, "range": [0, 18]}],
    }
    assert_analytic_geometry(volume, section)
    wrong = copy.deepcopy(section)
    wrong[field] = value
    with pytest.raises(AssertionError):
        assert_analytic_geometry(volume, wrong)


@pytest.mark.parametrize("operation", ["inspect_pipeline", "slice_plane"])
def test_runner_respects_existing_inspection_envelope(monkeypatch, operation):
    async def result(client, name, arguments):
        return {"success": True, "context": {"sources": []}}

    monkeypatch.setattr(runner, "call", result)
    if operation == "inspect_pipeline":
        assert asyncio.run(runner.verified_call(None, operation, {})) == {"sources": []}
    else:
        with pytest.raises(KeyError, match="postcondition"):
            asyncio.run(runner.verified_call(None, operation, {}))
