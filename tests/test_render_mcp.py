"""Opt-in isolated DISPLAY acceptance; never touches an existing ParaView GUI scene."""

import asyncio
import json
import os
import shutil
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from dcc_mcp_paraview.server import ParaViewMcpServer

auto_skip = not (os.environ.get("DISPLAY") and os.environ.get("PARAVIEW_RENDER_ACCEPTANCE") == "1")
pytestmark = [
    pytest.mark.paraview,
    pytest.mark.mcp,
    pytest.mark.render,
    pytest.mark.skipif(
        auto_skip or not shutil.which("pvpython"), reason="Explicit isolated display acceptance required"
    ),
]


def test_render_camera_scalar_native_readback_over_mcp(tmp_path):
    # Imports from test modules also work when this suite is copied outside the source tree for wheel QA.
    from test_mcp import assert_loopback_listener, call, payload
    from test_native import write_vti

    root = Path(os.environ.get("PARAVIEW_RENDER_WORKSPACE", str(tmp_path)))
    root.mkdir(parents=True, exist_ok=True)
    assert not (root / "preview.png").exists(), "Use a fresh render workspace; outputs never overwrite"
    write_vti(root / "volume.vti")
    server = ParaViewMcpServer(
        workspace=root,
        registry_dir=str(tmp_path / "registry"),
        enable_gateway_failover=False,
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_telemetry=False,
        enable_checkpoint_persistence=False,
    )
    evidence = {"operations": []}

    async def workflow():
        async with httpx.AsyncClient(trust_env=False) as http:
            async with streamable_http_client(server.mcp_url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    evidence["protocol_version"] = initialized.protocolVersion
                    loaded = payload(await client.call_tool("load_skill", {"skill_name": "paraview-pipeline"}))
                    assert loaded["loaded"]
                    names = set()
                    cursor = None
                    while True:
                        page = await client.list_tools(cursor=cursor)
                        names.update(tool.name for tool in page.tools)
                        cursor = page.nextCursor
                        if cursor is None:
                            break
                    assert {"open_dataset", "contour", "render_preview", "save_state", "reopen_state"} <= names
                    operations = [
                        ("open_dataset", {"name": "Volume", "path": "volume.vti"}),
                        (
                            "contour",
                            {"name": "Contour", "input_name": "Volume", "scalar": "RadiusSquared", "values": [4, 6]},
                        ),
                        (
                            "render_preview",
                            {
                                "name": "Contour",
                                "path": "preview.png",
                                "width": 320,
                                "height": 240,
                                "scalar": "RadiusSquared",
                                "preset": "Viridis (matplotlib)",
                                "color_range": [0, 10],
                                "camera_position": [9, 7, 6],
                                "camera_target": [0, 0, 0],
                                "background": [0.1, 0.15, 0.2],
                                "show_scalar_bar": False,
                                "show_orientation_axes": False,
                                "camera_parallel_scale": 3.2,
                                "ambient": 0.2,
                                "diffuse": 0.7,
                                "specular": 0.3,
                                "specular_power": 32,
                            },
                        ),
                        ("save_state", {"path": "rendered.pvsm"}),
                        ("reopen_state", {"path": "rendered.pvsm"}),
                        (
                            "render_preview",
                            {
                                "name": "Contour",
                                "path": "reopened-preview.png",
                                "width": 320,
                                "height": 240,
                                "scalar": "RadiusSquared",
                                "preset": "Viridis (matplotlib)",
                                "color_range": [0, 10],
                                "camera_position": [9, 7, 6],
                                "camera_target": [0, 0, 0],
                                "background": [0.1, 0.15, 0.2],
                                "show_scalar_bar": False,
                                "show_orientation_axes": False,
                                "camera_parallel_scale": 3.2,
                                "ambient": 0.2,
                                "diffuse": 0.7,
                                "specular": 0.3,
                                "specular_power": 32,
                            },
                        ),
                    ]
                    for name, arguments in operations:
                        result = await call(client, name, arguments)
                        assert result["success"], result
                        assert result["postcondition"]["verified"] is True
                        evidence["operations"].append({"name": name, "result": result})
                        if name == "render_preview":
                            artifact = result["context"]["artifact"]
                            assert artifact["dimensions"] == [320, 240]
                            assert any(low != high for low, high in artifact["pixel_ranges"])
                            view = artifact["view"]
                            for key in (
                                "camera_position",
                                "camera_target",
                                "background",
                                "color_range",
                                "preset",
                                "scalar",
                                "show_scalar_bar",
                                "show_orientation_axes",
                                "camera_parallel_scale",
                                "ambient",
                                "diffuse",
                                "specular",
                                "specular_power",
                            ):
                                assert view[key] == arguments[key]
                            assert len(view["color_control_points"]) >= 8
                    solid_arguments = {
                        "name": "Contour",
                        "path": "solid-preview.png",
                        "width": 320,
                        "height": 240,
                        "solid_color": [0.16, 0.18, 0.22],
                        "ambient": 0.15,
                        "diffuse": 0.7,
                        "specular": 0.6,
                        "specular_power": 48,
                        "show_scalar_bar": False,
                        "show_orientation_axes": False,
                        "camera_position": [9, 7, 6],
                        "camera_target": [0, 0, 0],
                        "camera_view_up": [0, 0, 1],
                        "camera_view_angle": 22,
                    }
                    solid = await call(client, "render_preview", solid_arguments)
                    assert solid["success"], solid
                    view = solid["context"]["artifact"]["view"]
                    assert view["diffuse_color"] == solid_arguments["solid_color"]
                    assert view["camera_parallel_projection"] is False
                    assert view["camera_view_angle"] == 22 and view["specular"] == 0.6
                    assert view["show_scalar_bar"] is False and view["show_orientation_axes"] is False
                    evidence["operations"].append({"name": "render_preview_solid", "result": solid})
                    assert (await call(client, "save_state", {"path": "styled.pvsm"}))["success"]
                    assert (await call(client, "reopen_state", {"path": "styled.pvsm"}))["success"]
                    solid_arguments["path"] = "reopened-solid-preview.png"
                    reopened = await call(client, "render_preview", solid_arguments)
                    assert reopened["success"], reopened
                    assert reopened["context"]["artifact"]["view"] == view
                    assert (root / "solid-preview.png").read_bytes() == (
                        root / "reopened-solid-preview.png"
                    ).read_bytes()
                    assert (root / "preview.png").read_bytes() == (root / "reopened-preview.png").read_bytes()
                    evidence["operations"].append({"name": "render_preview_solid_reopened", "result": reopened})
                    visible_legend = await call(
                        client,
                        "render_preview",
                        {
                            "name": "Contour",
                            "path": "legend-preview.png",
                            "width": 320,
                            "height": 240,
                            "scalar": "RadiusSquared",
                            "color_range": [0, 10],
                            "show_scalar_bar": True,
                            "show_orientation_axes": True,
                        },
                    )
                    assert visible_legend["success"], visible_legend
                    assert visible_legend["context"]["artifact"]["view"]["show_scalar_bar"] is True
                    assert visible_legend["context"]["artifact"]["view"]["show_orientation_axes"] is True
                    evidence["operations"].append({"name": "render_preview_visible_legend", "result": visible_legend})
                    solid_arguments["path"] = "post-legend-solid.png"
                    hidden_again = await call(client, "render_preview", solid_arguments)
                    assert hidden_again["success"], hidden_again
                    assert (root / "solid-preview.png").read_bytes() == (root / "post-legend-solid.png").read_bytes()
                    evidence["operations"].append({"name": "render_preview_hidden_again", "result": hidden_again})
                    # A movable project is produced only through native SaveState with
                    # relative reader properties; the client only copies artifact bytes.
                    (root / "public/data").mkdir(parents=True)
                    shutil.copy2(root / "volume.vti", root / "public/data/volume.vti")
                    portable = await call(
                        client,
                        "save_state",
                        {
                            "path": "public/project.pvsm",
                            "data_directory": "public/data",
                        },
                    )
                    assert portable["success"], portable
                    assert portable["context"]["artifact"]["portable_relative_paths"] is True
                    serialized = (root / "public/project.pvsm").read_bytes()
                    assert str(root).encode() not in serialized
                    shutil.copytree(root / "public", root / "moved")
                    (root / "volume.vti").rename(root / "old-input.vti")
                    (root / "public").rename(root / "old-package")
                    relocated = await call(
                        client,
                        "reopen_state",
                        {
                            "path": "moved/project.pvsm",
                            "data_directory": "moved/data",
                        },
                    )
                    assert relocated["success"], relocated
                    assert relocated["context"]["relocation_verified"] is True
                    assert relocated["context"]["reader_paths"] == {"Volume": str(root / "moved/data/volume.vti")}
                    assert (root / "moved/project.pvsm").read_bytes() == serialized
                    solid_arguments["path"] = "moved/reopened.png"
                    relocated_render = await call(client, "render_preview", solid_arguments)
                    assert relocated_render["success"], relocated_render
                    assert (root / "solid-preview.png").read_bytes() == (root / "moved/reopened.png").read_bytes()
                    evidence["operations"].extend(
                        [
                            {"name": "save_portable_state", "result": portable},
                            {"name": "reopen_relocated_state", "result": relocated},
                            {"name": "render_relocated_state", "result": relocated_render},
                        ]
                    )
                    native = (await call(client, "inspect_pipeline", {}))["context"]["host"]
                    assert native["render_available"] and native["thread_id"] == native["main_thread_id"]
                    evidence["host"] = native

    try:
        server.start()
        from urllib.parse import urlparse

        evidence["listener"] = assert_loopback_listener(urlparse(server.mcp_url).port)
        asyncio.run(workflow())
    finally:
        server.stop()
    assert server.host_session.process.poll() is not None
    assert not server.ipc_dispatcher._thread.is_alive()
    evidence["cleanup"] = {"host_exited": True, "pump_stopped": True, "server_stopped": not server.is_running}
    if os.environ.get("PARAVIEW_RENDER_EVIDENCE"):
        Path(os.environ["PARAVIEW_RENDER_EVIDENCE"]).write_text(json.dumps(evidence, indent=2) + "\n")
