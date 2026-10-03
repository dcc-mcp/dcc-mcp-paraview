import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

from dcc_mcp_paraview.bridge import ParaViewSession
from dcc_mcp_paraview.contracts import OperationError

pytestmark = [pytest.mark.paraview, pytest.mark.skipif(not shutil.which("pvpython"), reason="pvpython not installed")]


@pytest.fixture
def session(tmp_path):
    host = ParaViewSession(tmp_path)
    yield host
    host.close()


def test_native_roundtrip_and_serial_main_thread(session, tmp_path):
    sphere = session.request("create_sphere", {"name": "Sphere"})
    assert sphere["source"]["radius"] == 1
    edit = session.request("edit_sphere", {"name": "Sphere", "radius": 1.25})
    assert edit["source"]["radius"] == 1.25
    clipped = session.request("clip_plane", {"name": "Clip", "input_name": "Sphere", "origin": [-0.2, 0, 0]})
    assert clipped["source"]["cells"] > 0
    export = session.request("export_dataset", {"name": "Clip", "path": "clip.vtu"})
    reopened = session.request("open_dataset", {"name": "Reopened", "path": "clip.vtu"})
    assert export["artifact"]["points"] == reopened["source"]["points"]
    saved = session.request("save_state", {"path": "scene.pvsm"})
    session.request("edit_sphere", {"name": "Sphere", "radius": 2})
    restored = session.request("reopen_state", {"path": "scene.pvsm"})
    assert restored["sources"] == saved["sources"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(lambda _: session.request("inspect_pipeline", {}), range(8)))
    assert len({r["host"]["pid"] for r in reports}) == 1
    assert all(r["host"]["thread_id"] == r["host"]["main_thread_id"] for r in reports)
    assert (tmp_path / "scene.pvsm").is_file()


def test_path_overwrite_untrusted_state_and_render_fail_closed(session, tmp_path):
    session.request("create_sphere", {"name": "Sphere"})
    session.request("export_dataset", {"name": "Sphere", "path": "sphere.vtp"})
    original = (tmp_path / "sphere.vtp").read_bytes()
    for op, params, code in [
        ("save_state", {"path": "../outside.pvsm"}, "path_denied"),
        ("export_dataset", {"name": "Sphere", "path": "sphere.vtp"}, "output_exists"),
        ("create_sphere", {"name": "Sphere"}, "name_exists"),
        ("edit_sphere", {"name": "Missing", "radius": 1}, "source_not_found"),
        ("render_preview", {"name": "Sphere", "path": "preview.png"}, "render_unavailable"),
    ]:
        with pytest.raises(OperationError) as error:
            session.request(op, params)
        assert error.value.code == code
    assert (tmp_path / "sphere.vtp").read_bytes() == original
    assert not (tmp_path / "preview.png").exists()
    session.request("save_state", {"path": "saved.pvsm"})
    (tmp_path / "saved.pvsm").write_text("altered")
    with pytest.raises(OperationError) as error:
        session.request("reopen_state", {"path": "saved.pvsm"})
    assert error.value.code == "untrusted_state"
    assert len(session.request("inspect_pipeline", {})["sources"]) == 1


def write_vti(path):
    values = " ".join(str(x * x + y * y + z * z) for z in range(-3, 4) for y in range(-3, 4) for x in range(-3, 4))
    path.write_text(
        '<VTKFile type="ImageData" version="0.1" byte_order="LittleEndian">'
        '<ImageData WholeExtent="0 6 0 6 0 6" Origin="-3 -3 -3" Spacing="1 1 1">'
        '<Piece Extent="0 6 0 6 0 6"><PointData Scalars="RadiusSquared">'
        '<DataArray type="Float32" Name="RadiusSquared" format="ascii">'
        + values
        + "</DataArray></PointData><CellData/></Piece></ImageData></VTKFile>"
    )


def test_vti_import_contour_export(session, tmp_path):
    write_vti(tmp_path / "volume.vti")
    source = session.request("open_dataset", {"name": "Volume", "path": "volume.vti"})
    assert source["source"]["points"] == 343
    contour = session.request(
        "contour", {"name": "Contour", "input_name": "Volume", "scalar": "RadiusSquared", "values": [4, 6]}
    )
    assert contour["source"]["points"] > 0
    exported = session.request("export_dataset", {"name": "Contour", "path": "contour.vtp"})
    assert exported["artifact"]["points"] == contour["source"]["points"]


def test_symlink_escape(session, tmp_path):
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(OperationError) as error:
        session.request("save_state", {"path": "escape/outside.pvsm"})
    assert error.value.code == "path_denied"


@pytest.mark.parametrize("kind", ["file", "directory", "dangling"])
def test_all_symlink_components_are_rejected(session, tmp_path, kind):
    session.request("create_sphere", {"name": "Sphere"})
    if kind == "directory":
        (tmp_path / "real").mkdir()
        (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
        path = "alias/output.pvsm"
    else:
        target = tmp_path / "real.pvsm"
        if kind == "file":
            target.write_text("preserve")
        (tmp_path / "alias.pvsm").symlink_to(target)
        path = "alias.pvsm"
    with pytest.raises(OperationError) as error:
        session.request("save_state", {"path": path})
    assert error.value.code == "path_denied"


def test_two_sessions_do_not_share_pipeline_or_trusted_state(session, tmp_path):
    other_root = tmp_path / "second"
    other_root.mkdir()
    other = ParaViewSession(other_root)
    try:
        session.request("create_sphere", {"name": "First"})
        assert other.request("inspect_pipeline", {})["sources"] == []
        other.request("create_sphere", {"name": "Second"})
        assert [x["name"] for x in session.request("inspect_pipeline", {})["sources"]] == ["First"]
        session.request("save_state", {"path": "first.pvsm"})
        (other_root / "copied.pvsm").write_bytes((tmp_path / "first.pvsm").read_bytes())
        with pytest.raises(OperationError) as error:
            other.request("reopen_state", {"path": "copied.pvsm"})
        assert error.value.code == "untrusted_state"
    finally:
        other.close()


def test_portable_native_state_and_verified_relocation(session, tmp_path):
    original = tmp_path / "original.vti"
    write_vti(original)
    package = tmp_path / "package"
    (package / "data").mkdir(parents=True)
    shutil.copy2(original, package / "data/original.vti")
    session.request("open_dataset", {"name": "Volume", "path": "original.vti"})
    session.request("contour", {"name": "Contour", "input_name": "Volume", "scalar": "RadiusSquared", "values": [4]})
    saved = session.request("save_state", {"path": "package/project.pvsm", "data_directory": "package/data"})
    raw = (package / "project.pvsm").read_bytes()
    assert saved["artifact"]["portable_relative_paths"] is True
    assert str(tmp_path).encode() not in raw
    assert b"data/original.vti" in raw
    assert session.request("inspect_pipeline", {})["sources"] == saved["sources"]
    relocated = tmp_path / "different-container/project"
    shutil.copytree(package, relocated)
    # Remove the original paths from native reach; they are only synthetic test artifacts.
    original.rename(tmp_path / "old-input-not-used.vti")
    package.rename(tmp_path / "old-package-not-used")
    restored = session.request(
        "reopen_state",
        {
            "path": "different-container/project/project.pvsm",
            "data_directory": "different-container/project/data",
        },
    )
    assert restored["relocation_verified"] is True
    assert restored["sources"] == saved["sources"]
    assert restored["reader_paths"] == {"Volume": str(relocated / "data/original.vti")}
    assert (relocated / "project.pvsm").read_bytes() == raw


@pytest.mark.parametrize("failure", ["different_bytes", "missing", "symlink", "outside", "modified_state"])
def test_relocation_fails_before_current_pipeline_reset(session, tmp_path, failure):
    write_vti(tmp_path / "original.vti")
    session.request("open_dataset", {"name": "Volume", "path": "original.vti"})
    saved = session.request("save_state", {"path": "original.pvsm"})
    (tmp_path / "relocated-data").mkdir()
    shutil.copy2(tmp_path / "original.vti", tmp_path / "relocated-data/original.vti")
    shutil.copy2(tmp_path / "original.pvsm", tmp_path / "copied.pvsm")
    args = {"path": "copied.pvsm", "data_directory": "relocated-data"}
    if failure == "different_bytes":
        (tmp_path / "relocated-data/original.vti").write_text("modified")
    elif failure == "missing":
        (tmp_path / "relocated-data/original.vti").unlink()
    elif failure == "symlink":
        (tmp_path / "alias").symlink_to(tmp_path / "relocated-data", target_is_directory=True)
        args["data_directory"] = "alias"
    elif failure == "outside":
        args["data_directory"] = "../outside"
    else:
        (tmp_path / "copied.pvsm").write_text("altered state")
    with pytest.raises(OperationError):
        session.request("reopen_state", args)
    assert session.request("inspect_pipeline", {})["sources"] == saved["sources"]


def test_portable_save_rejects_data_outside_package(session, tmp_path):
    write_vti(tmp_path / "original.vti")
    session.request("open_dataset", {"name": "Volume", "path": "original.vti"})
    (tmp_path / "package").mkdir()
    with pytest.raises(OperationError) as error:
        session.request("save_state", {"path": "package/project.pvsm", "data_directory": "."})
    assert error.value.code == "path_denied"
    assert not (tmp_path / "package/project.pvsm").exists()
