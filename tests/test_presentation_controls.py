"""Host-free preflight, schema and native-property readback fixtures for r4 controls."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from test_contracts import SKILL
from test_host_files import host as host
from test_slice_presentation import presentation, source

from dcc_mcp_paraview.contracts import OperationError, validate

CONTROLS = {
    "line_width": 2,
    "scalar_bar_title": "Time (s)",
    "scalar_bar_position": [0.89, 0.18],
    "scalar_bar_length": 0.6,
    "scalar_bar_thickness": 12,
    "scalar_bar_title_font_size": 16,
    "scalar_bar_label_font_size": 14,
}
SCHEMA = next(
    t["input_schema"] for t in json.loads((SKILL / "tools.yaml").read_text())["tools"] if t["name"] == "render_preview"
)


@pytest.mark.parametrize(
    "controls",
    [
        {"line_width": 0},
        {"line_width": 8.1},
        {"line_width": True},
        {"scalar_bar_title": ""},
        {"scalar_bar_title": "x" * 81},
        {"scalar_bar_title": "Time\n(s)"},
        {"scalar_bar_title": "Time\x00(s)"},
        {"scalar_bar_title": "Time\x7f(s)"},
        {"scalar_bar_title": "Time\u202e(s)"},
        {"scalar_bar_title": "$x$"},
        {"scalar_bar_title": "{value}"},
        {"scalar_bar_title": "\\alpha"},
        {"scalar_bar_position": [0.5]},
        {"scalar_bar_position": [0.5, 0.2, 0.1]},
        {"scalar_bar_position": [True, 0.2]},
        {"scalar_bar_position": [-0.01, 0.2]},
        {"scalar_bar_position": [0.9, 1.01]},
        {"scalar_bar_length": 0},
        {"scalar_bar_length": 0.91},
        {"scalar_bar_thickness": 0},
        {"scalar_bar_thickness": 65},
        {"scalar_bar_thickness": 12.5},
        {"scalar_bar_title_font_size": 5},
        {"scalar_bar_title_font_size": 49},
        {"scalar_bar_label_font_size": True},
        {"scalar_bar_label_font_size": 13.5},
        {"scalar_bar_label_format": "%n"},
    ],
)
def test_invalid_controls_fail_schema_and_before_any_native_mutation(host, controls):
    params = {"name": "Field", "path": "x.png", "scalar": "RadiusSquared", **controls}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(params, SCHEMA)
    host.pv = object()  # Any accidental source/view/legend access will fail the test.
    with pytest.raises(OperationError) as error:
        validate("render_preview", params)
        host.render_preview(**params)
    assert error.value.code == "invalid_input"
    if set(controls).issubset(CONTROLS):
        with pytest.raises(OperationError) as direct:
            host.render_preview(**params)
        assert direct.value.code == "invalid_input"


@pytest.mark.parametrize(
    "controls",
    [
        {"line_width": float("nan")},
        {"line_width": float("inf")},
        {"scalar_bar_position": [0.9, float("nan")]},
        {"scalar_bar_length": float("inf")},
        {"scalar_bar_position": [0.89, 0.6], "scalar_bar_length": 0.6},
    ],
)
def test_nonfinite_and_out_of_view_vertical_extent_preflight(host, controls):
    host.pv = object()
    with pytest.raises(OperationError) as error:
        host.render_preview(name="Field", path="x.png", scalar="RadiusSquared", **controls)
    assert error.value.code == "invalid_input"


@pytest.mark.parametrize("name", [key for key in CONTROLS if key.startswith("scalar_bar_")])
def test_legend_controls_require_scalar_before_mutation(host, name):
    params = {"name": "Field", "path": "x.png", name: CONTROLS[name]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(params, SCHEMA)
    host.pv = object()
    with pytest.raises(OperationError, match="require scalar"):
        host.render_preview(**params)


def test_target_and_boundary_controls_are_valid():
    for controls in (
        CONTROLS,
        {"line_width": 1},
        {"line_width": 8},
        {"scalar_bar_position": [0, 0], "scalar_bar_length": 0.9},
        {
            "scalar_bar_length": 0.05,
            "scalar_bar_thickness": 1,
            "scalar_bar_title_font_size": 6,
            "scalar_bar_label_font_size": 48,
        },
    ):
        params = {"name": "Field", "path": "x.png", "scalar": "RadiusSquared", **controls}
        jsonschema.validate(params, SCHEMA)
        assert validate("render_preview", params) == params


def renderer(host, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":controlled-fixture")
    view, display, bar = presentation(host)
    volume = source()
    display.Input = [volume]
    display.LineWidth = 1
    bar.ScalarBarLength = 0.33
    bar.ScalarBarThickness = 16
    bar.TitleFontSize = bar.LabelFontSize = 16
    bar.WindowLocation = "Lower Right Corner"
    lut = display.LookupTable
    lut.ApplyPreset = lambda name, rescale: True
    lut.RescaleTransferFunction = lambda low, high: setattr(lut, "RGBPoints", [low, 0, 0, 0, high, 1, 1, 1])
    display.SetScalarBarVisibility = lambda view, visible: setattr(bar, "Visibility", int(visible))
    events = []

    def screenshot(path, target, ImageResolution=None):
        assert target is view
        width, height = ImageResolution or view.ViewSize
        Path(path).write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + b"\x00" * 8
            + width.to_bytes(4, "big")
            + height.to_bytes(4, "big")
            + b"controlled pixels"
        )
        hook = getattr(host.pv, "capture_hook", None)
        if hook:
            hook()

    host.pv = SimpleNamespace(
        FindSource=lambda name: volume,
        GetSources=lambda: {("Field", "1"): volume},
        GetViews=lambda: [view],
        Hide=lambda source, view: events.append("Hide"),
        HideUnusedScalarBars=lambda view: events.append("HideBars"),
        Show=lambda source, view: display,
        GetColorTransferFunction=lambda scalar: lut,
        GetScalarBar=lambda lookup, view: bar,
        ColorBy=lambda display, array: setattr(display, "ColorArrayName", list(array)),
        Render=lambda view: events.append("Render"),
        ResetCamera=lambda view: events.append("ResetCamera"),
        SaveScreenshot=screenshot,
    )
    pixels = SimpleNamespace(
        GetDimensions=lambda: [*view.ViewSize, 1],
        GetPointData=lambda: SimpleNamespace(
            GetScalars=lambda: SimpleNamespace(GetNumberOfComponents=lambda: 3, GetRange=lambda channel: [0, 255])
        ),
    )
    reader = SimpleNamespace(SetFileName=lambda path: None, Update=lambda: None, GetOutput=lambda: pixels)
    monkeypatch.setitem(sys.modules, "vtkmodules.vtkIOImage", SimpleNamespace(vtkPNGReader=lambda: reader))
    return view, display, bar, events


def test_render_controls_readback_inspection_and_capture_preserve_native_values(host, tmp_path, monkeypatch):
    view, display, bar, events = renderer(host, monkeypatch)
    rendered = host.render_preview(
        name="Field", path="configured.png", scalar="RadiusSquared", width=720, height=480, **CONTROLS
    )["artifact"]
    for key, value in CONTROLS.items():
        assert rendered["view"][key] == value
    assert rendered["view"]["scalar_bar_orientation"] == "Vertical"
    assert rendered["view"]["scalar_bar_window_location"] == "Any Location"
    before = host.inspect_presentation()
    measured = before["views"][0]
    assert measured["representations"][0]["line_width"] == 2
    legend = measured["scalar_bars"][0]
    assert (legend["title"], legend["position"], legend["length"], legend["thickness"]) == (
        "Time (s)",
        [0.89, 0.18],
        0.6,
        12,
    )
    assert (legend["title_font_size"], legend["label_font_size"]) == (16, 14)
    captured = host.capture_current_view("current.png")["artifact"]
    assert captured["presentation"] == before and captured["presentation_unchanged"]
    assert captured["dimensions"] == [720, 480]
    assert (tmp_path / "configured.png").read_bytes() == (tmp_path / "current.png").read_bytes()
    assert events.count("Render") == 1  # capture itself never reapplies presentation.


def test_omitted_controls_preserve_existing_native_values(host, monkeypatch):
    _, display, bar, _ = renderer(host, monkeypatch)
    display.LineWidth = 3
    bar.Title = "Existing"
    bar.Position = [0.8, 0.1]
    bar.ScalarBarLength = 0.7
    bar.ScalarBarThickness = 20
    bar.TitleFontSize = 22
    bar.LabelFontSize = 18
    host.render_preview(name="Field", path="default.png", scalar="RadiusSquared")
    assert (
        display.LineWidth,
        bar.Title,
        bar.Position,
        bar.ScalarBarLength,
        bar.ScalarBarThickness,
        bar.TitleFontSize,
        bar.LabelFontSize,
        bar.WindowLocation,
    ) == (3, "Existing", [0.8, 0.1], 0.7, 20, 22, 18, "Lower Right Corner")


@pytest.mark.parametrize("property_name,coerced", [("LineWidth", 1), ("TitleFontSize", 15), ("Position", [0.8, 0.1])])
def test_changed_native_controls_fail_verification_without_publication(
    host, tmp_path, monkeypatch, property_name, coerced
):
    _, display, bar, _ = renderer(host, monkeypatch)
    target = display if property_name == "LineWidth" else bar
    host.pv.capture_hook = lambda: setattr(target, property_name, coerced)
    with pytest.raises(OperationError) as error:
        host.render_preview(name="Field", path="rejected.png", scalar="RadiusSquared", **CONTROLS)
    assert error.value.code == "verification_failed"
    assert not (tmp_path / "rejected.png").exists()
    assert not list(tmp_path.glob(".rejected.*.png"))


def test_unavailable_native_line_width_fails_clearly_without_publication(host, tmp_path, monkeypatch):
    _, display, _, _ = renderer(host, monkeypatch)
    get_property = display.GetProperty
    display.GetProperty = lambda name: None if name == "LineWidth" else get_property(name)
    with pytest.raises(OperationError, match="line width property is unavailable") as error:
        host.render_preview(name="Field", path="missing.png", scalar="RadiusSquared", line_width=2)
    assert error.value.code == "verification_failed"
    assert not (tmp_path / "missing.png").exists()
