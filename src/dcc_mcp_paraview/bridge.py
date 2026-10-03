"""Serialized IPC to one adapter-owned pvpython main thread; no ParaView imports."""

import contextvars
import json
import os
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from dcc_mcp_core import HostUiDispatcherBase
from dcc_mcp_core.cancellation import current_job_id
from dcc_mcp_core.skills_helper import check_dcc_cancelled, skill_error, skill_success

from .compatibility import HOST_VERSIONS
from .contracts import OperationError, validate

_current_session = contextvars.ContextVar("paraview_session", default=None)


MAX_FRAME_BYTES = 1024 * 1024
MAX_STARTUP_DIAGNOSTICS = 64 * 1024
PROTOCOL_VERSION = 1


def _strict_json(frame):
    def reject_constant(value):
        raise ValueError("Nonfinite JSON value")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result

    return json.loads(frame, parse_constant=reject_constant, object_pairs_hook=unique_pairs)


class ParaViewSession:
    def __init__(self, workspace, executable=None, timeout=60):
        self.workspace = Path(workspace).expanduser().resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        self.executable = executable or os.environ.get("DCC_MCP_PARAVIEW_EXECUTABLE") or shutil.which("pvpython")
        if not self.executable:
            raise OperationError("host_not_found", "Set DCC_MCP_PARAVIEW_EXECUTABLE to pvpython")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError("timeout must be positive and at most 300 seconds")
        self.timeout = timeout
        self.process = None
        self.stderr = None
        self._lock = threading.RLock()
        # Closing must not wait behind the serial request lock during a native call.
        self._state_lock = threading.RLock()
        self._buffer = b""
        self._closed = False
        self._cleanup_done = False
        self.identity = {}
        self.last_request = None

    def start(self):
        with self._lock:
            try:
                with self._state_lock:
                    if self._closed:
                        raise OperationError("session_closed", "Session was closed; start a new adapter instance")
                    if self.process is not None:
                        if self.process.poll() is not None:
                            raise OperationError("host_exited", "Owned ParaView host has exited")
                        return
                    self.stderr = tempfile.TemporaryFile(mode="w+b")
                    # Keep sidecar packages out of vendor Python; only this owned process is launched.
                    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONHOME", "PYTHONPATH"}}
                    env["PYTHONUNBUFFERED"] = "1"
                    self.process = subprocess.Popen(
                        [
                            self.executable,
                            "--force-offscreen-rendering",
                            str(Path(__file__).with_name("host_driver.py")),
                            str(self.workspace),
                        ],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=self.stderr,
                        cwd=self.workspace,
                        env=env,
                        start_new_session=True,
                    )
                os.set_blocking(self.process.stdin.fileno(), False)
                identity = self._read_frame(time.monotonic() + self.timeout, startup=True)
                if (
                    set(identity) != {"ready", "pid", "version", "protocol"}
                    or identity["ready"] is not True
                    or type(identity["pid"]) is not int
                    or identity["pid"] <= 0
                    or type(identity["protocol"]) is not int
                    or identity["protocol"] != PROTOCOL_VERSION
                    or not isinstance(identity["version"], str)
                ):
                    raise OperationError("host_protocol_error", "Host readiness identity or protocol is invalid")
                if identity["version"] not in HOST_VERSIONS:
                    raise OperationError(
                        "unsupported_host_version", "Qualified ParaView versions: " + ", ".join(HOST_VERSIONS)
                    )
                self.identity = identity
            except BaseException:
                self.close()
                raise

    def _read_frame(self, deadline, startup=False):
        diagnostics = 0
        while True:
            check_dcc_cancelled()
            if self._closed:
                raise OperationError("session_closed", "Owned ParaView session was stopped")
            if time.monotonic() >= deadline:
                raise OperationError(
                    "host_timeout", "Host deadline exceeded; session terminated, mutation may be partial"
                )
            if b"\n" in self._buffer:
                frame, self._buffer = self._buffer.split(b"\n", 1)
                if len(frame) + 1 > MAX_FRAME_BYTES:
                    raise OperationError("host_protocol_error", "Host response exceeded its size limit")
                try:
                    value = _strict_json(frame)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    diagnostics += len(frame) + 1
                    if startup and diagnostics <= MAX_STARTUP_DIAGNOSTICS and not frame.lstrip().startswith(b"{"):
                        continue
                    raise OperationError("host_protocol_error", "Host returned malformed JSON") from None
                if not isinstance(value, dict):
                    raise OperationError("host_protocol_error", "Host response must be an object")
                return value
            if len(self._buffer) >= MAX_FRAME_BYTES:
                raise OperationError("host_protocol_error", "Host response exceeded its size limit")
            remaining = deadline - time.monotonic()
            if self.process is None or self.process.poll() is not None:
                raise OperationError("host_exited", "Owned ParaView host exited; inspect adapter logs")
            try:
                if select.select([self.process.stdout], [], [], min(0.1, max(0, remaining)))[0]:
                    chunk = os.read(self.process.stdout.fileno(), 65536)
                    if not chunk:
                        raise OperationError("host_exited", "Owned ParaView host closed its protocol pipe")
                    self._buffer += chunk
            except OperationError:
                raise
            except (OSError, ValueError):
                if self._closed:
                    raise OperationError("session_closed", "Owned ParaView session was stopped") from None
                raise OperationError("host_protocol_error", "Owned host protocol pipe failed") from None

    def _write_frame(self, frame, deadline):
        position = 0
        while position < len(frame):
            check_dcc_cancelled()
            if self._closed:
                raise OperationError("session_closed", "Owned ParaView session was stopped")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OperationError("host_timeout", "Host request pipe deadline exceeded; session terminated")
            try:
                stream = self.process.stdin
                if select.select([], [stream], [], min(0.1, remaining))[1]:
                    try:
                        position += os.write(stream.fileno(), frame[position:])
                    except BlockingIOError:
                        pass
            except (OSError, ValueError):
                raise OperationError("host_protocol_error", "Owned host request pipe failed") from None

    def request(self, operation, params):
        validate(operation, params)
        request = {"id": uuid.uuid4().hex, "job_id": current_job_id(), "operation": operation, "params": params}
        frame = (json.dumps(request, allow_nan=False) + "\n").encode()
        if len(frame) > MAX_FRAME_BYTES:
            raise OperationError("invalid_input", "Request exceeded its size limit")
        with self._lock:
            check_dcc_cancelled()
            self.start()
            self.last_request = request
            try:
                deadline = time.monotonic() + self.timeout
                self._write_frame(frame, deadline)
                response = self._read_frame(deadline)
                if response.get("id") != request["id"] or response.get("job_id") != request["job_id"]:
                    raise OperationError("host_protocol_error", "Host response identity mismatch; session terminated")
                if set(response) == {"id", "job_id", "result"}:
                    result = response["result"]
                    reserved = {
                        "message",
                        "success",
                        "error",
                        "prompt",
                        "context",
                        "postcondition",
                        "verified",
                        "_meta",
                    }
                    if not isinstance(result, dict) or reserved.intersection(result):
                        raise OperationError("host_protocol_error", "Host result shape is invalid")
                elif set(response) == {"id", "job_id", "error"}:
                    error = response["error"]
                    if (
                        not isinstance(error, dict)
                        or set(error) != {"code", "message"}
                        or not isinstance(error["code"], str)
                        or not 1 <= len(error["code"]) <= 100
                        or not isinstance(error["message"], str)
                        or len(error["message"]) > 500
                    ):
                        raise OperationError("host_protocol_error", "Host error shape is invalid")
                else:
                    raise OperationError(
                        "host_protocol_error", "Host response must contain exactly one result or error"
                    )
            except BaseException:
                # Never replay mutations or reuse interrupted/malformed streams.
                self.close()
                raise
            if "error" in response:
                raise OperationError(response["error"]["code"], response["error"]["message"])
            return response["result"]

    def close(self):
        with self._state_lock:
            if self._cleanup_done:
                return
            self._closed = True
            if self.process is not None:
                # The child owns a new POSIX session. Kill its group even if the launcher
                # already exited, so inherited-pipe descendants cannot mutate later.
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=3)
                for stream in (self.process.stdin, self.process.stdout):
                    if stream is not None:
                        stream.close()
            if self.stderr is not None:
                self.stderr.close()
            self._cleanup_done = True


class IpcDispatcher(HostUiDispatcherBase):
    """One persistent Core queue pump owns IPC; ParaView runs only in the child.

    Reuse Core's native HTTP queue and Python callable queue, not a second
    adapter job system. The transport pump is distinct from host main-thread API.
    """

    def __init__(self, session):
        super().__init__(label="ParaView IPC owner")
        self.session = session
        self._wake = threading.Event()
        self._finished = threading.Event()
        self._thread = None

    def start(self):
        if self._finished.is_set():
            raise OperationError("session_closed", "Dispatcher was stopped; create a new adapter instance")
        if self._thread is None:
            self._thread = threading.Thread(target=self._pump, name="paraview-ipc-owner", daemon=True)
            self._thread.start()

    def poke_host_pump(self):
        self._wake.set()

    def _pump(self):
        token = _current_session.set(self.session)
        try:
            while not self._finished.is_set():
                self.drain_queue(10)
                self._wake.wait(0.01)
                self._wake.clear()
        finally:
            _current_session.reset(token)

    def dispatch_callable(self, func, *args, **kwargs):
        request_id = uuid.uuid4().hex
        outcome = self.submit_callable(
            request_id,
            lambda: func(*args),
            affinity=kwargs.get("affinity", "main"),
            timeout_ms=int((kwargs.get("timeout_hint_secs") or 65) * 1000),
        )
        if not outcome["success"]:
            self.cancel(request_id)
            raise OperationError("dispatch_failed", outcome["error"])
        return outcome["output"]

    def close(self):
        self.shutdown()
        self.session.close()
        self._finished.set()
        self._wake.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise OperationError("shutdown_failed", "IPC pump did not stop within its shutdown budget")


def call(operation, **params):
    session = _current_session.get()
    if session is None:
        return skill_error("No ParaView execution bridge is bound", "host_not_bound")
    try:
        result = session.request(operation, params)
        if operation == "inspect_pipeline":
            return skill_success("ParaView pipeline inspected", **result)
        return skill_success(
            "ParaView operation verified",
            verified=True,
            postcondition={"method": "host_api_readback", "operation": operation},
            **result,
        )
    except OperationError as error:
        return skill_error(str(error), error.code)
