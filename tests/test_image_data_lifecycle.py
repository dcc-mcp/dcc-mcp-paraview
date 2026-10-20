"""Host-free process and controlled native-double lifecycle checks, not ParaView acceptance."""

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from dcc_mcp_core.cancellation import CancelToken, DccMcpCancelledError, reset_cancel_token, set_cancel_token
from test_host_files import host as host
from test_image_data import Array, Image, parameters

from dcc_mcp_paraview.bridge import ParaViewSession
from dcc_mcp_paraview.contracts import OperationError
from dcc_mcp_paraview.image_data import plan_image_data


def staged_fake_process(tmp_path, published=False):
    executable = tmp_path / "fake-image-host"
    executable.write_text(
        "#!" + sys.executable + "\nimport json,sys,time,pathlib,os\n"
        "print(json.dumps({'ready':True,'pid':os.getpid(),'version':'5.13.2','protocol':1}),flush=True)\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        " folder=pathlib.Path(r['params']['path']).parent\n"
        " for suffix in ['.native.vti','.vti']:\n"
        "  (folder/('.dcc-mcp-image-'+r['id']+suffix)).write_text('partial native output')\n"
        + (" pathlib.Path(r['params']['path']).write_text('published output')\n" if published else "")
        + " time.sleep(10)\n"
    )
    executable.chmod(0o700)
    return executable


@pytest.mark.parametrize("published", [False, True])
def test_timeout_removes_only_owned_image_stages(tmp_path, published):
    executable = staged_fake_process(tmp_path, published)
    session = ParaViewSession(tmp_path, executable=str(executable), timeout=0.25)
    unrelated = tmp_path / ".dcc-mcp-image-unrelated.vti"
    unrelated.write_text("preserve")
    try:
        with pytest.raises(OperationError) as error:
            session.request("create_image_data", parameters(path=str(tmp_path / "volume.vti")))
        assert error.value.code == "host_timeout"
        assert session.process.poll() is not None
        assert list(tmp_path.glob(".dcc-mcp-image-*.native.vti")) == []
        assert list(tmp_path.glob(".dcc-mcp-image-*.vti")) == [unrelated]
        assert (tmp_path / "volume.vti").exists() is published
    finally:
        session.close()


def test_cancellation_removes_stages_after_owned_host_stops(tmp_path):
    session = ParaViewSession(tmp_path, executable=str(staged_fake_process(tmp_path)), timeout=3)
    token = CancelToken(job_id="image-data-cancel-test")
    reset = set_cancel_token(token)
    timer = threading.Timer(0.25, token.cancel)
    try:
        session.start()
        timer.start()
        with pytest.raises(DccMcpCancelledError):
            session.request("create_image_data", parameters(path=str(tmp_path / "volume.vti")))
        assert session.last_request["job_id"] == "image-data-cancel-test"
        assert session.process.poll() is not None
        assert not list(tmp_path.glob(".dcc-mcp-image-*.vti"))
        time.sleep(0.1)
        assert not (tmp_path / "volume.vti").exists()
    finally:
        timer.join()
        reset_cancel_token(reset)
        session.close()


@pytest.fixture
def native_doubles(host, monkeypatch):
    """Minimal native-shaped data objects; nothing imports or starts real VTK/ParaView."""
    state = SimpleNamespace(failure=None, reads=0, writes=0, final=None)

    class MutableArray(Array):
        def __init__(self):
            super().__init__("", [])

        def SetName(self, value):
            self.name = value

        def SetNumberOfComponents(self, value):
            self.components = value

        def SetNumberOfTuples(self, count):
            self.values = [0.0] * count

        def SetValue(self, index, value):
            self.values[index] = value

        def GetRange(self):
            return min(self.values), max(self.values)

    class MutableImage(Image):
        def __init__(self):
            self.params = {"fields": []}
            self.arrays = []

        def SetDimensions(self, *value):
            self.params["dimensions"] = list(value)

        def SetOrigin(self, *value):
            self.params["origin"] = list(value)

        def SetSpacing(self, *value):
            self.params["spacing"] = list(value)

        def AddArray(self, value):
            self.arrays.append(value)
            self.params["fields"].append({"name": value.name, "dtype": "float64", "expression": "x"})

        def SetActiveScalars(self, value):
            self.params["active_scalar"] = value
            self.plan = plan_image_data(self.params)

    def read(path):
        state.reads += 1
        payload = json.loads(Path(path).read_text())
        image = MutableImage()
        image.params, image.plan = payload["params"], plan_image_data(payload["params"])
        image.arrays = [MutableArray() for _ in payload["arrays"]]
        for array, value in zip(image.arrays, payload["arrays"]):
            array.name, array.values = value["name"], value["values"]
        if state.failure == "readback":
            image.arrays[0].values[0] += 1
        return image

    class Writer:
        def SetFileName(self, value):
            self.path = value

        def SetInputData(self, value):
            self.image = value

        def SetDataModeToBinary(self):
            pass

        def SetCompressorTypeToNone(self):
            pass

        def GetErrorCode(self):
            return 0

        def Write(self):
            state.writes += 1
            Path(self.path).write_text(
                json.dumps(
                    {
                        "params": self.image.params,
                        "arrays": [{"name": a.name, "values": a.values} for a in self.image.arrays],
                    }
                )
            )
            if state.failure == "write":
                raise RuntimeError("controlled native write failure")
            return 1

    class Reader:
        def SetFileName(self, value):
            self.path = value

        def Update(self):
            self.image = read(self.path)

        def GetErrorCode(self):
            return 0

        def GetOutput(self):
            return self.image

    class Pipeline:
        def __init__(self):
            self.sources = {}
            self.active = object()
            self.previous = self.active

        def GetActiveSource(self):
            return self.active

        def SetActiveSource(self, value):
            self.active = value

        def GetSources(self):
            return {(name, "1"): value for name, value in self.sources.items()}

        def FindSource(self, name):
            return self.sources.get(name)

        def Delete(self, value):
            self.sources = {k: v for k, v in self.sources.items() if v is not value}

        def XMLImageDataReader(self, FileName, registrationName=None):
            image = read(FileName[0])
            proxy = SimpleNamespace(
                FileName=FileName,
                image=image,
                SMProxy=SimpleNamespace(GetXMLName=lambda: "XMLImageDataReader"),
                UpdatePipeline=lambda: None,
                GetDataInformation=lambda: image,
                PointData={a.name: a for a in image.arrays},
            )
            self.sources[registrationName] = proxy
            self.active = proxy
            if state.failure == "registered_reader":
                raise RuntimeError("controlled registered reader failure")
            return proxy

    Pipeline.XMLPolyDataReader = Pipeline.XMLImageDataReader
    Pipeline.XMLUnstructuredGridReader = Pipeline.XMLImageDataReader
    host.pv = Pipeline()
    host.version = "5.13.2"
    host.thread_id = threading.get_ident()
    for name, value in {
        "vtkmodules.vtkCommonCore": SimpleNamespace(vtkDoubleArray=MutableArray),
        "vtkmodules.vtkCommonDataModel": SimpleNamespace(vtkImageData=MutableImage),
        "vtkmodules.vtkIOXML": SimpleNamespace(vtkXMLImageDataWriter=Writer, vtkXMLImageDataReader=Reader),
        "paraview": SimpleNamespace(servermanager=SimpleNamespace(Fetch=lambda proxy: proxy.image)),
    }.items():
        monkeypatch.setitem(sys.modules, name, value)
    return state


def test_controlled_creation_is_reader_backed_and_restores_active(host, native_doubles, tmp_path):
    result = host.call("create_image_data", parameters())
    assert result["source"]["type"] == "XMLImageDataReader"
    assert result["artifact"]["all_values_equal"] is True
    assert result["image_data"]["arrays"][0]["dtype"] == "float64"
    assert native_doubles.writes == 1 and native_doubles.reads == 3
    assert list(host.pv.sources) == ["Volume"]
    assert host.pv.active is host.pv.previous
    assert (tmp_path / "volume.vti").is_file()
    assert not list(tmp_path.glob(".dcc-mcp-image-*.vti"))


@pytest.mark.parametrize("failure", ["write", "readback", "registered_reader", "numeric"])
def test_controlled_failures_remove_new_proxy_and_stages(host, native_doubles, tmp_path, failure):
    native_doubles.failure = failure
    params = parameters()
    if failure == "numeric":
        params["fields"][0]["expression"] = "1/(x-x)"
    with pytest.raises((RuntimeError, OperationError)):
        host.call("create_image_data", params)
    assert host.pv.sources == {}
    assert host.pv.active is host.pv.previous
    assert not list(tmp_path.glob(".dcc-mcp-image-*.vti"))
    # A final reader failure happens after verified publication; no rollback is promised.
    assert (tmp_path / "volume.vti").exists() is (failure == "registered_reader")


def test_rejected_file_preflight_keeps_healthy_session(tmp_path):
    session = ParaViewSession(tmp_path, executable=str(staged_fake_process(tmp_path)), timeout=1)
    (tmp_path / "volume.vti").write_text("existing output")
    try:
        session.start()
        with pytest.raises(OperationError) as error:
            session.request("create_image_data", parameters(path=str(tmp_path / "volume.vti")))
        assert error.value.code == "output_exists"
        assert session.process.poll() is None
        assert session._closed is False
        assert not list(tmp_path.glob(".dcc-mcp-image-*.vti"))
    finally:
        session.close()


@pytest.mark.parametrize(
    "expression, origin",
    [
        ("(x**8+x)-x**8", [999998, 0, 0]),
        ("x*x*x*x-x**4", [999998, 0, 0]),
        ("-x", [0, 0, 0]),
    ],
)
def test_controlled_integer_and_float_requests_match_independent_float64_oracle(
    host,
    native_doubles,
    expression,
    origin,
):
    import hashlib
    import struct

    results = []
    for index, number in enumerate((int, float)):
        params = parameters(
            name="Volume" + str(index),
            path="volume%d.vti" % index,
            dimensions=[2, 2, 2],
            origin=[number(value) for value in origin],
            spacing=[number(1)] * 3,
            fields=[{"name": "Value", "dtype": "float64", "expression": expression}],
        )
        results.append(host.call("create_image_data", params))
    expected = hashlib.sha256()
    for _z in range(2):
        for _y in range(2):
            for ix in range(2):
                x = float(origin[0]) + ix * 1.0
                if expression == "(x**8+x)-x**8":
                    value = (x**8 + x) - x**8
                elif expression == "x*x*x*x-x**4":
                    value = x * x * x * x - x**4
                else:
                    value = -x
                expected.update(struct.pack("<d", value))
    assert all(result["image_data"]["arrays"][0]["sha256_le_float64"] == expected.hexdigest() for result in results)
    assert all(type(value) is float for value in results[0]["image_data"]["origin"])


def test_host_rejects_mismatched_directory_before_native_allocation(host, native_doubles, tmp_path):
    from dcc_mcp_paraview.image_files import ImageOutput

    parent = tmp_path / "destination"
    parent.mkdir()
    sidecar = ImageOutput(tmp_path, "destination/volume.vti", "c" * 32)
    host.output_directory = sidecar.directory_identity
    host.request_id = "c" * 32
    parent.rename(tmp_path / "old-destination")
    parent.mkdir()
    try:
        with pytest.raises(OperationError) as error:
            host.call("create_image_data", parameters(path="destination/volume.vti"))
        assert error.value.code == "path_denied"
        assert native_doubles.reads == native_doubles.writes == 0
        assert host.pv.sources == {}
        assert not list(tmp_path.glob("**/.dcc-mcp-image-*.vti"))
    finally:
        sidecar.close()


def test_bridge_sends_its_pinned_directory_identity(tmp_path, monkeypatch):
    session = ParaViewSession(tmp_path, executable=sys.executable)
    sent = []
    monkeypatch.setattr(session, "start", lambda: None)
    monkeypatch.setattr(session, "_write_frame", lambda frame, deadline: sent.append(json.loads(frame)))
    monkeypatch.setattr(
        session,
        "_read_frame",
        lambda deadline: {
            "id": sent[-1]["id"],
            "job_id": sent[-1]["job_id"],
            "result": {},
        },
    )
    try:
        session.request("create_image_data", parameters())
        metadata = tmp_path.stat()
        assert sent[0]["output_directory"] == {"device": metadata.st_dev, "inode": metadata.st_ino}
        assert set(sent[0]) == {"id", "job_id", "operation", "params", "output_directory"}
        assert "output_directory" not in sent[0]["params"]
        assert session._image_output is None
    finally:
        session.close()


@pytest.mark.parametrize(
    "identity",
    [
        None,
        {},
        {"device": True, "inode": 1},
        {"device": 1, "inode": 0},
        {"device": -1, "inode": 2},
        {"device": 1, "inode": 2, "path": "/outside"},
        {"device": 1, "inode": 2},
    ],
)
def test_host_wire_requires_typed_image_directory_identity(tmp_path, monkeypatch, identity):
    import importlib.util
    import io

    import dcc_mcp_paraview
    from dcc_mcp_paraview import contracts

    monkeypatch.setitem(sys.modules, "contracts", contracts)
    path = Path(dcc_mcp_paraview.__file__).with_name("host_driver.py")
    spec = importlib.util.spec_from_file_location("paraview_image_wire_test", path)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    calls = []

    class ControlledHost:
        version = "5.13.2"

        def __init__(self, root):
            pass

        def call(self, operation, params):
            calls.append((operation, self.output_directory))
            return {}

    request = {"id": "d" * 32, "job_id": None, "operation": "create_image_data", "params": parameters()}
    if identity is not None:
        request["output_directory"] = identity
    incoming, wire = io.BytesIO((json.dumps(request) + "\n").encode()), io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(driver, "Host", ControlledHost)
        patch.setattr(driver.sys, "argv", [str(path), str(tmp_path)])
        patch.setattr(driver.sys, "stdout", sys.stdout)
        patch.setattr(driver.os, "dup", lambda descriptor: descriptor)
        patch.setattr(driver.os, "fdopen", lambda descriptor, mode, **kwargs: wire if mode == "w" else incoming)
        driver.main()
    valid = identity == {"device": 1, "inode": 2} and type(identity.get("device")) is int
    assert calls == ([("create_image_data", identity)] if valid else [])
    assert len(wire.getvalue().splitlines()) == (2 if valid else 1)
