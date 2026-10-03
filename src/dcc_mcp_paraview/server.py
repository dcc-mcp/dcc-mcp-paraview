"""Core-owned MCP lifecycle composed with a typed ParaView IPC execution lane."""

import argparse
import json
import os
import signal
import threading
from pathlib import Path

from dcc_mcp_core import DccServerBase, DccServerOptions, HostExecutionBridge
from dcc_mcp_core.host_errors import capture_bootstrap_errors

from . import __version__
from .bridge import IpcDispatcher, ParaViewSession
from .compatibility import check_core_runtime
from .contracts import OperationError


class ParaViewMcpServer(DccServerBase):
    def __init__(self, port=None, workspace=None, executable=None, session=None, **kwargs):
        check_core_runtime()
        self._adapter_stopped = False
        self._adapter_stop_complete = False
        self._adapter_start_lock = threading.RLock()
        self.host_session = session or ParaViewSession(
            workspace or os.environ.get("DCC_MCP_PARAVIEW_WORKSPACE", os.getcwd()), executable=executable
        )
        try:
            self.ipc_dispatcher = IpcDispatcher(self.host_session)
            self.host_bridge = HostExecutionBridge(dispatcher=self.ipc_dispatcher)
            kwargs.setdefault("gateway_port", 0)
            options = DccServerOptions.from_env(
                "paraview",
                Path(__file__).parent / "skills",
                port=port,
                server_name="dcc-mcp-paraview",
                server_version=__version__,
                adapter_version=__version__,
                instance_type="standalone",
                execution_bridge=self.host_bridge,
                **kwargs,
            )
            super().__init__(options=options)
            self.register_builtin_actions()
        except BaseException:
            self.host_session.close()
            if hasattr(self, "ipc_dispatcher"):
                self.ipc_dispatcher.close()
            raise

    def _version_string(self):
        return self.host_session.identity.get("version", "unknown")

    def start(self, **kwargs):
        with self._adapter_start_lock:
            if self._adapter_stopped:
                raise OperationError("session_closed", "Server was stopped; create a new adapter instance")
            try:
                self.host_session.start()
                self.ipc_dispatcher.start()
                return super().start(**kwargs)
            except BaseException:
                self.stop()
                raise

    def stop(self):
        # Serialize lifecycle only: native requests use their separate session lock.
        with self._adapter_start_lock:
            if self._adapter_stop_complete:
                return
            self._adapter_stopped = True
            # Stop host work before Core waits for jobs, including failed startup.
            try:
                self.ipc_dispatcher.close()
            finally:
                try:
                    self.host_session.close()
                finally:
                    super().stop()
            self._adapter_stop_complete = True


def main():
    parser = argparse.ArgumentParser(description="Serve an isolated, persistent ParaView pipeline over local MCP")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--pvpython")
    parser.add_argument("--port", type=int)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    with capture_bootstrap_errors("paraview", adapter_version=__version__, min_core_version="0.20.41"):
        server = ParaViewMcpServer(port=args.port, workspace=args.workspace, executable=args.pvpython)
        try:
            server.start()
            print(json.dumps({"mcp_url": server.mcp_url, "instance_id": server.instance_id}), flush=True)
            stopped.wait()
        finally:
            server.stop()


if __name__ == "__main__":
    main()
