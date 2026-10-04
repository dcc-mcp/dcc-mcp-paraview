"""Controlled native-proxy fixtures; these are not graphical/native acceptance."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from test_host_files import host as host

from dcc_mcp_paraview.contracts import MAX_SLICE_CELLS, MAX_SLICE_POINTS, OperationError, validate


class Property:
    def __init__(self, value):
        self.value = value
        self.SMProperty = self

    def GetElement(self, index):
        return self.value[index] if isinstance(self.value, list) else self.value

    def GetData(self):
        return self.value

    def GetNumberOfElements(self):
        return len(self.value) if isinstance(self.value, list) else 1

    def FindDomain(self, kind):
        return None

    def GetNumberOfProxies(self):
        return len(self.value)

    def GetProxy(self, index):
        return self.value[index].SMProxy

    def GetOutputPortForConnection(self, index):
        return 0


class Proxy:
    def __init__(self, kind, **properties):
        self.SMProxy = SimpleNamespace(GetXMLName=lambda: kind)
        self.__dict__.update(properties)

    def GetProperty(self, name):
        return Property(getattr(self, name)) if name in self.__dict__ else None

    def __setattr__(self, name, value):
        if name == "SliceType" and value == "Plane":
            value = SimpleNamespace(Origin=[0, 0, 0], Normal=[1, 0, 0])
        object.__setattr__(self, name, value)


class Data:
    def __init__(self, points=49, cells=72, data_type=0):
        self.points, self.cells, self.data_type = points, cells, data_type

    def GetNumberOfPoints(self):
        return self.points

    def GetNumberOfCells(self):
        return self.cells

    def GetBounds(self):
        return [0, 0, -3, 3, -3, 3]

    def GetDataSetType(self):
        return self.data_type


def source(kind="XMLImageDataReader", data=None, arrays=None):
    return Proxy(
        kind,
        UpdatePipeline=lambda: None,
        GetDataInformation=lambda: data or Data(),
        PointData=arrays
        if arrays is not None
        else {"RadiusSquared": SimpleNamespace(GetNumberOfComponents=lambda: 1, GetRange=lambda: [0, 18])},
    )


class Pipeline:
    def __init__(self, input_source=None):
        self.sources = {"Volume": input_source or source()}
        self.active = self.sources["Volume"]
        self.created = 0
        self.next_output = source("Cut")

    def GetSources(self):
        return {(name, str(id(proxy))): proxy for name, proxy in self.sources.items()}

    def FindSource(self, name):
        return self.sources.get(name)

    def GetActiveSource(self):
        return self.active

    def SetActiveSource(self, proxy):
        self.active = proxy

    def Slice(self, registrationName, Input):
        assert Input is self.sources["Volume"]
        self.created += 1
        self.next_output.Input = [Input]
        self.sources[registrationName] = self.next_output
        self.active = self.next_output
        return self.next_output

    def Delete(self, proxy):
        self.sources = {key: value for key, value in self.sources.items() if value is not proxy}


@pytest.mark.parametrize(
    "params",
    [
        {"normal": [0, 0, 0]},
        {"normal": [0, True, 1]},
        {"normal": [0, 1]},
        {"origin": [float("nan"), 0, 0]},
        {"origin": [0, float("inf"), 1]},
        {"origin": [0, 0, 1000001]},
        {"invert": True},
        {"name": "Volume"},
        {"input_name": "Missing"},
    ],
)
def test_slice_preflight_does_not_create(host, params):
    host.pv = Pipeline()
    with pytest.raises(OperationError):
        host.call("slice_plane", {"name": "Section", "input_name": "Volume", **params})
    assert host.pv.created == 0
    assert list(host.pv.sources) == ["Volume"]


@pytest.fixture(autouse=True)
def host_thread(host):
    import threading

    host.thread_id = threading.get_ident()


def test_slice_readback_and_active_source_preserved(host):
    host.pv = Pipeline()
    active = host.pv.active
    result = host.call("slice_plane", {"name": "Section", "input_name": "Volume"})["source"]
    assert result["type"] == "Cut" and result["crinkle"] is False and result["triangulate"] is True
    assert result["points"] == 49 and result["cells"] == 72 and result["bounds"] == [0, 0, -3, 3, -3, 3]
    assert result["origin"] == [0, 0, 0] and result["normal"] == [1, 0, 0]
    assert result["point_arrays"] == [{"name": "RadiusSquared", "components": 1, "range": [0, 18]}]
    assert result["input_name"] == "Volume" and host.pv.active is active


@pytest.mark.parametrize(
    "output,code",
    [
        (source("Cut", Data(points=MAX_SLICE_POINTS + 1)), "resource_limit"),
        (source("Cut", Data(cells=MAX_SLICE_CELLS + 1)), "resource_limit"),
        (source("Cut", Data(points=0, cells=0)), "empty_slice"),
        (source("Cut", Data(data_type=4)), "verification_failed"),
        (source("Cut", arrays={}), "verification_failed"),
    ],
)
def test_slice_failed_readback_rolls_back_and_recovers(host, output, code):
    host.pv = Pipeline()
    active = host.pv.active
    host.pv.next_output = output
    with pytest.raises(OperationError) as error:
        host.slice_plane("Section", "Volume")
    assert error.value.code == code
    assert list(host.pv.sources) == ["Volume"] and host.pv.active is active
    host.pv.next_output = source("Cut")
    assert host.slice_plane("Section", "Volume")["source"]["cells"] == 72


def test_slice_capacity_and_input_limit_are_preflight(host):
    host.pv = Pipeline(source(data=Data(points=MAX_SLICE_POINTS + 1)))
    with pytest.raises(OperationError, match="input exceeds"):
        host.slice_plane("Section", "Volume")
    host.pv = Pipeline()
    host.pv.sources.update({"Extra%d" % i: source() for i in range(31)})
    with pytest.raises(OperationError, match="32 sources"):
        host.slice_plane("Section", "Volume")
    assert host.pv.created == 0


def test_slice_constructor_registration_failure_rolls_back(host):
    host.pv = Pipeline()
    native_slice = host.pv.Slice

    def failure(**kwargs):
        native_slice(**kwargs)
        raise RuntimeError("constructor failed after registration")

    host.pv.Slice = failure
    with pytest.raises(RuntimeError):
        host.slice_plane("Section", "Volume")
    assert list(host.pv.sources) == ["Volume"]
    assert host.pv.active is host.pv.sources["Volume"]


def presentation(host, legend=True):
    volume = source()
    lut = Proxy("PVLookupTable", RGBPoints=[0, 0.1, 0.2, 0.3, 18, 0.8, 0.9, 1], ColorSpace="RGB")
    rep = Proxy(
        "GeometryRepresentation",
        Input=[volume],
        Visibility=1,
        Representation="Surface",
        ColorArrayName=["POINTS", "RadiusSquared"],
        LookupTable=lut,
        Ambient=0.2,
        Diffuse=0.7,
        Specular=0.3,
        SpecularPower=32,
        Opacity=1,
    )
    bar = Proxy(
        "ScalarBarWidgetRepresentation",
        LookupTable=lut,
        Visibility=1,
        Title="RadiusSquared",
        ComponentTitle="",
        Position=[0.8, 0.1],
        Orientation="Vertical",
    )
    view = Proxy(
        "RenderView",
        Representations=[rep, bar] if legend else [rep],
        CameraPosition=[9, 7, 6],
        CameraFocalPoint=[0, 0, 0],
        CameraViewUp=[0, 0, 1],
        CameraParallelProjection=1,
        CameraParallelScale=3.2,
        CameraViewAngle=30,
        CameraClippingRange=[0.1, 100],
        ViewSize=[320, 240],
        Background=[0.1, 0.15, 0.2],
        OrientationAxesVisibility=0,
    )
    host.view = view
    host.pv = SimpleNamespace(GetSources=lambda: {("Volume", "12345"): volume}, GetViews=lambda: [view])
    return view, rep, bar


def test_inspect_is_read_only_without_creation_helpers(host):
    view, rep, bar = presentation(host)
    before = copy.deepcopy(view.__dict__)
    result = host.inspect_presentation()
    assert result == host.inspect_presentation()
    actual = result["views"][0]
    assert result["current_view_index"] == 0 and actual["camera_position"] == [9, 7, 6]
    assert actual["visible_bindings"] == [{"source_names": ["Volume"], "port": 0}]
    assert actual["scalar_bars"][0]["bindings"] == actual["visible_bindings"]
    assert actual["representations"][0]["palette"]["range"] == [0, 18]
    assert actual["representations"][0]["ambient"] == 0.2
    assert actual["camera_clipping_range"] == [0.1, 100]
    assert actual["unavailable_properties"] == []
    assert "12345" not in json.dumps(result)
    assert view.CameraPosition == before["CameraPosition"]
    assert view.Representations == [rep, bar]
    # Regenerated proxy identities after state load do not change the presentation.
    presentation(host)
    assert host.inspect_presentation() == result


def test_inspect_empty_and_absent_legend_do_not_create(host):
    host.view = None
    host.pv = SimpleNamespace(GetSources=lambda: {}, GetViews=lambda: [])
    assert host.inspect_presentation() == {"views": [], "current_view_index": None}
    presentation(host, legend=False)
    assert host.inspect_presentation()["views"][0]["scalar_bars"] == []


@pytest.mark.parametrize("change", ["camera", "clipping", "visibility", "palette", "legend"])
def test_capture_changed_state_is_not_repaired_or_published(host, tmp_path, monkeypatch, change):
    monkeypatch.setenv("DISPLAY", ":controlled-test")
    view, rep, bar = presentation(host)

    def screenshot(path, captured_view):
        assert captured_view is view
        Path(path).write_bytes(b"staged image")
        if change == "camera":
            view.CameraPosition = [1, 2, 3]
        elif change == "clipping":
            view.CameraClippingRange = [1, 4]
        elif change == "visibility":
            rep.Visibility = 0
        elif change == "palette":
            rep.LookupTable.RGBPoints[0] = -1
        else:
            bar.Visibility = 0

    host.pv.SaveScreenshot = screenshot
    with pytest.raises(OperationError) as error:
        host.capture_current_view("snapshot.png")
    assert error.value.code == "presentation_changed"
    assert not list(tmp_path.glob("*.png")) and not list(tmp_path.glob(".*.png"))
    if change == "camera":
        assert view.CameraPosition == [1, 2, 3]


def test_capture_verifies_before_atomic_no_clobber_publication(host, tmp_path, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":controlled-test")
    presentation(host)
    host.pv.SaveScreenshot = lambda path, view: Path(path).write_bytes(b"verified by fixture")
    host._verify_png = lambda path, dimensions: {"dimensions": dimensions, "pixel_ranges": [[0, 255]] * 3}
    before = host.inspect_presentation()
    artifact = host.capture_current_view("snapshot.png")["artifact"]
    assert artifact["presentation"] == before and artifact["presentation_unchanged"] is True
    assert artifact["dimensions"] == [320, 240] and (tmp_path / "snapshot.png").exists()
    with pytest.raises(OperationError) as error:
        host.capture_current_view("snapshot.png")
    assert error.value.code == "output_exists"
    assert host.inspect_presentation() == before
    assert list(tmp_path.glob(".*.png")) == []


def test_capture_concurrent_output_and_decoder_failure_preserve_cleanup(host, tmp_path, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":controlled-test")
    presentation(host)
    host.pv.SaveScreenshot = lambda path, view: Path(path).write_bytes(b"candidate")

    def fail(path, dimensions):
        raise OperationError("verification_failed", "invalid native pixels")

    host._verify_png = fail
    with pytest.raises(OperationError):
        host.capture_current_view("snapshot.png")
    assert not (tmp_path / "snapshot.png").exists()

    def concurrent(path, dimensions):
        (tmp_path / "snapshot.png").write_bytes(b"concurrent winner")
        return {}

    host._verify_png = concurrent
    with pytest.raises(OperationError) as error:
        host.capture_current_view("snapshot.png")
    assert error.value.code == "output_exists"
    assert (tmp_path / "snapshot.png").read_bytes() == b"concurrent winner"
    assert list(tmp_path.glob(".*.png")) == []


@pytest.mark.parametrize(
    "operation,params",
    [
        ("inspect_presentation", {}),
        ("capture_current_view", {"path": "image.png"}),
        ("slice_plane", {"name": "Section", "input_name": "Volume", "normal": [0, 0, 1e-8]}),
    ],
)
def test_new_schema_parity(operation, params):
    from test_contracts import SKILL

    tools = json.loads((SKILL / "tools.yaml").read_text())["tools"]
    tool = next(t for t in tools if t["name"] == operation)
    jsonschema.validate(params, tool["input_schema"])
    assert validate(operation, params) == params
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**params, "unexpected": 1}, tool["input_schema"])
    with pytest.raises(OperationError):
        validate(operation, {**params, "unexpected": 1})


def test_missing_clipping_property_is_explicit_without_camera_creation(host):
    view, _, _ = presentation(host)
    del view.CameraClippingRange
    actual = host.inspect_presentation()["views"][0]
    assert "camera_clipping_range" not in actual
    assert actual["unavailable_properties"] == ["camera_clipping_range"]


class RawNativeProperty:
    """vtkSMVectorProperty-shaped; no GetData, SMProperty or Python sequence API."""

    def __init__(self, values, entries=()):
        self.values = values
        self.entries = entries

    def GetNumberOfElements(self):
        return len(self.values)

    def GetElement(self, index):
        return self.values[index]

    def FindDomain(self, name):
        assert name == "vtkSMEnumerationDomain"
        if not self.entries:
            return None
        return SimpleNamespace(
            GetNumberOfEntries=lambda: len(self.entries),
            GetEntryValue=lambda i: self.entries[i][0],
            GetEntryText=lambda i: self.entries[i][1],
        )


class PropertyOnlyProxy:
    """Existing SM properties may lack the equivalent Python attribute accessor."""

    def __init__(self, properties):
        self.properties = properties

    def GetProperty(self, name):
        return self.properties.get(name)

    def __getattr__(self, name):
        raise AssertionError("Direct proxy attribute access is forbidden: " + name)


@pytest.mark.parametrize("raw", [True, False])
def test_palette_reads_existing_raw_or_hidden_wrapped_properties(host, raw):
    properties = {
        "RGBPoints": RawNativeProperty([0, 0.1, 0.2, 0.3, 18, 0.8, 0.9, 1]),
        "ColorSpace": RawNativeProperty([1], [(0, "RGB"), (1, "HSV")]),
        "IndexedLookup": RawNativeProperty([1]),
        "UseLogScale": RawNativeProperty([0]),
        "VectorComponent": RawNativeProperty([2]),
        "VectorMode": RawNativeProperty([0], [(0, "Magnitude"), (1, "Component")]),
    }
    if not raw:
        properties = {
            name: Property(prop.values if len(prop.values) > 1 else prop.values[0]) for name, prop in properties.items()
        }
        properties["ColorSpace"] = Property("HSV")
        properties["VectorMode"] = Property("Magnitude")
    proxy = PropertyOnlyProxy(properties)
    palette = host._palette(proxy)
    assert palette == {
        "rgb_points": [0, 0.1, 0.2, 0.3, 18, 0.8, 0.9, 1],
        "range": [0, 18],
        "color_space": "HSV",
        "indexed_lookup": True,
        "use_log_scale": False,
        "vector_component": 2.0,
        "vector_mode": "Magnitude",
    }


def test_wrapped_array_selection_keeps_semantic_association_and_name(host):
    class ArraySelection(RawNativeProperty):
        def GetData(self):
            return ["POINTS", self.GetElement(4)]

    proxy = PropertyOnlyProxy({"ColorArrayName": ArraySelection(["", "", "", "0", "RadiusSquared"])})
    assert host._presentation_properties(proxy, {"color_array": ("ColorArrayName", "vector")}) == {
        "color_array": ["POINTS", "RadiusSquared"]
    }


def test_raw_property_limit_is_checked_before_reading_elements(host):
    class TooMany(RawNativeProperty):
        def GetNumberOfElements(self):
            return 4097

        def GetElement(self, index):
            pytest.fail("Elements must not be read before the count limit")

    with pytest.raises(OperationError) as error:
        host._palette(PropertyOnlyProxy({"RGBPoints": TooMany([])}))
    assert error.value.code == "resource_limit"


def test_empty_native_vector_and_invalid_scalar_cardinality(host):
    proxy = PropertyOnlyProxy({"CustomLabels": RawNativeProperty([]), "Visibility": RawNativeProperty([])})
    assert host._presentation_properties(proxy, {"labels": ("CustomLabels", "vector")}) == {"labels": []}
    with pytest.raises(OperationError) as error:
        host._presentation_properties(proxy, {"visible": ("Visibility", "bool")})
    assert error.value.code == "verification_failed"
