"""Owned hosted-fixture support, extracted from the successful HTTP caller.
No full fixture, direct native request, cancellation matrix or production implementation.
"""

import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

MIB = 1024 * 1024
MAX_EVIDENCE = 8 * MIB
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
FROZEN_PACKAGE_COMMIT = "9100157ebc60bc30e8f49fce0ce887058377059e"
PACKAGE_MANIFEST = {
    "__init__.py": "cb6a9cbc87c6785d940a6af708cd2696105754332fbf9f12c818d252c9925724",
    "bridge.py": "343565fe4e3ad9c63c80f5e13414477087005a3816e960729dd0d0e62cc7fc50",
    "cli.py": "0a96a5dda8ef304bb3d500656e7686098faf91abada0ff570c48e6f9bdf26826",
    "compatibility.py": "fd435853b0e7472d40508f9efc97431469e3214e83e0b5085dd4809c5bf2ce72",
    "contracts.py": "f77b29809d9433bbc0149dd030ef9980caa6e726b22721d5bfbe3cffbbae913f",
    "host_driver.py": "27c0d32d80308a20914c20cc6144d8afbb0f698e8970c8aa74c686ed016cd1fc",
    "image_data.py": "c19f8babcdf03a4b145bf7e431f355f3a79003d92e138f11ae57eff826dc566c",
    "image_files.py": "3880b590f19bc07ccf64d476c66149975aa4be627aa9dc1258f93e2f15f377bb",
    "server.py": "9a8afebddfecb60dbdef3d1aa93e2929bdaed329b619ab9d30184e4902868868",
    "skills/paraview-pipeline/SKILL.md": "68309d708fa6df5300187704328c538dd0265905985e992d887d8451840bd48a",
    "skills/paraview-pipeline/agents/openai.yaml": "f1bedb0c7f92dfb40ed0b4923403edcc5a8f7341dbd4d26a809aa098c84ea8a0",
    "skills/paraview-pipeline/scripts/capture_current_view.py": (
        "d3d316bca03e16720cc1eb6a55d964fcfc455e3d91d40e075fffe1325ceebb02"
    ),
    "skills/paraview-pipeline/scripts/clip_plane.py": (
        "8d86768eb31ea6125a613c04cedfefdbbb0d81d4d1fe24fb31edbc32872dc23a"
    ),
    "skills/paraview-pipeline/scripts/contour.py": "2a5228c31eec057f0601852448c1b4eee7866fe619cb26f4e97ca8a804e793b8",
    "skills/paraview-pipeline/scripts/create_image_data.py": (
        "dc25348e584202f5844025aa7ee43a6d853e9e665f6cc2317df46227199bf536"
    ),
    "skills/paraview-pipeline/scripts/create_sphere.py": (
        "11bd6d891c32478e050971c9a99f8aa9422d8852496bf0a06462f62e265f2a45"
    ),
    "skills/paraview-pipeline/scripts/edit_sphere.py": (
        "3c7f210a558e1d7e2f068ab10da7bee3d2220470d42b70aac89b1bdde05fb560"
    ),
    "skills/paraview-pipeline/scripts/export_dataset.py": (
        "0112519f8ae97c521928d62ac03f99e81eaddedb24db9b39c68292f280d325ad"
    ),
    "skills/paraview-pipeline/scripts/get_adapter_info.py": (
        "a12c251648ae46253daf40e967abe248e541323da7201c15ebb0acccbe351416"
    ),
    "skills/paraview-pipeline/scripts/inspect_pipeline.py": (
        "8405e7590d1396916fcadd3b409f1415abfe2ee7f9d37dfc38794625b90fdede"
    ),
    "skills/paraview-pipeline/scripts/inspect_presentation.py": (
        "7743c38fd88c52675a83957bbf3fb8c56399760fa41b703f6048da0a61e54445"
    ),
    "skills/paraview-pipeline/scripts/open_dataset.py": (
        "0358d9cd1ca02bc1bc1029c6481cc67825674e3901d2208ecce7ed22cebac0f3"
    ),
    "skills/paraview-pipeline/scripts/render_preview.py": (
        "cf292e035be2a43899cb1db72b270514123f767a3ce280565118a875eb522421"
    ),
    "skills/paraview-pipeline/scripts/reopen_state.py": (
        "24523ef523be3d5b50330dd5005886f29db2511d85479500756304787557402b"
    ),
    "skills/paraview-pipeline/scripts/save_state.py": (
        "6a3768f493810a40fe35f355c2dd428ed64eaee31475f8b6cd6f6e4f6b4d370e"
    ),
    "skills/paraview-pipeline/scripts/slice_plane.py": (
        "064fc24eb44af2bbafa96c51f94b7c61a14982ec93c15edc654a456dc873f096"
    ),
    "skills/paraview-pipeline/tools.yaml": "84e412770edc5d905ecca164fd516f5ae4f34ab8d7dcdf2ffc5bba9b606c4f99",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plain(value):
    return value.model_dump(mode="json", by_alias=True) if hasattr(value, "model_dump") else value


class Evidence:
    def __init__(self, directory):
        self.stream = (directory / "events.jsonl").open("x", encoding="utf-8")
        self.lock = threading.Lock()
        self.bytes = 0
        self.sequence = 0

    def record(self, kind, **data):
        with self.lock:
            self.sequence += 1
            event = {
                "sequence": self.sequence,
                "utc": datetime.now(timezone.utc).isoformat(),
                "monotonic": time.monotonic(),
                "kind": kind,
                **data,
            }
            line = json.dumps(event, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"
            require(self.bytes + len(line.encode()) <= MAX_EVIDENCE, "Evidence exceeded 8 MiB; no truncation or retry")
            self.stream.write(line)
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.bytes += len(line.encode())

    def close(self):
        self.stream.close()


def no_symlink_path(path, must_exist=True):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "Use an absolute path without parent traversal")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        require(not current.is_symlink(), "Symlink path components are forbidden: " + str(current))
    if must_exist:
        require(path.exists(), "Missing path: " + str(path))
    return path


def allocated_bytes(root):
    total = 0
    count = 0
    for directory, folders, files in os.walk(root, followlinks=False):
        total += Path(directory).stat().st_blocks * 512
        folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()]
        for name in files:
            try:
                item = (Path(directory) / name).lstat()
            except FileNotFoundError:
                continue
            total += item.st_blocks * 512
            count += 1
            require(count <= 100000, "Qualification budget directory contains too many files")
    return total


def memory_available():
    values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(values["MemAvailable"].split()[0]) * 1024


def owned_process_snapshot(server=None, process=None):
    # Cleanup passes its saved Popen explicitly: clearing a session reference
    # must never make the original owned PGID falsely look empty.
    host = process if process is not None else server.host_session.process if server is not None else None
    if host is None:
        return {"owned_pid": None, "pids": [], "rss_bytes": 0}
    pids, rss = [], 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            if os.getpgid(pid) != host.pid:
                continue
            status_text = (entry / "status").read_text()
            match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status_text, re.MULTILINE)
            rss += int(match.group(1)) * 1024 if match else 0
            pids.append(pid)
        except (ProcessLookupError, FileNotFoundError, PermissionError):
            continue
    return {"owned_pid": host.pid, "pids": sorted(pids), "rss_bytes": rss, "returncode": host.poll()}


def payload(response):
    require(not response.isError, "MCP returned isError; inspect the recorded raw response")
    value = response.structuredContent
    if value is None:
        text = next((part.text for part in response.content if getattr(part, "type", None) == "text"), None)
        require(text is not None, "MCP response has no structured or textual JSON payload")
        value = json.loads(text)
    require(isinstance(value, dict), "MCP payload is not an object")
    return value


class Caller:
    def __init__(self, client, evidence, guard):
        self.client, self.evidence, self.guard = client, evidence, guard
        self.tools = {}
        self.tool_names = {}
        self.full_schemas = {}
        self.attempted_mutations = set()

    async def request(self, name, arguments, kind="tool", timeout=75):
        self.guard.check()
        name = self.tool_names.get(name, name)
        self.evidence.record("request", request_kind=kind, tool=name, arguments=arguments)
        response = await asyncio.wait_for(self.client.call_tool(name, arguments), timeout=timeout)
        self.evidence.record("response", request_kind=kind, tool=name, raw=plain(response))
        self.guard.check()
        return payload(response)

    async def discover(self):
        self.evidence.record("initialize_request")
        initialized = await asyncio.wait_for(self.client.initialize(), timeout=20)
        self.evidence.record("initialize_response", raw=plain(initialized))
        found = await self.request("search_skills", {"query": "paraview"}, "discovery")
        require(
            any(skill["name"] == "paraview-pipeline" for skill in found["skills"]),
            "Official pipeline skill was not found",
        )
        loaded = await self.request("load_skill", {"skill_name": "paraview-pipeline"}, "discovery")
        require(
            loaded.get("loaded") is True and not loaded.get("partial") and not loaded.get("errors"),
            "Pipeline skill did not load completely",
        )
        cursor, seen = None, set()
        for index in range(100):
            self.evidence.record("list_tools_request", page=index, cursor=cursor)
            page = await asyncio.wait_for(self.client.list_tools(cursor=cursor), timeout=20)
            self.evidence.record("list_tools_response", page=index, raw=plain(page))
            for tool in page.tools:
                require(tool.name not in self.tools, "Duplicate tool across discovery pages")
                self.tools[tool.name] = tool
            cursor = page.nextCursor
            if cursor is None:
                break
            require(cursor not in seen, "Tool discovery cursor repeated")
            seen.add(cursor)
        else:
            raise RuntimeError("Tool discovery exceeded 100 pages")
        require("jobs_get_status" in self.tools, "Core jobs_get_status missing from actual discovery")
        for operation in (
            "create_image_data",
            "save_state",
            "reopen_state",
            "render_preview",
            "inspect_presentation",
            "slice_plane",
            "inspect_pipeline",
            "get_adapter_info",
        ):
            # .41 registers underscore-prefixed action IDs, then tools/list emits
            # either collision-free bare names or hyphenated skill-qualified names.
            declarations = [
                tool
                for tool in loaded["tools"]
                if tool.get("skill_name") == "paraview-pipeline"
                and tool.get("name") == "paraview_pipeline__" + operation
            ]
            require(len(declarations) == 1, "Missing or ambiguous official load declaration: " + operation)
            declaration = declarations[0]
            candidates = {operation, declaration["name"], "paraview-pipeline__" + operation} & set(self.tools)
            require(len(candidates) == 1, "Missing or ambiguous actual tools/list name: " + operation)
            actual_name = candidates.pop()
            require(
                self.tools[actual_name].outputSchema == declaration["outputSchema"],
                "tools/list output schema differs from the official skill declaration",
            )
            self.tool_names[operation] = actual_name
            self.full_schemas[operation] = declaration
        self.evidence.record("resolved_actual_tool_names", mapping=self.tool_names)

    async def launch(self, name, arguments):
        import jsonschema

        tool = self.tools[self.tool_names[name]]
        jsonschema.validate(arguments, tool.inputSchema)
        jsonschema.validate(arguments, self.full_schemas[name]["inputSchema"])
        identity = json.dumps([name, arguments], sort_keys=True)
        require(identity not in self.attempted_mutations, "Mutation replay is forbidden")
        self.attempted_mutations.add(identity)
        initial = await self.request(name, arguments, "mutation_once")
        require(tool.outputSchema is not None, "Discovered mutation has no output schema")
        jsonschema.validate(initial, tool.outputSchema)
        require(
            isinstance(initial.get("job_id"), str) and 1 <= len(initial["job_id"]) <= 256,
            "Async mutation did not return an existing Core job ID",
        )
        require(initial.get("status") in TERMINAL | {"pending", "running"}, "Invalid initial job status")
        self.evidence.record("initial_job", tool=name, arguments=arguments, payload=initial)
        return initial["job_id"]

    async def wait_job(self, job_id, deadline=None):
        deadline = deadline or time.monotonic() + 90
        while time.monotonic() < deadline:
            job = await self.request(
                "jobs_get_status",
                {"job_id": job_id},
                "poll_existing_job",
                timeout=max(0.1, min(10, deadline - time.monotonic())),
            )
            require(job.get("job_id") == job_id, "Job identity changed")
            if job.get("status") in TERMINAL:
                self.evidence.record("terminal_job", job_id=job_id, payload=job)
                return job
            require(job.get("status") in {"pending", "running"}, "Unknown job state")
            await asyncio.sleep(0.1)
        raise RuntimeError("Existing job did not terminate within 90 seconds; no mutation replay")

    async def mutation(self, name, arguments):
        job = await self.wait_job(await self.launch(name, arguments))
        require(job["status"] == "completed", name + " failed or was interrupted")
        result = job.get("result")
        require(
            isinstance(result, dict) and result.get("success") is True and isinstance(result.get("context"), dict),
            name + " has no successful canonical terminal result",
        )
        require(result.get("postcondition", {}).get("verified") is True, name + " has no verified postcondition")
        return result["context"]

    async def inspect(self, name):
        result = await self.request(name, {}, "read_only")
        require(result.get("success") is True and isinstance(result.get("context"), dict), name + " failed")
        return result["context"]


def listener_rows(port):
    rows = []
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        for row in Path(name).read_text().splitlines()[1:]:
            fields = row.split()
            address, value = fields[1].split(":")
            if fields[3] == "0A" and int(value, 16) == port:
                rows.append(address)
    return rows


def cleanup_server(server, evidence, started_url):
    process = server.host_session.process
    instance_id = server.instance_id
    port = urlparse(started_url).port if started_url else None
    errors = []
    try:
        server.stop()
    except BaseException as error:
        errors.append("server.stop: " + str(error))
    finally:
        # Existing official owned-host close, never unrelated brokers/processes.
        try:
            server.host_session.close()
        except BaseException as error:
            errors.append("owned host close: " + str(error))
        if process is not None:
            try:
                process.wait(timeout=8)
            except BaseException as error:
                errors.append("owned host wait: " + str(error))
    dispatcher = server.ipc_dispatcher._thread
    result = {
        "instance_id": instance_id,
        "pid": process.pid if process else None,
        "returncode": process.poll() if process else None,
        "dispatcher_stopped": dispatcher is None or not dispatcher.is_alive(),
        "server_stopped": not server.is_running,
        "listener_rows": listener_rows(port) if port else [],
        "remaining_owned_process_group": owned_process_snapshot(process=process),
        "errors": errors,
    }
    evidence.record("cleanup", **result)
    require(
        not errors
        and result["dispatcher_stopped"]
        and result["server_stopped"]
        and not result["listener_rows"]
        and not result["remaining_owned_process_group"]["pids"],
        "Owned runtime did not fully stop; inspect cleanup evidence",
    )
    if port:
        with socket.socket() as connection:
            connection.settimeout(1)
            require(connection.connect_ex(("127.0.0.1", port)) != 0, "Owned loopback listener remains connectable")
    return result


class HostedGuard:
    """Bound only this fresh run; the workflow owns download/extraction budgets."""

    def __init__(self, directory, evidence):
        self.directory, self.evidence = directory, evidence
        self.server = None
        self.failure = None
        self.peak_owned_rss = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.watch, daemon=True)

    def sample(self, preflight=False):
        used, free = allocated_bytes(self.directory), shutil.disk_usage(self.directory).free
        available, owned = memory_available(), owned_process_snapshot(self.server)
        self.peak_owned_rss = max(self.peak_owned_rss, owned["rss_bytes"])
        self.evidence.record(
            "resource_sample", run_allocated=used, free=free, mem_available=available, preflight=preflight, **owned
        )
        require(used <= 32 * MIB, "Run evidence/artifacts exceeded 32 MiB")
        require(free >= 1024 * MIB, "Hosted free disk fell below 1 GiB")
        if preflight:
            require(available >= 2800 * MIB, "MemAvailable below 2800 MiB")

    def check(self):
        require(self.failure is None, "Owned resource guard failed: " + str(self.failure))

    def watch(self):
        while not self.stop_event.wait(1):
            try:
                self.sample()
            except BaseException as error:
                self.failure = str(error)
                if self.server is not None:
                    self.server.host_session.close()
                return

    def stop(self):
        self.stop_event.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=8)
            require(not self.thread.is_alive(), "Owned resource monitor did not stop")


def verify_installed_wheel(wheel_path, wheel_hash, evidence):
    require(sys.flags.isolated == 1, "Use python -I")
    require(sys.version_info[:3] == (3, 12, 14), "Require exact sidecar Python 3.12.14")
    require(sha256_file(wheel_path) == wheel_hash, "Reviewed adapter wheel SHA mismatch")
    for name, version in {
        "dcc-mcp-core": "0.20.41",
        "dcc-mcp-server": "0.20.41",
        "mcp": "1.30.0",
        "dcc-mcp-paraview": "0.1.0",
    }.items():
        dist = importlib.metadata.distribution(name)
        require(dist.version == version, "Exact runtime version mismatch: " + name)
        evidence.record("distribution", name=name, version=dist.version, path=str(dist.locate_file("")))
    from dcc_mcp_core._runtime.core_availability import is_core_extension_available

    import dcc_mcp_paraview

    require(is_core_extension_available(), "Native Core wheel required; no fallback server process")
    core_path = Path(importlib.util.find_spec("dcc_mcp_core._core").origin).resolve()
    package_root = Path(dcc_mcp_paraview.__file__).resolve().parent
    prefix = Path(sys.prefix).resolve()
    require(
        core_path.is_relative_to(prefix)
        and package_root.is_relative_to(prefix)
        and "site-packages" in package_root.parts,
        "Installed runtime origin mismatch",
    )
    with zipfile.ZipFile(wheel_path) as wheel:
        members = {
            name.removeprefix("dcc_mcp_paraview/")
            for name in wheel.namelist()
            if name.startswith("dcc_mcp_paraview/") and not name.endswith("/")
        }
        require(members == set(PACKAGE_MANIFEST), "Wheel must contain exactly the frozen 27 package files")
        for name, digest in PACKAGE_MANIFEST.items():
            require(
                hashlib.sha256(wheel.read("dcc_mcp_paraview/" + name)).hexdigest() == digest
                and sha256_file(package_root / name) == digest,
                "Frozen package hash mismatch: " + name,
            )
    require("paraview" not in sys.modules and "vtkmodules" not in sys.modules, "Native imports leaked into sidecar")
    evidence.record(
        "installed_wheel_verified",
        wheel=str(wheel_path),
        sha256=wheel_hash,
        frozen_package_commit=FROZEN_PACKAGE_COMMIT,
        package_path=str(package_root),
        native_core_path=str(core_path),
        executable=sys.executable,
        python=sys.version,
        pillow=importlib.metadata.version("Pillow"),
        package_file_count=len(PACKAGE_MANIFEST),
    )
