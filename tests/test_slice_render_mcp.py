"""Opt-in five-state real-MCP Slice and unchanged-presentation capture proof."""

import asyncio
import hashlib
import json
import os
import shutil
import socket
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlparse

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from test_mcp import assert_loopback_listener, call, payload
from test_native import write_vti

import dcc_mcp_paraview
from dcc_mcp_paraview.server import ParaViewMcpServer

pytestmark = [
    pytest.mark.paraview,
    pytest.mark.mcp,
    pytest.mark.render,
    pytest.mark.skipif(
        not (
            os.environ.get("DISPLAY")
            and os.environ.get("PARAVIEW_SLICE_ACCEPTANCE") == "1"
            and shutil.which("pvpython")
        ),
        reason="Explicit isolated DISPLAY and Slice acceptance required",
    ),
]


class TracedClient(ClientSession):
    """Record actual SDK protocol request/result models, including every job poll."""

    def __init__(self, read, write, trace_stream):
        super().__init__(read, write)
        self.trace_stream = trace_stream
        self.events = []

    def record(self, event):
        self.events.append(event)
        self.trace_stream.write(json.dumps(event, allow_nan=False) + "\n")
        self.trace_stream.flush()

    async def send_request(self, request, result_type, *args, **kwargs):
        sequence = len(self.events) + 1
        self.record(
            {"sequence": sequence, "event": "request", "request": request.model_dump(mode="json", by_alias=True)}
        )
        try:
            result = await super().send_request(request, result_type, *args, **kwargs)
        except BaseException as error:
            self.record(
                {"sequence": sequence, "event": "exception", "type": type(error).__name__, "message": str(error)}
            )
            raise
        self.record({"sequence": sequence, "event": "result", "result": result.model_dump(mode="json", by_alias=True)})
        return result

    async def send_notification(self, notification, related_request_id=None):
        self.record(
            {
                "sequence": len(self.events) + 1,
                "event": "notification",
                "notification": notification.model_dump(mode="json", by_alias=True),
            }
        )
        return await super().send_notification(notification, related_request_id=related_request_id)

    def operation_names(self, start=0):
        return [
            event["request"]["params"]["name"]
            for event in self.events[start:]
            if event["event"] == "request"
            and event["request"]["method"] == "tools/call"
            and event["request"]["params"]["name"] != "jobs_get_status"
        ]


async def verified_call(client, name, arguments):
    result = await call(client, name, arguments)
    assert result["success"], result
    # inspect_pipeline has an existing read-only envelope without mutation postconditions.
    if name != "inspect_pipeline":
        assert result["postcondition"]["verified"] is True
    return result["context"]


def assert_analytic_geometry(volume, section):
    # Independent lattice expectations for the original x*x+y*y+z*z fixture.
    assert (volume["points"], volume["cells"], volume["bounds"]) == (343, 216, [-3, 3, -3, 3, -3, 3])
    assert volume["point_arrays"] == [{"name": "RadiusSquared", "components": 1, "range": [0, 27]}]
    # x=0 cuts a 7x7 lattice: 49 vertices, 6x6 squares triangulated into 72 cells.
    assert (section["points"], section["cells"], section["bounds"]) == (49, 72, [0, 0, -3, 3, -3, 3])
    assert section["point_arrays"] == [{"name": "RadiusSquared", "components": 1, "range": [0, 18]}]


def test_slice_five_saved_presentations_over_real_mcp(tmp_path):
    root = Path(os.environ.get("PARAVIEW_SLICE_WORKSPACE", str(tmp_path / "artifacts")))
    root.mkdir(parents=True, exist_ok=True)
    assert not any(root.iterdir()), "Use a fresh, empty operator-owned workspace"
    (root / "data").mkdir()
    input_path = root / "data/volume.vti"
    analytic = not os.environ.get("PARAVIEW_SLICE_INPUT")
    if not analytic:
        original = Path(os.environ["PARAVIEW_SLICE_INPUT"])
        assert original.is_file() and original.suffix.lower() == ".vti"
        assert 0 < original.stat().st_size <= 64 * 1024 * 1024
        input_path.write_bytes(original.read_bytes())
    else:
        # Original analytic test volume: RadiusSquared = x*x + y*y + z*z.
        write_vti(input_path)
    scalar = os.environ.get("PARAVIEW_SLICE_SCALAR", "RadiusSquared")
    presentation_controls = (
        {
            "line_width": 2,
            "scalar_bar_title": "Time (s)",
            "scalar_bar_position": [0.89, 0.18],
            "scalar_bar_length": 0.6,
            "scalar_bar_thickness": 12,
            "scalar_bar_title_font_size": 16,
            "scalar_bar_label_font_size": 14,
        }
        if os.environ.get("PARAVIEW_PRESENTATION_CONTROLS_ACCEPTANCE") == "1"
        else {}
    )
    adapter_dir = Path(dcc_mcp_paraview.__file__).parent
    evidence = {
        "core": version("dcc-mcp-core"),
        "server": version("dcc-mcp-server"),
        "sdk": version("mcp"),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "adapter_files_sha256": {
            name: hashlib.sha256((adapter_dir / name).read_bytes()).hexdigest()
            for name in ("host_driver.py", "contracts.py", "skills/paraview-pipeline/tools.yaml")
        },
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "analytic_fixture": analytic,
        "presentation_controls": presentation_controls,
        "views": [],
        "native_qualified": False,
    }
    log_dir = Path(os.environ["DCC_MCP_LOG_DIR"]).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    server = ParaViewMcpServer(
        workspace=root,
        registry_dir=str(tmp_path / "registry"),
        gateway_port=0,
        enable_gateway_failover=False,
        enable_file_logging=True,
        enable_telemetry=False,
        enable_job_persistence=True,
        enable_checkpoint_persistence=True,
        checkpoint_path=str(tmp_path / "checkpoints.json"),
    )
    owned_host, port = None, None
    workflow_complete = False
    trace_stream = (root / "mcp-trace.jsonl").open("x")

    async def workflow():
        async with streamable_http_client(server.mcp_url) as (read, write, _):
            async with TracedClient(read, write, trace_stream) as client:
                initialized = await client.initialize()
                evidence["protocol_version"] = initialized.protocolVersion
                assert payload(await client.call_tool("load_skill", {"skill_name": "paraview-pipeline"}))["loaded"]
                names, cursor = set(), None
                while True:
                    page = await client.list_tools(cursor=cursor)
                    names.update(t.name for t in page.tools)
                    cursor = page.nextCursor
                    if cursor is None:
                        break
                assert {"slice_plane", "inspect_presentation", "capture_current_view"} <= names

                async def verified(name, arguments):
                    return await verified_call(client, name, arguments)

                initial = await verified("inspect_pipeline", {})
                native = initial["host"]
                assert initial["sources"] == []
                assert native["thread_id"] == native["main_thread_id"] and native["pid"] != os.getpid()
                assert native["version"] == "5.13.2"
                evidence["host"] = native
                empty = await verified("inspect_presentation", {})
                assert empty == {"views": [], "current_view_index": None}
                volume = (await verified("open_dataset", {"name": "Volume", "path": "data/volume.vti"}))["source"]
                bounds = volume["bounds"]
                center = [(bounds[i] + bounds[i + 1]) / 2 for i in (0, 2, 4)]
                span = max(bounds[i + 1] - bounds[i] for i in (0, 2, 4))
                section = (
                    await verified(
                        "slice_plane",
                        {
                            "name": "Section",
                            "input_name": "Volume",
                            "origin": center,
                            "normal": [1, 0, 0],
                        },
                    )
                )["source"]
                assert section["type"] == "Cut" and not section["crinkle"] and section["triangulate"]
                assert section["bounds"][0:2] == [center[0], center[0]]
                assert section["points"] > 0 and section["cells"] > 0
                assert {a["name"] for a in volume["point_arrays"]} == {a["name"] for a in section["point_arrays"]}
                chosen = next(a for a in volume["point_arrays"] if a["name"] == scalar)
                assert chosen["components"] == 1 and chosen["range"][0] < chosen["range"][1]
                assert section["origin"] == center and section["normal"] == [1, 0, 0]
                assert section["offsets"] == [0.0]
                assert section["input_bindings"] == [{"source_names": ["Volume"], "port": 0}]
                if analytic:
                    assert_analytic_geometry(volume, section)
                pipeline = await verified("inspect_pipeline", {})
                assert pipeline["host"] == native
                assert {item["name"] for item in pipeline["sources"]} == {"Volume", "Section"}
                measured = {item["name"]: item for item in pipeline["sources"]}
                assert measured["Volume"] == volume
                assert measured["Section"] == {key: value for key, value in section.items() if key != "input_name"}
                evidence["pipeline"] = pipeline
                evidence["section"] = section
                # Same section, five independently saved native camera states.
                for index, direction in enumerate(([2, 1, 1], [2, -1, 1], [2, 0, 0.3], [2, 1, 2], [2, -2, 0.5])):
                    stem = "view-%d" % (index + 1)
                    position = [center[i] + span * direction[i] for i in range(3)]
                    configured = await verified(
                        "render_preview",
                        {
                            "name": "Section",
                            "path": stem + "-configured.png",
                            "width": 320,
                            "height": 240,
                            "scalar": scalar,
                            "color_range": chosen["range"],
                            "preset": "Viridis (matplotlib)",
                            "camera_position": position,
                            "camera_target": center,
                            "camera_view_up": [0, 0, 1],
                            "camera_parallel_scale": span * 0.65,
                            "show_scalar_bar": True,
                            "background": [0.04, 0.06, 0.1],
                            **presentation_controls,
                        },
                    )
                    for key, value in presentation_controls.items():
                        assert configured["artifact"]["view"][key] == value
                    before = await verified("inspect_presentation", {})
                    if presentation_controls:
                        view = before["views"][before["current_view_index"]]
                        displayed = [rep for rep in view["representations"] if rep.get("visible")]
                        legends = [bar for bar in view["scalar_bars"] if bar.get("visible")]
                        assert len(displayed) == len(legends) == 1
                        assert displayed[0]["line_width"] == 2
                        legend = legends[0]
                        assert (
                            legend["title"],
                            legend["position"],
                            legend["length"],
                            legend["thickness"],
                            legend["title_font_size"],
                            legend["label_font_size"],
                            legend["orientation"],
                            legend["window_location"],
                        ) == ("Time (s)", [0.89, 0.18], 0.6, 12, 16, 14, "Vertical", "Any Location")
                    assert before == await verified("inspect_presentation", {})
                    await verified("save_state", {"path": stem + ".pvsm", "data_directory": "data"})
                    captured = (await verified("capture_current_view", {"path": stem + "-current.png"}))["artifact"]
                    assert captured["presentation"] == before and captured["presentation_unchanged"]
                    restore_start = len(client.events)
                    await verified("reopen_state", {"path": stem + ".pvsm", "data_directory": "data"})
                    restored = await verified("inspect_presentation", {})
                    assert restored == before, "LoadState changed the measured presentation"
                    # No render_preview call is allowed between restore and capture.
                    reopened = (await verified("capture_current_view", {"path": stem + "-restored.png"}))["artifact"]
                    assert reopened["presentation"] == before and reopened["presentation_unchanged"]
                    assert captured["sha256"] == reopened["sha256"]
                    restore_operations = client.operation_names(restore_start)
                    assert restore_operations == ["reopen_state", "inspect_presentation", "capture_current_view"]
                    assert hashlib.sha256(input_path.read_bytes()).hexdigest() == evidence["input_sha256"]
                    evidence["views"].append(
                        {
                            "state": stem + ".pvsm",
                            "presentation": before,
                            "image_sha256": reopened["sha256"],
                            "restore_operations": restore_operations,
                            "no_parameter_reapply": True,
                        }
                    )
                final = await verified("inspect_pipeline", {})
                assert final == pipeline
                evidence["final_pipeline"] = final

    try:
        server.start()
        owned_host = server.host_session.process
        port = urlparse(server.mcp_url).port
        evidence["listener"] = assert_loopback_listener(port)
        observability = server.observability_summary
        assert observability["file_logging"] and observability["job_persistence"]
        assert observability["job_persistence_state"] == "healthy"
        assert Path(observability["log_dir"]).resolve().is_relative_to(log_dir)
        assert Path(observability["job_db"]).resolve().is_relative_to(log_dir)
        evidence["observability"] = observability
        asyncio.run(workflow())
        workflow_complete = True
    finally:
        try:
            server.stop()
            assert owned_host is None or owned_host.poll() is not None
            assert not server.ipc_dispatcher._thread.is_alive()
            if port is not None:
                with socket.socket() as connection:
                    assert connection.connect_ex(("127.0.0.1", port)) != 0
            evidence["cleanup"] = {"host_exited": True, "pump_stopped": True, "listener_closed": True}
            evidence["native_qualified"] = workflow_complete
        finally:
            trace_stream.close()
            (root / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
