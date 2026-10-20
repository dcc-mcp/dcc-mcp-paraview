#!/usr/bin/env python3
"""One GitHub-hosted coarse ImageData/SectionY image fixture; offline candidate only.
No full historical fixture, original figures, local/self-hosted execution, installation,
X server launch, arbitrary host code or mutation replay. The workflow owns Xvfb.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import os
import re
import signal
import stat
import struct
import sys
import tempfile
import traceback
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

HELPER_SHA256 = "3eb290166e00d248040ba3a880bb3a19e7d6d042990d8b21c77b8d3a7be9a97d"
PARAMETERS_SHA256 = "abc4dabbb847c639b89d43a3a5356a5f8a474c40b9f6e2b6609b555978c5b255"
ARCHIVE_SHA256 = "815978cef3f02522c9cbb6d77b354aa3071a6c9808b0ce5954e337baf44e9329"
ARCHIVE_BYTES = 688873467
ARCHIVE_NAME = "ParaView-5.13.2-MPI-Linux-Python3.10-x86_64.tar.gz"
PRECHECK_RUN = "39483508573"


def load_fixture_support():
    directory = Path(__file__).resolve().parent
    helper = directory / "hosted_http_fixture_support.py"
    parameters = directory / "hosted_coarse_parameters.json"
    for path, digest in ((helper, HELPER_SHA256), (parameters, PARAMETERS_SHA256)):
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Reviewed fixture companion hash mismatch: " + str(path))
    # Fixed package-owned fixture module, hash-verified before loading. No user code.
    spec = importlib.util.spec_from_file_location("_owned_hosted_fixture_support", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, json.loads(parameters.read_text())


def artifact(h, workspace, name, context, limit=4 * 1024 * 1024):
    path = workspace / name
    report = context["artifact"]
    h.require(
        report["path"] == str(path) and stat.S_ISREG(path.lstat().st_mode) and not path.is_symlink(),
        "Artifact path/type differs",
    )
    h.require(
        0 < path.stat().st_size <= limit and report["bytes"] == path.stat().st_size, "Artifact byte budget differs"
    )
    digest = h.sha256_file(path)
    h.require(report["sha256"] == digest, "Artifact hash differs from actual HTTP terminal")
    return path, digest


def verify_volume(h, path, context, parameters, evidence):
    data = context["image_data"]
    h.require(
        context["artifact"]["all_values_equal"] is True
        and context["runtime"] == {"adapter": "0.1.0", "core": "0.20.41", "server": "0.20.41"},
        "Native readback/runtime provenance differs",
    )
    h.require(
        data["dimensions"] == [41, 33, 25]
        and data["points"] == 33825
        and data["cells"] == 30720
        and data["fields"] == parameters["fields"]
        and data["significant_digits"] == 10
        and data["serialization"] == "canonical_ascii_v1",
        "Coarse grid provenance differs",
    )
    h.require(context["source"]["type"] == "XMLImageDataReader", "Volume is not reader-backed")
    root = ElementTree.parse(path).getroot()
    volume = root.find("ImageData")
    h.require(
        root.tag == "VTKFile"
        and root.get("type") == "ImageData"
        and volume is not None
        and volume.get("WholeExtent") == "0 40 0 32 0 24",
        "Coarse VTI geometry differs",
    )
    h.require(
        [float(x) for x in volume.get("Origin", "").split()] == [-2, -1.6, 0]
        and [float(x) for x in volume.get("Spacing", "").split()] == [0.1, 0.1, 0.1],
        "VTI coordinates differ",
    )
    points = volume.find("Piece/PointData")
    h.require(points is not None and points.get("Scalars") == "SyntheticTime_s", "Active scalar differs")
    arrays = points.findall("DataArray")
    h.require(len(arrays) == 2, "Coarse field count differs")
    reports = []
    for field, array in enumerate(arrays):
        name = parameters["fields"][field]["name"]
        h.require(
            array.attrib == {"type": "Float64", "Name": name, "NumberOfComponents": "1", "format": "ascii"},
            "VTI field type/name differs",
        )
        tokens = re.finditer(r"\S+", array.text or "")
        actual_hash, expected_hash = hashlib.sha256(), hashlib.sha256()
        index = 0
        for iz in range(25):
            z = iz * 0.1
            for iy in range(33):
                y = -1.6 + iy * 0.1
                for ix in range(41):
                    x = -2.0 + ix * 0.1
                    q = ((x - 0.2) / 0.9) ** 2 + ((y + 0.1) / 0.65) ** 2 + ((z - 1.2) / 0.30) ** 2
                    delay = 220 * math.exp(-q)
                    value = 0.6 + 0.4 * z + 0.025 * (x * x + y * y) - delay / 1000 if field == 0 else delay
                    expected = struct.pack("<d", float(format(value, ".10g")))
                    token = next(tokens, None)
                    h.require(token is not None, "Missing coarse tuple")
                    actual = float(token.group())
                    packed = struct.pack("<d", actual)
                    h.require(
                        math.isfinite(actual) and packed == expected,
                        "Independent Float64 mismatch in %s at %d" % (name, index),
                    )
                    expected_hash.update(expected)
                    actual_hash.update(packed)
                    index += 1
        h.require(next(tokens, None) is None and index == 33825, "Coarse tuple count differs")
        h.require(
            data["arrays"][field]["sha256_le_float64"] == expected_hash.hexdigest(),
            "Native array hash differs from independent oracle",
        )
        reports.append(
            {
                "name": name,
                "tuples": index,
                "expected_sha256_le_float64": expected_hash.hexdigest(),
                "actual_sha256_le_float64": actual_hash.hexdigest(),
            }
        )
    evidence.record(
        "all_coarse_values_verified",
        comparison="every Float64 bit pattern",
        arrays=reports,
        vti_sha256=h.sha256_file(path),
        historical_full_byte_identity_claimed=False,
    )
    return reports


def verify_png_and_view(h, path, context, expected, evidence):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = 720 * 480
    with Image.open(path) as image:
        h.require(
            image.format == "PNG" and image.size == (720, 480) and image.mode in ("RGB", "RGBA"),
            "PNG type/dimensions/components differ",
        )
        image.verify()
    with Image.open(path) as image:
        image.load()
        rgb = image.convert("RGB")
        ranges = [list(pair) for pair in rgb.getextrema()]
        h.require(any(lo != hi for lo, hi in ranges), "PNG is flat")
        pixel_hash = hashlib.sha256(rgb.tobytes()).hexdigest()
    native = context["artifact"]
    h.require(
        native["dimensions"] == [720, 480] and native["pixel_ranges"] == ranges,
        "Independent/native PNG decode evidence differs",
    )
    view = native["view"]
    for key, value in expected.items():
        if key not in {"name", "path", "width", "height"}:
            h.require(view.get(key) == value, "Native visual readback differs: " + key)
    h.require(
        view["camera_parallel_projection"] is True
        and view["view_size"] == [720, 480]
        and view["representation"] == "Surface",
        "Projection/representation differs",
    )
    report = {
        "dimensions": [720, 480],
        "rgb_pixel_sha256": pixel_hash,
        "pixel_ranges": ranges,
        "png_sha256": h.sha256_file(path),
        "native_view": view,
    }
    evidence.record("png_decode_and_camera_lookup_verified", **report)
    return report


async def exercise(h, caller, server, workspace, parameters, evidence, guard):
    await caller.discover()
    info = await caller.inspect("get_adapter_info")
    before = await caller.inspect("inspect_pipeline")
    host = before["host"]
    h.require(
        before["sources"] == []
        and host["pid"] == server.host_session.process.pid
        and host["thread_id"] == host["main_thread_id"]
        and host["version"] == "5.13.2"
        and host["render_available"] is True,
        "Owned empty main-thread render host not ready",
    )
    evidence.record("adapter_and_host", adapter_info=info, host=host)
    create = parameters["create"]
    volume = await caller.mutation("create_image_data", create)
    vti, vti_hash = artifact(h, workspace, create["path"], volume)
    numeric = verify_volume(h, vti, volume, create, evidence)
    # One schema-valid over-budget request: rejected before native allocation.
    before_negative = await caller.inspect("inspect_pipeline")
    last_request = dict(server.host_session.last_request)
    invalid = {
        **create,
        "name": "OverBudgetMustFail",
        "path": "over-budget-must-not-exist.vti",
        "dimensions": [101, 101, 101],
    }
    rejected = await caller.wait_job(await caller.launch("create_image_data", invalid))
    result = rejected.get("result", {})
    h.require(
        rejected["status"] in {"failed", "completed"}
        and result.get("success") is False
        and result.get("error") == "resource_limit",
        "Over-budget request did not fail precisely",
    )
    h.require(server.host_session.last_request == last_request, "Budget rejection reached native execution")
    h.require(
        not (workspace / invalid["path"]).exists() and not list(workspace.glob(".dcc-mcp-image-*")),
        "Rejected budget request left output/stages",
    )
    h.require(
        (await caller.inspect("inspect_pipeline")) == before_negative and h.sha256_file(vti) == vti_hash,
        "Budget rejection changed the existing volume/pipeline",
    )
    evidence.record(
        "typed_budget_boundary_verified",
        job_id=rejected["job_id"],
        error="resource_limit",
        native_request_not_sent=True,
        prior_artifact_unchanged=True,
    )
    section = await caller.mutation(
        "slice_plane", {"name": "SectionY", "input_name": "CoarseLens", "origin": [0.2, -0.1, 1.2], "normal": [0, 1, 0]}
    )
    h.require(
        section["source"]["points"] == 1025
        and section["source"]["cells"] == 1920
        and section["source"]["type"] == "Cut"
        and section["source"]["origin"] == [0.2, -0.1, 1.2]
        and section["source"]["normal"] == [0, 1, 0]
        and section["source"]["input_bindings"] == [{"source_names": ["CoarseLens"], "port": 0}],
        "Coarse SectionY geometry or source/plane binding differs",
    )
    render = await caller.mutation("render_preview", parameters["render"])
    png, png_hash = artifact(h, workspace, parameters["render"]["path"], render)
    pixels = verify_png_and_view(h, png, render, parameters["render"], evidence)
    presentation = await caller.inspect("inspect_presentation")
    saved = await caller.mutation("save_state", {"path": "coarse-section-y.pvsm"})
    state, state_hash = artifact(h, workspace, "coarse-section-y.pvsm", saved)
    h.require(ElementTree.parse(state).getroot().tag == saved["artifact"]["xml_root"], "PVSM XML readback differs")
    reopened = await caller.mutation("reopen_state", {"path": "coarse-section-y.pvsm"})
    h.require(
        reopened["sources"] == saved["sources"] and reopened["reader_paths"]["CoarseLens"] == str(vti),
        "State roundtrip lost reader-backed pipeline",
    )
    h.require((await caller.inspect("inspect_presentation")) == presentation, "State roundtrip changed presentation")
    h.require(
        [h.sha256_file(p) for p in (vti, png, state)] == [vti_hash, png_hash, state_hash],
        "State roundtrip changed a published artifact",
    )
    guard.check()
    return {
        "kind": "new_hosted_coarse_single_image",
        "points": 33825,
        "native_lane": "official Kitware 5.13.2 Python3.10",
        "not_local_distro_python313": True,
        "valid_volume_creates": 1,
        "render_calls": 1,
        "numeric": numeric,
        "png_decode": pixels,
        "artifacts": [
            {"path": str(p), "sha256": digest, "bytes": p.stat().st_size}
            for p, digest in ((vti, vti_hash), (png, png_hash), (state, state_hash))
        ],
        "state_reopened": True,
        "presentation_preserved": True,
        "budget_negative": "resource_limit",
    }


def main():
    h, parameters = load_fixture_support()
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run-dir", "wheel", "paraview-root", "runtime-archive", "runtime-provenance"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--wheel-sha256", required=True)
    parser.add_argument("--hosted-window-confirmed", action="store_true")
    args = parser.parse_args()
    h.require(
        args.hosted_window_confirmed
        and sys.platform == "linux"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and os.environ.get("GITHUB_REPOSITORY") == "dcc-mcp/dcc-mcp-paraview",
        "Only the explicitly approved official GitHub-hosted Linux window is permitted",
    )
    h.require(os.environ.get("DISPLAY"), "Workflow-owned isolated DISPLAY is required; caller never starts Xvfb")
    runner_temp = h.no_symlink_path(Path(os.environ["RUNNER_TEMP"]))
    args.run_dir = h.no_symlink_path(args.run_dir, must_exist=False)
    h.require(
        args.run_dir.parent == runner_temp and not args.run_dir.exists(), "Fresh direct RUNNER_TEMP child required"
    )
    for key in ("wheel", "paraview_root", "runtime_archive", "runtime_provenance"):
        setattr(args, key, h.no_symlink_path(getattr(args, key)))
    h.require(
        args.paraview_root.is_relative_to(runner_temp), "Official runtime must be workflow-owned under RUNNER_TEMP"
    )
    h.require(re.fullmatch("[a-f0-9]{64}", args.wheel_sha256), "Reviewed wheel digest required")
    args.run_dir.mkdir(mode=0o700)
    for name in ("workspace", "home", "data", "config", "cache", "tmp", "registry"):
        (args.run_dir / name).mkdir(mode=0o700)
    for key in list(os.environ):
        if key.startswith("DCC_MCP_"):
            os.environ.pop(key)
    for key, name in {
        "HOME": "home",
        "XDG_DATA_HOME": "data",
        "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache",
        "TMPDIR": "tmp",
    }.items():
        os.environ[key] = str(args.run_dir / name)
    os.environ.pop("PYTHONPATH", None)
    os.environ["DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS"] = "1"
    os.environ["RUST_LOG"] = "error"
    os.chdir(args.run_dir)
    tempfile.tempdir = str(args.run_dir / "tmp")
    evidence = h.Evidence(args.run_dir)
    guard = h.HostedGuard(args.run_dir, evidence)
    server = None
    started_url = None
    summary = {
        "success": False,
        "scope": "new coarse single-image fixture",
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_sha": os.environ.get("GITHUB_SHA"),
        "prior_full_fixture_regenerated": False,
    }

    def interrupted(signum, frame):
        raise KeyboardInterrupt("Owned hosted fixture interrupted: " + str(signum))

    signal.signal(signal.SIGTERM, interrupted)
    try:
        evidence.record(
            "run_start",
            argv=sys.argv,
            script_sha256=h.sha256_file(__file__),
            helper_sha256=HELPER_SHA256,
            parameters_sha256=PARAMETERS_SHA256,
            parameters=parameters,
            display=os.environ["DISPLAY"],
            **summary,
        )
        guard.sample(preflight=True)
        h.require(
            args.runtime_archive.name == ARCHIVE_NAME
            and args.runtime_archive.stat().st_size == ARCHIVE_BYTES
            and h.sha256_file(args.runtime_archive) == ARCHIVE_SHA256,
            "Actual official archive size/SHA mismatch",
        )
        h.require(args.runtime_provenance.stat().st_size <= 32768, "Runtime provenance metadata unbounded")
        provenance = json.loads(args.runtime_provenance.read_text())
        h.require(
            provenance["sha256"] == ARCHIVE_SHA256
            and provenance["bytes"] == ARCHIVE_BYTES
            and provenance["github_run_id"] == os.environ["GITHUB_RUN_ID"]
            and provenance["github_commit"] == os.environ["GITHUB_SHA"],
            "Current-run runtime provenance differs",
        )
        pvpython = (args.paraview_root / "bin/pvpython").resolve(strict=True)
        h.require(
            pvpython.is_relative_to(args.paraview_root) and os.access(pvpython, os.X_OK),
            "Official pvpython path differs",
        )
        evidence.record(
            "official_runtime_verified",
            current_download=provenance,
            source_preflight_run=PRECHECK_RUN,
            provenance_sha256=h.sha256_file(args.runtime_provenance),
            archive_sha256=ARCHIVE_SHA256,
            archive_bytes=ARCHIVE_BYTES,
            pvpython=str(pvpython),
            pvpython_sha256=h.sha256_file(pvpython),
            not_local_distro_python313=True,
        )
        h.verify_installed_wheel(args.wheel, args.wheel_sha256, evidence)
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        from dcc_mcp_paraview.server import ParaViewMcpServer

        server = ParaViewMcpServer(
            workspace=args.run_dir / "workspace",
            executable=str(pvpython),
            registry_dir=str(args.run_dir / "registry"),
            gateway_port=0,
            enable_gateway_failover=False,
            enable_file_logging=False,
            enable_job_persistence=False,
            enable_telemetry=False,
            enable_checkpoint_persistence=False,
        )
        guard.server = server
        guard.thread.start()
        server.start()
        started_url = server.mcp_url
        parsed = urlparse(started_url)
        h.require(
            parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and h.listener_rows(parsed.port) == ["0100007F"],
            "Owned HTTP listener is not loopback-only",
        )
        pid = server.host_session.process.pid
        actual_exe = Path(os.readlink("/proc/%d/exe" % pid)).resolve()
        python_libs = sorted(
            {line.split()[-1] for line in Path("/proc/%d/maps" % pid).read_text().splitlines() if "libpython" in line}
        )
        h.require(
            actual_exe.is_relative_to(args.paraview_root)
            and python_libs
            and all("libpython3.10" in p and Path(p).is_relative_to(args.paraview_root) for p in python_libs),
            "Owned runtime is not the official native Python3.10 lane",
        )
        evidence.record(
            "owned_native_started",
            mcp_url=started_url,
            identity=server.host_session.identity,
            actual_executable=str(actual_exe),
            loaded_python_libraries=python_libs,
            process=h.owned_process_snapshot(server),
        )

        async def workflow():
            async def outgoing(request):
                body = await request.aread()
                h.require(len(body) <= 128 * 1024, "Outgoing HTTP request exceeded bound")
                evidence.record(
                    "actual_http_request",
                    method=request.method,
                    url=str(request.url),
                    json=json.loads(body) if body else None,
                )

            async with httpx.AsyncClient(
                trust_env=False,
                timeout=httpx.Timeout(20, read=95, write=20, pool=20),
                transport=httpx.AsyncHTTPTransport(retries=0),
                event_hooks={"request": [outgoing]},
            ) as http:
                async with streamable_http_client(started_url, http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as client:
                        return await exercise(
                            h,
                            h.Caller(client, evidence, guard),
                            server,
                            args.run_dir / "workspace",
                            parameters,
                            evidence,
                            guard,
                        )

        summary["result"] = asyncio.run(workflow())
        guard.check()
        summary["success"] = True
    except BaseException as error:
        summary["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
        try:
            evidence.record("failure", **summary["error"])
        except BaseException:
            print("Evidence write failed; continuing owned cleanup", file=sys.stderr)
    finally:
        try:
            guard.stop()
        except BaseException as error:
            summary.update(success=False, guard_stop_error=str(error))
        if server is not None:
            try:
                summary["cleanup"] = h.cleanup_server(server, evidence, started_url)
                identifier = summary["cleanup"]["instance_id"]
                records = list((args.run_dir / "registry").rglob("*.json"))
                if identifier is not None:
                    h.require(all(identifier not in p.read_text() for p in records), "Owned registration remains")
                else:
                    h.require(not records, "Unidentified registry files remain in the fresh owned registry")
                summary["cleanup"]["registry_check"] = {
                    "identified_instance": identifier,
                    "status": "owned_instance_absent"
                    if identifier
                    else "unidentified_no_entry_observed"
                    if not records
                    else "unknown",
                }
                h.require(not list((args.run_dir / "workspace").glob(".dcc-mcp-image-*")), "Owned image stages remain")
            except BaseException as error:
                summary.update(success=False, cleanup_error=str(error))
        summary["peak_sampled_owned_process_group_rss"] = guard.peak_owned_rss
        try:
            evidence.record("run_summary", summary=summary)
        except BaseException as error:
            summary.update(success=False, summary_evidence_error=str(error))
        try:
            with (args.run_dir / "summary.json").open("x") as stream:
                json.dump(summary, stream, indent=2, allow_nan=False)
                stream.write("\n")
        finally:
            evidence.close()
    print(json.dumps({"success": summary["success"], "summary": str(args.run_dir / "summary.json")}))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
