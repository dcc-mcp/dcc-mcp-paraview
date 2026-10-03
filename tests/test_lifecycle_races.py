"""Portable lifecycle races using controlled resources, never native hosts or HTTP."""

import threading
from types import SimpleNamespace

import pytest
from dcc_mcp_core import DccServerBase

from dcc_mcp_paraview.contracts import OperationError
from dcc_mcp_paraview.server import ParaViewMcpServer


@pytest.fixture
def lifecycle(monkeypatch):
    server = object.__new__(ParaViewMcpServer)
    server._adapter_stopped = False
    server._adapter_stop_complete = False
    server._adapter_start_lock = threading.RLock()
    calls = []
    state = SimpleNamespace(transport_active=False)
    host = SimpleNamespace(closed=False)
    dispatcher = SimpleNamespace(closed=False)

    def host_start():
        calls.append("host-start")

    def dispatcher_start():
        calls.append("dispatcher-start")

    def host_close():
        calls.append("host-close")
        host.closed = True

    def dispatcher_close():
        calls.append("dispatcher-close")
        dispatcher.closed = True

    def core_start(self, **kwargs):
        calls.append("core-start")
        state.transport_active = True

    def core_stop(self):
        calls.append("core-stop")
        state.transport_active = False

    host.start, host.close = host_start, host_close
    dispatcher.start, dispatcher.close = dispatcher_start, dispatcher_close
    server.host_session, server.ipc_dispatcher = host, dispatcher
    monkeypatch.setattr(DccServerBase, "start", core_start)
    monkeypatch.setattr(DccServerBase, "stop", core_stop)
    return SimpleNamespace(server=server, host=host, dispatcher=dispatcher, calls=calls, state=state)


def worker(action, errors, done=None):
    def run():
        try:
            action()
        except BaseException as error:
            errors.append(error)
        finally:
            if done is not None:
                done.set()

    return threading.Thread(target=run, daemon=True)


def join_threads(*threads):
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads), "Owned lifecycle worker did not finish"


def test_stop_during_core_prehandle_waits_then_closes_final_transport(lifecycle, monkeypatch):
    entered_core = threading.Event()
    release_core = threading.Event()
    stop_requested = threading.Event()
    stop_finished = threading.Event()
    errors = []

    def core_start(self, **kwargs):
        entered_core.set()
        assert release_core.wait(timeout=5), "Test did not release controlled startup"
        lifecycle.calls.append("core-start")
        lifecycle.state.transport_active = True

    def stop():
        stop_requested.set()
        lifecycle.server.stop()

    monkeypatch.setattr(DccServerBase, "start", core_start)
    starter = worker(lifecycle.server.start, errors)
    stopper = worker(stop, errors, stop_finished)
    starter.start()
    try:
        assert entered_core.wait(timeout=5)
        stopper.start()
        assert stop_requested.wait(timeout=5)
        assert not stop_finished.wait(timeout=0.25), "Stop returned before startup's final transport was available"
    finally:
        release_core.set()
        join_threads(starter, *([stopper] if stopper.ident is not None else []))
    assert errors == []
    assert stop_finished.is_set()
    assert lifecycle.server._adapter_stopped
    assert lifecycle.host.closed and lifecycle.dispatcher.closed
    assert not lifecycle.state.transport_active
    assert lifecycle.calls == [
        "host-start",
        "dispatcher-start",
        "core-start",
        "dispatcher-close",
        "host-close",
        "core-stop",
    ]
    with pytest.raises(OperationError) as error:
        lifecycle.server.start()
    assert error.value.code == "session_closed"


def test_concurrent_stop_is_idempotent_after_normal_start(lifecycle):
    lifecycle.server.start()
    ready = threading.Barrier(5)
    errors = []

    def stop():
        ready.wait(timeout=5)
        lifecycle.server.stop()

    stoppers = [worker(stop, errors) for _ in range(4)]
    for stopper in stoppers:
        stopper.start()
    ready.wait(timeout=5)
    join_threads(*stoppers)
    lifecycle.server.stop()
    assert errors == []
    assert lifecycle.calls.count("dispatcher-close") == 1
    assert lifecycle.calls.count("host-close") == 1
    assert lifecycle.calls.count("core-stop") == 1
    assert lifecycle.host.closed and lifecycle.dispatcher.closed
    assert not lifecycle.state.transport_active


@pytest.mark.parametrize("failure_stage", ["host", "dispatcher", "core"])
def test_start_failure_closes_resources_without_reentrant_deadlock(lifecycle, monkeypatch, failure_stage):
    def fail(*args, **kwargs):
        raise RuntimeError("controlled " + failure_stage + " startup failure")

    if failure_stage == "host":
        lifecycle.host.start = fail
    elif failure_stage == "dispatcher":
        lifecycle.dispatcher.start = fail
    else:
        monkeypatch.setattr(DccServerBase, "start", fail)
    errors = []
    starter = worker(lifecycle.server.start, errors)
    starter.start()
    join_threads(starter)
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == "controlled " + failure_stage + " startup failure"
    assert lifecycle.host.closed and lifecycle.dispatcher.closed
    assert not lifecycle.state.transport_active
    assert lifecycle.server._adapter_stopped
    assert lifecycle.calls.count("core-stop") == 1
    lifecycle.server.stop()
    assert lifecycle.calls.count("host-close") == 1
    assert lifecycle.calls.count("core-stop") == 1


def test_stop_can_close_host_while_owned_request_is_blocked(lifecycle):
    lifecycle.server.start()
    request_lock = threading.Lock()
    request_entered = threading.Event()
    host_closed = threading.Event()
    request_finished = threading.Event()
    stop_finished = threading.Event()
    errors = []
    original_close = lifecycle.host.close

    def request():
        with request_lock:
            request_entered.set()
            assert host_closed.wait(timeout=5), "Shutdown waited behind the active host request"

    def close():
        original_close()
        host_closed.set()

    lifecycle.host.close = close
    requester = worker(request, errors, request_finished)
    stopper = worker(lifecycle.server.stop, errors, stop_finished)
    requester.start()
    try:
        assert request_entered.wait(timeout=5)
        stopper.start()
        assert stop_finished.wait(timeout=5)
        assert request_finished.wait(timeout=5)
    finally:
        host_closed.set()
        join_threads(requester, *([stopper] if stopper.ident is not None else []))
    assert errors == []
    assert lifecycle.host.closed and not lifecycle.state.transport_active


def test_failed_transport_cleanup_can_be_retried_without_allowing_restart(lifecycle, monkeypatch):
    lifecycle.server.start()
    attempts = []

    def core_stop(self):
        attempts.append("stop")
        if len(attempts) == 1:
            raise RuntimeError("controlled transport cleanup failure")
        lifecycle.state.transport_active = False

    monkeypatch.setattr(DccServerBase, "stop", core_stop)
    with pytest.raises(RuntimeError, match="controlled transport cleanup failure"):
        lifecycle.server.stop()
    assert lifecycle.server._adapter_stopped
    assert not lifecycle.server._adapter_stop_complete
    assert lifecycle.state.transport_active
    with pytest.raises(OperationError) as error:
        lifecycle.server.start()
    assert error.value.code == "session_closed"
    lifecycle.server.stop()
    lifecycle.server.stop()
    assert attempts == ["stop", "stop"]
    assert lifecycle.server._adapter_stop_complete
    assert not lifecycle.state.transport_active
