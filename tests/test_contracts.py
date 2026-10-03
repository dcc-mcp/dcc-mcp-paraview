import importlib
import json
import sys
from pathlib import Path

import pytest
from dcc_mcp_core import validate_skill

from dcc_mcp_paraview.bridge import call
from dcc_mcp_paraview.contracts import OperationError, validate

SKILL = Path(importlib.import_module("dcc_mcp_paraview").__file__).parent / "skills/paraview-pipeline"


@pytest.mark.parametrize(
    "operation,params",
    [
        ("exec", {"code": "print(1)"}),
        ("create_sphere", {"name": "bad name"}),
        ("create_sphere", {"name": "ok", "radius": float("nan")}),
        ("create_sphere", {"name": "ok", "radius": True}),
        ("create_sphere", {"name": "ok", "resolution": 1024}),
        ("create_sphere", {"name": "ok", "extra": 1}),
        ("clip_plane", {"name": "a", "input_name": "b", "normal": [0, 0, 0]}),
        ("render_preview", {"name": "a", "path": "x.png", "color_range": [2, 1]}),
        ("render_preview", {"name": "a", "path": "x.png", "preset": "../../bad"}),
        ("render_preview", {"name": "a", "path": "x.png", "preset": []}),
        ("save_state", {"path": "x\x00.pvsm"}),
        ("render_preview", {"name": "a", "path": "x.png", "camera_position": [0, 0, 0], "camera_target": [0, 0, 0]}),
        ("render_preview", {"name": "a", "path": "x.png", "color_range": [0, 1]}),
        ([], {}),
        ("create_sphere", {"name": "ok", "radius": 10**1000}),
        ("create_sphere", {"name": "ok", "center": [10**1000, 0, 0]}),
        ("contour", {"name": "a", "input_name": "b", "scalar": "x", "values": list(range(9))}),
    ],
)
def test_reject_invalid_arguments(operation, params):
    with pytest.raises(OperationError):
        validate(operation, params)


def test_host_imports_are_lazy():
    importlib.import_module("dcc_mcp_paraview.server")
    assert "paraview" not in sys.modules
    assert "paraview.simple" not in sys.modules


def test_unbound_call_is_canonical_error():
    value = call("inspect_pipeline")
    assert value["success"] is False
    assert value["error"] == "host_not_bound"


def test_skill_validation_and_closed_typed_contracts():
    value = validate_skill(str(SKILL))
    assert value.is_clean, value.issues
    tools = json.loads((SKILL / "tools.yaml").read_text())["tools"]
    for tool in tools:
        assert tool["input_schema"]["additionalProperties"] is False
        assert all(isinstance(v, bool) for v in tool["annotations"].values())
        assert tool["enforce_thread_affinity"] is True
        assert (SKILL / tool["source_file"]).is_file()
        if not tool["annotations"]["read_only_hint"]:
            assert tool["execution"] == "async"
        assert tool["affinity"] == ("any" if tool["name"] == "get_adapter_info" else "main")


@pytest.mark.parametrize(
    "params",
    [
        {"solid_color": [-0.1, 0, 0]},
        {"solid_color": [True, 0, 0]},
        {"solid_color": [1, 0, 0], "scalar": "Speed"},
        {"show_scalar_bar": 0},
        {"show_orientation_axes": "false"},
        {"camera_parallel_scale": 0},
        {"camera_parallel_scale": float("inf")},
        {"camera_view_angle": 121},
        {"ambient": -1},
        {"diffuse": 2},
        {"specular": True},
        {"specular_power": 129},
        {"camera_view_up": [0, 0, 0]},
        {"camera_view_up": [0, float("nan"), 1]},
    ],
)
def test_styling_contract_rejects_invalid(params):
    with pytest.raises(OperationError):
        validate("render_preview", {"name": "Sphere", "path": "preview.png", **params})


def test_styling_schema_and_native_contract_agree():
    import jsonschema

    schema = next(
        t["input_schema"]
        for t in json.loads((SKILL / "tools.yaml").read_text())["tools"]
        if t["name"] == "render_preview"
    )
    params = {
        "name": "Sphere",
        "path": "preview.png",
        "solid_color": [0.1, 0.2, 0.3],
        "show_scalar_bar": False,
        "show_orientation_axes": False,
        "camera_parallel_scale": 3.25,
        "camera_view_angle": 25,
        "camera_view_up": [0, 0, 1],
        "ambient": 0.2,
        "diffuse": 0.6,
        "specular": 0.5,
        "specular_power": 64,
    }
    jsonschema.validate(params, schema)
    assert validate("render_preview", params) == params


@pytest.mark.parametrize("operation", ["save_state", "reopen_state"])
@pytest.mark.parametrize("directory", [None, True, "", "x\x00y", "x" * 4097])
def test_relocation_directory_contract_is_bounded(operation, directory):
    with pytest.raises(OperationError):
        validate(operation, {"path": "scene.pvsm", "data_directory": directory})
