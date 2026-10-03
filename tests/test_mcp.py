"""Actual Core HTTP endpoint and pinned official MCP client; no mocked transport."""

import asyncio
import json
import os
import shutil
import socket
import time
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlparse

import httpx
import jsonschema
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import dcc_mcp_paraview
from dcc_mcp_paraview.server import ParaViewMcpServer

pytestmark = [
    pytest.mark.mcp,
    pytest.mark.paraview,
    pytest.mark.skipif(not shutil.which("pvpython"), reason="pvpython not installed"),
]


def payload(result):
    assert not result.isError, result
    return result.structuredContent or json.loads(next(block.text for block in result.content if block.type == "text"))


async def call(client, name, arguments):
    result = payload(await client.call_tool(name, arguments))
    manifest = Path(dcc_mcp_paraview.__file__).parent / "skills/paraview-pipeline/tools.yaml"
    declaration = next(t for t in json.loads(manifest.read_text())["tools"] if t["name"] == name)
    jsonschema.validate(result, declaration["output_schema"])
    if declaration["execution"] == "async":
        assert isinstance(result.get("job_id"), str) and result["job_id"], result
        assert result["status"] in {"pending", "running", "completed", "failed", "cancelled", "interrupted"}, result
    if "job_id" not in result:
        return result
    job_id = result["job_id"]
    deadline = time.monotonic() + 70
    while time.monotonic() < deadline:
        job = payload(await client.call_tool("jobs_get_status", {"job_id": job_id}))
        assert job["job_id"] == job_id
        if job["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            assert "result" in job, job
            terminal = job["result"]
            assert isinstance(terminal["success"], bool)
            assert isinstance(terminal["context"], dict)
            assert terminal.get("error") is None or isinstance(terminal["error"], str)
            return terminal
        await asyncio.sleep(0.05)
    raise AssertionError("Core job did not reach terminal state")


def assert_loopback_listener(port):
    # Linux socket inventory verifies actual bound addresses, not construction options.
    addresses = []
    for file in ("/proc/net/tcp", "/proc/net/tcp6"):
        for row in Path(file).read_text().splitlines()[1:]:
            fields = row.split()
            address, port_hex = fields[1].split(":")
            if fields[3] == "0A" and int(port_hex, 16) == port:
                addresses.append(address)
    assert addresses == ["0100007F"], addresses
    return "127.0.0.1"


def test_real_mcp_discover_load_call_roundtrip_and_cleanup(tmp_path):
    registry = tmp_path / "registry"
    server = ParaViewMcpServer(
        workspace=tmp_path,
        registry_dir=str(registry),
        enable_gateway_failover=False,
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_telemetry=False,
        enable_checkpoint_persistence=False,
    )
    server.start()
    port = urlparse(server.mcp_url).port
    bind = assert_loopback_listener(port)
    host = server.host_session.process
    instance_id = server.instance_id
    evidence = {
        "core_version": version("dcc-mcp-core"),
        "server_version": version("dcc-mcp-server"),
        "sdk_version": version("mcp"),
        "listener": bind,
        "operations": [],
    }

    async def workflow():
        async with httpx.AsyncClient(trust_env=False) as http:
            async with streamable_http_client(server.mcp_url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    evidence["protocol_version"] = initialized.protocolVersion
                    found = payload(await client.call_tool("search_skills", {"query": "paraview"}))
                    assert any(s["name"] == "paraview-pipeline" for s in found["skills"])
                    loaded = payload(await client.call_tool("load_skill", {"skill_name": "paraview-pipeline"}))
                    assert loaded["loaded"]
                    names = set()
                    schemas = {}
                    cursor = None
                    while True:
                        page = await client.list_tools(cursor=cursor)
                        names.update(t.name for t in page.tools)
                        schemas.update({t.name: t for t in page.tools})
                        cursor = page.nextCursor
                        if cursor is None:
                            break
                    assert {"create_sphere", "inspect_pipeline", "contour", "get_adapter_info"} <= names
                    for tool in schemas.values():
                        if tool.name in {"create_sphere", "inspect_pipeline", "render_preview"}:
                            assert tool.inputSchema["additionalProperties"] is False
                            assert tool.outputSchema is not None
                    info = await call(client, "get_adapter_info", {})
                    assert info["context"]["host_imported"] is False
                    before = await call(client, "inspect_pipeline", {})
                    assert before["context"]["sources"] == []
                    native = before["context"]["host"]
                    assert native["thread_id"] == native["main_thread_id"]
                    assert native["pid"] != os.getpid()
                    evidence["host_version"] = native["version"]
                    operations = [
                        ("create_sphere", {"name": "Sphere"}),
                        ("edit_sphere", {"name": "Sphere", "radius": 1.25}),
                        ("clip_plane", {"name": "Clip", "input_name": "Sphere", "origin": [-0.2, 0, 0]}),
                        ("export_dataset", {"name": "Clip", "path": "mcp-clip.vtu"}),
                        ("open_dataset", {"name": "Imported", "path": "mcp-clip.vtu"}),
                        ("save_state", {"path": "mcp-scene.pvsm"}),
                        ("edit_sphere", {"name": "Sphere", "radius": 2}),
                        ("reopen_state", {"path": "mcp-scene.pvsm"}),
                    ]
                    for name, arguments in operations:
                        result = await call(client, name, arguments)
                        assert result["success"], result
                        assert result["postcondition"]["verified"] is True
                        evidence["operations"].append(name)
                    after = await call(client, "inspect_pipeline", {})
                    sphere = next(s for s in after["context"]["sources"] if s["name"] == "Sphere")
                    assert sphere["radius"] == 1.25
                    bad = await call(client, "edit_sphere", {"name": "Missing", "radius": 1})
                    assert bad["success"] is False and bad["error"] == "source_not_found"
                    missing_render = await call(client, "render_preview", {"name": "Clip", "path": "preview.png"})
                    assert missing_render["success"] is False and missing_render["error"] == "render_unavailable"
                    assert not (tmp_path / "preview.png").exists()
                    assert server.host_session.last_request["job_id"] is not None
                    assert server.ipc_dispatcher._thread.ident != __import__("threading").get_ident()
                    unloaded = payload(await client.call_tool("unload_skill", {"skill_name": "paraview-pipeline"}))
                    assert unloaded, unloaded

    try:
        asyncio.run(workflow())
    finally:
        server.stop()
    assert host.poll() is not None
    assert not server.ipc_dispatcher._thread.is_alive()
    assert not server.is_running
    with socket.socket() as connection:
        assert connection.connect_ex(("127.0.0.1", port)) != 0
    for record in registry.rglob("*.json"):
        assert instance_id not in record.read_text(), record
    evidence["cleanup"] = {"host_exited": True, "listener_closed": True, "registry_removed": True}
    if os.environ.get("PARAVIEW_MCP_EVIDENCE"):
        Path(os.environ["PARAVIEW_MCP_EVIDENCE"]).write_text(json.dumps(evidence, indent=2) + "\n")
