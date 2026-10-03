"""Official SDK/Core cancellation proof with controlled owned host, no vendor dependency."""

import asyncio
import sys
import time

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from test_bridge import scripted_host
from test_mcp import payload

from dcc_mcp_paraview.server import ParaViewMcpServer

pytestmark = pytest.mark.mcp


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only adapter runtime and POSIX protocol fixture")
@pytest.mark.parametrize("cancel", [False, True])
def test_core_http_timeout_or_cancel_prevents_late_host_mutation(tmp_path, cancel):
    marker = tmp_path / "late-native-mutation"
    started = tmp_path / "native-started"
    body = (
        f"pathlib.Path({str(started)!r}).write_text('started')\n"
        "time.sleep(1)\n"
        f"pathlib.Path({str(marker)!r}).write_text('mutated')\n"
        "print(json.dumps({'id':r['id'],'job_id':r['job_id'],'result':{}}),flush=True)"
    )
    session = scripted_host(tmp_path, body)
    session.timeout = 5 if cancel else 0.2
    server = ParaViewMcpServer(
        session=session,
        registry_dir=str(tmp_path / "registry"),
        enable_file_logging=False,
        enable_gateway_failover=False,
        enable_job_persistence=False,
        enable_telemetry=False,
        enable_checkpoint_persistence=False,
    )

    async def workflow():
        async with httpx.AsyncClient(trust_env=False) as http:
            async with streamable_http_client(server.mcp_url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    assert payload(await client.call_tool("load_skill", {"skill_name": "paraview-pipeline"}))["loaded"]
                    cursor = None
                    while True:
                        page = await client.list_tools(cursor=cursor)
                        cursor = page.nextCursor
                        if cursor is None:
                            break
                    launch = payload(await client.call_tool("create_sphere", {"name": "Deferred"}))
                    job_id = launch["job_id"]
                    assert isinstance(job_id, str) and job_id
                    deadline = time.monotonic() + 5
                    while not started.exists() and time.monotonic() < deadline:
                        await asyncio.sleep(0.02)
                    assert started.exists()
                    assert session.last_request["job_id"] == job_id
                    if cancel:
                        # Core owns async cancellation on its documented REST route.
                        cancelled = await http.delete(server.mcp_url.rsplit("/mcp", 1)[0] + "/v1/jobs/" + job_id)
                        assert cancelled.is_success, cancelled.text
                    while time.monotonic() < deadline:
                        state = payload(await client.call_tool("jobs_get_status", {"job_id": job_id}))
                        assert state["job_id"] == job_id
                        if state["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                            if cancel:
                                assert state["status"] in {"cancelled", "interrupted"}, state
                            else:
                                assert state["result"]["error"] == "host_timeout", state
                            break
                        await asyncio.sleep(0.02)
                    else:
                        raise AssertionError("Core job did not terminate")
                    # Terminal cancellation can be recorded before the native owner finishes cleanup.
                    while session.process.poll() is None and time.monotonic() < deadline:
                        await asyncio.sleep(0.02)
                    assert session.process.poll() is not None
                    await asyncio.sleep(1.1)
                    assert not marker.exists()

    try:
        server.start()
        asyncio.run(workflow())
    finally:
        server.stop()
    assert not server.ipc_dispatcher._thread.is_alive()
