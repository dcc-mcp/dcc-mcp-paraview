"""Required native gates. These are never substituted by the controlled host-free fixtures."""

import hashlib
import math
import shutil
import struct
from xml.etree import ElementTree

import pytest
from test_native import session as session

pytestmark = [pytest.mark.paraview, pytest.mark.skipif(not shutil.which("pvpython"), reason="pvpython not installed")]


def small_parameters(serialization="native"):
    return {
        "name": "Volume",
        "path": "volume.vti",
        "dimensions": [7, 7, 7],
        "origin": [-3, -3, -3],
        "spacing": [1, 1, 1],
        "fields": [{"name": "RadiusSquared", "dtype": "float64", "expression": "x*x+y*y+z*z"}],
        "serialization": serialization,
    }


@pytest.mark.parametrize("serialization", ["native", "canonical_ascii_v1"])
def test_native_typed_volume_full_values_state_contour_slice(session, tmp_path, serialization):
    generated = session.request("create_image_data", small_parameters(serialization))
    assert generated["source"]["type"] == "XMLImageDataReader"
    assert generated["source"]["points"] == 343
    assert generated["artifact"]["all_values_equal"] is True
    expected = hashlib.sha256()
    for z in range(-3, 4):
        for y in range(-3, 4):
            for x in range(-3, 4):
                expected.update(struct.pack("<d", x * x + y * y + z * z))
    assert generated["image_data"]["arrays"][0]["sha256_le_float64"] == expected.hexdigest()
    saved = session.request("save_state", {"path": "generated.pvsm"})
    reopened = session.request("reopen_state", {"path": "generated.pvsm"})
    assert reopened["sources"] == saved["sources"]
    assert reopened["reader_paths"]["Volume"] == str(tmp_path / "volume.vti")
    contour = session.request(
        "contour", {"name": "Iso", "input_name": "Volume", "scalar": "RadiusSquared", "values": [4]}
    )
    section = session.request(
        "slice_plane", {"name": "Section", "input_name": "Volume", "origin": [0, 0, 0], "normal": [1, 0, 0]}
    )
    assert contour["source"]["points"] > 0 and section["source"]["points"] == 49
    assert section["source"]["cells"] == 72


def test_native_analytic_fixture_numeric_and_canonical_byte_gates(session, tmp_path):
    delay_expression = "220*exp(-(((x-.2)/.9)**2+((y+.1)/.65)**2+((z-1.2)/.30)**2))"
    time_expression = ".6+.4*z+.025*(x*x+y*y)-(" + delay_expression + ")/1000"
    params = {
        "name": "AnalyticVolume",
        "path": "analytic.vti",
        "dimensions": [81, 65, 97],
        "origin": [-2, -1.6, 0],
        "spacing": [0.05, 0.05, 0.025],
        "significant_digits": 10,
        "active_scalar": "SyntheticTime_s",
        "serialization": "canonical_ascii_v1",
        "fields": [
            {"name": "SyntheticTime_s", "dtype": "float64", "expression": time_expression},
            {"name": "SyntheticDelay_ms", "dtype": "float64", "expression": delay_expression},
        ],
    }
    generated = session.request("create_image_data", params)
    assert generated["image_data"]["points"] == 510705 and generated["image_data"]["cells"] == 491520
    artifact = tmp_path / "analytic.vti"
    arrays = ElementTree.parse(artifact).findall(".//DataArray")
    assert [a.get("Name") for a in arrays] == [f["name"] for f in params["fields"]]
    values = [[float(value) for value in array.text.split()] for array in arrays]
    assert all(len(field) == 510705 for field in values)
    hashes = [hashlib.sha256(), hashlib.sha256()]
    index = 0
    # Independent mathematical oracle, not the production expression interpreter.
    # The time field deliberately uses unquantized d; fields are rounded independently.
    for iz in range(97):
        z = iz * 0.025
        for iy in range(65):
            y = -1.6 + iy * 0.05
            for ix in range(81):
                x = -2 + ix * 0.05
                q = ((x - 0.2) / 0.9) ** 2 + ((y + 0.1) / 0.65) ** 2 + ((z - 1.2) / 0.30) ** 2
                d = 220 * math.exp(-q)
                expected = [
                    float(format(0.6 + 0.4 * z + 0.025 * (x * x + y * y) - d / 1000, ".10g")),
                    float(format(d, ".10g")),
                ]
                for field, value in enumerate(expected):
                    assert struct.pack("<d", values[field][index]) == struct.pack("<d", value)
                    hashes[field].update(struct.pack("<d", value))
                index += 1
    assert [a["sha256_le_float64"] for a in generated["image_data"]["arrays"]] == [h.hexdigest() for h in hashes]
    # A distinct lexical identity gate, not a substitute for the numerical assertions above.
    assert (
        hashlib.sha256(artifact.read_bytes()).hexdigest()
        == "43ade20b1f2b78149e7d948894568b599db55f5aa8f9a5ceb62050e970e93d9d"
    )


@pytest.mark.mcp
def test_real_http_image_data_discovery_job_and_runtime(tmp_path):
    import asyncio
    from importlib.metadata import version

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from test_mcp import call, payload

    from dcc_mcp_paraview.server import ParaViewMcpServer

    assert version("dcc-mcp-core") == version("dcc-mcp-server") == "0.20.41"
    assert version("mcp") == "1.30.0"
    server = ParaViewMcpServer(
        workspace=tmp_path,
        registry_dir=str(tmp_path / "registry"),
        enable_gateway_failover=False,
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_telemetry=False,
        enable_checkpoint_persistence=False,
    )

    async def exercise():
        async with streamable_http_client(server.mcp_url) as (read, write, _), ClientSession(read, write) as client:
            await client.initialize()
            found = payload(await client.call_tool("search_skills", {"query": "paraview pipeline"}))
            assert found
            loaded = payload(await client.call_tool("load_skill", {"skill_name": "paraview-pipeline"}))
            assert loaded
            tools, cursor = [], None
            while True:
                page = await client.list_tools(cursor=cursor)
                tools.extend(page.tools)
                cursor = page.nextCursor
                if not cursor:
                    break
            assert any(tool.name == "create_image_data" for tool in tools)
            result = await call(client, "create_image_data", small_parameters())
            assert result["success"] is True
            assert result["context"]["runtime"] == {"adapter": "0.1.0", "core": "0.20.41", "server": "0.20.41"}
            assert result["context"]["image_data"]["host_version"] == "5.13.2"
            assert result["context"]["artifact"]["all_values_equal"] is True

    try:
        server.start()
        asyncio.run(exercise())
    finally:
        server.stop()
    assert server.host_session.process.poll() is not None
