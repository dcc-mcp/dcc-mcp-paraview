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
