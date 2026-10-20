"""Host-free contracts, expression arithmetic, native-shaped doubles and file safety.

These fixtures are NOT native ParaView acceptance.
"""

import copy
import hashlib
import json
import math
import struct
import threading
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from test_host_files import host as host

from dcc_mcp_paraview.contracts import OperationError, validate
from dcc_mcp_paraview.image_data import ImageDataError, evaluate_expression, parse_expression, plan_image_data
from dcc_mcp_paraview.image_files import ImageOutput


def parameters(**changes):
    return {
        "name": "Volume",
        "path": "volume.vti",
        "dimensions": [2, 3, 2],
        "origin": [-1, 0, 2],
        "spacing": [0.5, 1, 2],
        "fields": [{"name": "Value", "dtype": "float64", "expression": "x + 10*y + 100*z"}],
        **changes,
    }


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "x.real",
        "x[0]",
        "[x]",
        "(x,y)",
        "{x: y}",
        "lambda: x",
        "[x for x in y]",
        "x if y else z",
        "x < y",
        "x and y",
        "x := 1",
        "x; y",
        "'1'",
        "True",
        "None",
        "1j",
        "0xff",
        "0b10",
        "1_000",
        "x^2",
        "x//2",
        "x%2",
        "sqrt(x)",
        "exp(x,y)",
        "exp(value=x)",
        "exp(*x)",
        "pow(x,2)",
        "x**y",
        "x**2.0",
        "x**(2+1)",
        "x**9",
        "x**-9",
        "x**0x2",
        "1e309",
        "nan",
        "inf",
        "delay/1000",
        "",
        "x" * 513,
        "x # ignored",
        "𝓍",
        "x\ny",
    ],
)
def test_expression_allowlist_rejects_unsafe_or_unsupported(expression):
    with pytest.raises(ImageDataError):
        parse_expression(expression)


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("1", 1),
        (".5 + 2e-1", 0.7),
        ("+x - -y", 3),
        ("x + y*z", 7),
        ("(x+y)*z", 9),
        ("z**-2", 1 / 9),
        ("x**+8", 1),
        ("exp(-x)", math.exp(-1)),
        ("(x-x)**0", 1),
    ],
)
def test_explicit_arithmetic(expression, expected):
    assert evaluate_expression(parse_expression(expression), (1.0, 2.0, 3.0)) == expected


@pytest.mark.parametrize("expression", ["1/(x-x)", "0**-1", "exp(1000)", "1e308*1e308", "(1e308*2)*0"])
def test_bad_intermediate_is_rejected_even_if_later_operation_could_hide_it(expression):
    with pytest.raises(ImageDataError) as error:
        evaluate_expression(parse_expression(expression), (1.0, 2.0, 3.0))
    assert error.value.code == "invalid_scalar"


def test_independent_final_quantization():
    delay = "220*exp(-(((x-.2)/.9)**2+((y+.1)/.65)**2+((z-1.2)/.30)**2))"
    time = ".6+.4*z+.025*(x*x+y*y)-(" + delay + ")/1000"
    coordinates = (0.25, 0.15, 1.25)
    x, y, z = coordinates
    d = 220 * math.exp(-(((x - 0.2) / 0.9) ** 2 + ((y + 0.1) / 0.65) ** 2 + ((z - 1.2) / 0.30) ** 2))
    assert evaluate_expression(parse_expression(delay), coordinates, 10) == float(format(d, ".10g"))
    assert evaluate_expression(parse_expression(time), coordinates, 10) == float(
        format(0.6 + 0.4 * z + 0.025 * (x * x + y * y) - d / 1000, ".10g")
    )
    assert evaluate_expression(parse_expression("1.2345"), coordinates, 3) == 1.23


def test_parser_and_operation_budgets():
    for expression in ("-" * 17 + "x", "+".join(["x"] * 100)):
        with pytest.raises(ImageDataError) as error:
            parse_expression(expression)
        assert error.value.code == "resource_limit"
    bounded = "exp(exp(exp(exp(exp(exp(exp(exp(exp(exp(exp(exp(exp(exp(x))))))))))))))"
    with pytest.raises(ImageDataError):
        plan_image_data(
            parameters(
                dimensions=[100, 100, 100],
                fields=[{"name": "F" + str(i), "dtype": "float64", "expression": bounded} for i in range(4)],
            )
        )


@pytest.mark.parametrize(
    "change",
    [
        {"dimensions": [1, 2, 2]},
        {"dimensions": [True, 2, 2]},
        {"dimensions": [2.0, 2, 2]},
        {"dimensions": [100, 100, 101]},
        {"dimensions": [10**1000, 2, 2]},
        {"origin": [float("inf"), 0, 0]},
        {"origin": [float("nan"), 0, 0]},
        {"spacing": [0, 1, 1]},
        {"spacing": [-1, 1, 1]},
        {"spacing": [True, 1, 1]},
        {"spacing": [10**1000, 1, 1]},
        {"origin": [1e6, 0, 0]},
        {"origin": [1e6 - 1, 0, 0], "spacing": [1e-20, 1, 1]},
        {"fields": []},
        {"fields": [{"name": "A", "dtype": "float32", "expression": "x"}]},
        {"fields": [{"name": 'A"bad', "dtype": "float64", "expression": "x"}]},
        {"fields": [{"name": "A", "dtype": "float64", "expression": "x", "code": "x"}]},
        {"fields": [{"name": "A", "dtype": "float64", "expression": "x"}] * 2},
        {"active_scalar": "Missing"},
        {"active_scalar": []},
        {"significant_digits": 0},
        {"significant_digits": 18},
        {"significant_digits": True},
        {"significant_digits": None},
        {"serialization": []},
        {"serialization": "script"},
        {"python": "print(1)"},
    ],
)
def test_invalid_contract(change):
    with pytest.raises(OperationError):
        validate("create_image_data", parameters(**change))


def test_contract_schema_and_payload_budgets():
    params = parameters(significant_digits=10, serialization="canonical_ascii_v1", active_scalar="Value")
    tools = json.loads(
        (Path(__file__).parents[1] / "src/dcc_mcp_paraview/skills/paraview-pipeline/tools.yaml").read_text()
    )["tools"]
    tool = next(tool for tool in tools if tool["name"] == "create_image_data")
    jsonschema.validate(params, tool["input_schema"])
    assert tool["input_schema"]["properties"]["fields"]["items"]["additionalProperties"] is False
    assert tool["execution"] == "async" and tool["affinity"] == "main"
    assert validate("create_image_data", params) is params
    plan = plan_image_data(params)
    assert plan["points"] == 12 and plan["cells"] == 2 and plan["array_bytes"] == 96
    assert plan["bounds"] == [-1, -0.5, 0, 2, 2, 4]
    with pytest.raises(ImageDataError) as error:
        plan_image_data(
            parameters(
                dimensions=[100, 100, 100],
                fields=[{"name": "F" + str(i), "dtype": "float64", "expression": "x"} for i in range(3)],
            )
        )
    assert error.value.code == "resource_limit"


class Array:
    def __init__(self, name, values):
        self.name, self.values = name, values
        self.dtype, self.components = 11, 1

    def GetName(self):
        return self.name

    def GetDataType(self):
        return self.dtype

    def GetNumberOfComponents(self):
        return self.components

    def GetNumberOfTuples(self):
        return len(self.values)

    def GetValue(self, index):
        return self.values[index]


class Image:
    def __init__(self, params):
        self.params = params
        self.plan = plan_image_data(params)
        self.arrays = []
        for field, expression in zip(params["fields"], self.plan["plans"]):
            values = [
                evaluate_expression(
                    expression,
                    tuple(o + i * s for o, i, s in zip(params["origin"], (x, y, z), params["spacing"])),
                    params.get("significant_digits"),
                )
                for z in range(params["dimensions"][2])
                for y in range(params["dimensions"][1])
                for x in range(params["dimensions"][0])
            ]
            self.arrays.append(Array(field["name"], values))

    def IsA(self, kind):
        return kind == "vtkImageData"

    def GetDimensions(self):
        return self.params["dimensions"]

    def GetOrigin(self):
        return [float(x) for x in self.params["origin"]]

    def GetSpacing(self):
        return [float(x) for x in self.params["spacing"]]

    def GetNumberOfPoints(self):
        return self.plan["points"]

    def GetNumberOfCells(self):
        return self.plan["cells"]

    def GetBounds(self):
        return self.plan["bounds"]

    def GetPointData(self):
        return self

    def GetScalars(self):
        return next((a for a in self.arrays if a.name == self.plan["active_scalar"]), None)

    def GetNumberOfArrays(self):
        return len(self.arrays)

    def GetArray(self, index):
        return self.arrays[index]


def test_full_native_shaped_evidence_checks_every_value(host):
    params = parameters()
    image = Image(params)
    plan = plan_image_data(params)
    result = host._image_data_evidence(image, params, plan)
    expected_hash = hashlib.sha256(b"".join(struct.pack("<d", v) for v in image.arrays[0].values)).hexdigest()
    assert result["arrays"][0]["sha256_le_float64"] == expected_hash
    altered = copy.deepcopy(image)
    altered.arrays[0].values[3] += 0.001
    with pytest.raises(OperationError, match="array value"):
        host._image_data_evidence(altered, params, plan, reference=image)


@pytest.mark.parametrize("change", ["dtype", "components", "nan", "tuples", "name", "geometry"])
def test_native_shaped_readback_rejects_mismatches(host, change):
    params = parameters()
    image = Image(params)
    if change in ("dtype", "components", "name"):
        setattr(image.arrays[0], change, {"dtype": 10, "components": 2, "name": "Wrong"}[change])
    elif change == "nan":
        image.arrays[0].values[2] = float("nan")
    elif change == "tuples":
        image.arrays[0].values.pop()
    else:
        image.params["dimensions"] = [3, 2, 2]
    with pytest.raises(OperationError):
        host._image_data_evidence(image, parameters(), plan_image_data(parameters()))


def test_generic_canonical_ascii_profile_golden(host, tmp_path):
    params = parameters(
        dimensions=[2, 2, 2],
        origin=[0, 0, 0],
        spacing=[0.5, 1, 2],
        significant_digits=3,
        fields=[
            {"name": "B", "dtype": "float64", "expression": "x+y+z"},
            {"name": "A", "dtype": "float64", "expression": "exp(-x)"},
        ],
        active_scalar="A",
    )
    image = Image(params)
    target = tmp_path / "profile.vti"
    host._write_canonical_image_data(target, image, params, plan_image_data(params))
    assert target.read_bytes() == (
        b'<?xml version="1.0"?>\n<VTKFile type="ImageData" version="0.1" byte_order="LittleEndian">\n'
        b'<ImageData WholeExtent="0 1 0 1 0 1" Origin="0 0 0" Spacing="0.5 1 2">\n'
        b'<Piece Extent="0 1 0 1 0 1"><PointData Scalars="A">\n'
        b'<DataArray type="Float64" Name="B" NumberOfComponents="1" format="ascii">\n'
        b"0 0.5 1 1.5 2 2.5 3 3.5\n</DataArray>\n"
        b'<DataArray type="Float64" Name="A" NumberOfComponents="1" format="ascii">\n'
        b"1 0.607 1 0.607 1 0.607 1 0.607\n</DataArray>\n"
        b"</PointData><CellData/></Piece></ImageData></VTKFile>\n"
    )


@pytest.mark.parametrize("value", ["../outside.vti", "missing/out.vti", "alias/out.vti", "bad.vtp"])
def test_anchored_output_rejects_paths(tmp_path, value):
    (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ImageDataError):
        ImageOutput(tmp_path, value, "a" * 32)


def test_anchored_directory_replacement_never_writes_through_symlink(tmp_path):
    folder = tmp_path / "destination"
    folder.mkdir()
    publication = ImageOutput(tmp_path, "destination/result.vti", "a" * 32)
    old = tmp_path / "old"
    folder.rename(old)
    outside = tmp_path / "other"
    outside.mkdir()
    folder.symlink_to(outside, target_is_directory=True)
    try:
        publication.reserve().write_text("verified")
        result = publication.publish(lambda p: {"checked": True})
        assert result["checked"] is True
        assert (old / "result.vti").read_text() == "verified"
        assert list(outside.iterdir()) == []
    finally:
        publication.close()
    assert [p.name for p in old.iterdir()] == ["result.vti"]


def test_staging_failure_cleanup_and_no_clobber(tmp_path):
    target = tmp_path / "result.vti"
    output = ImageOutput(tmp_path, target, "b" * 32)
    try:
        output.reserve(native=True).write_text("native")
        output.reserve().write_text("candidate")
        target.write_text("other writer")
        with pytest.raises(ImageDataError) as error:
            output.publish(lambda p: {})
        assert error.value.code == "output_exists"
    finally:
        output.close()
    assert target.read_text() == "other writer"
    assert not list(tmp_path.glob(".dcc-mcp-image-*"))


def test_preexisting_stages_are_never_cleaned(tmp_path):
    stage = tmp_path / (".dcc-mcp-image-" + "a" * 32 + ".native.vti")
    stage.write_text("not ours")
    with pytest.raises(ImageDataError):
        ImageOutput(tmp_path, "result.vti", "a" * 32)
    assert stage.read_text() == "not ours"


def test_direct_host_preflight_occurs_before_native_import_or_mutation(host):
    host.thread_id = threading.get_ident()
    calls = []
    host.pv = SimpleNamespace(FindSource=lambda n: calls.append(n))
    with pytest.raises(OperationError):
        host.call(
            "create_image_data", parameters(fields=[{"name": "V", "dtype": "float64", "expression": "x.__class__"}])
        )
    assert calls == []


def test_arithmetic_budget_separately_from_output_budget():
    expression = "x"
    for _ in range(6):
        expression = "(" + expression + "+x*x*x)"
    params = parameters(
        dimensions=[100, 100, 100],
        fields=[
            {"name": "CostA", "dtype": "float64", "expression": expression},
            {"name": "CostB", "dtype": "float64", "expression": expression},
        ],
    )
    assert len(parse_expression(expression)) * 2 > 64
    with pytest.raises(ImageDataError) as error:
        plan_image_data(params)
    assert error.value.code == "resource_limit"


def test_no_collapsed_grid_coordinates_at_large_origins():
    with pytest.raises(ImageDataError):
        plan_image_data(parameters(dimensions=[50, 2, 2], origin=[999999, 0, 0], spacing=[1e-11, 1, 1]))


def test_stage_path_replacement_cannot_publish_unverified_file(tmp_path):
    output = ImageOutput(tmp_path, "result.vti", "a" * 32)
    try:
        output.reserve().write_text("verified open file")
        stage = tmp_path / output.names[1]
        stage.rename(tmp_path / "original-stage")
        stage.symlink_to(tmp_path / "other.vti")
        (tmp_path / "other.vti").write_text("unverified")
        result = output.publish(lambda p: {"checked": p.read_text() == "verified open file"})
        assert result["checked"] is True
        assert (tmp_path / "result.vti").read_text() == "verified open file"
        assert not (tmp_path / "result.vti").is_symlink()
    finally:
        output.close()
