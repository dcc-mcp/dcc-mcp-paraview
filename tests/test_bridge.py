import json
import sys
import threading
import time

import pytest
from dcc_mcp_core.cancellation import (
    CancelToken,
    DccMcpCancelledError,
    current_job_id,
    reset_cancel_token,
    set_cancel_token,
)

from dcc_mcp_paraview.bridge import ParaViewSession
from dcc_mcp_paraview.contracts import OperationError


def fake_host(tmp_path, delay=0.5, mismatch=False):
    script = tmp_path / "pvpython-fake"
    marker = tmp_path / "late-mutation"
    script.write_text(
        "#!" + sys.executable + "\nimport json,sys,time,pathlib,os\n"
        "print(json.dumps({'ready':True,'pid':os.getpid(),'version':'5.13.2','protocol':1}),flush=True)\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        f" time.sleep({delay!r})\n"
        f" pathlib.Path({str(marker)!r}).write_text('mutated')\n"
        " print(json.dumps({'id':r['id']"
        + ("+'wrong'" if mismatch else "")
        + ",'job_id':r.get('job_id'),'result':{}}),flush=True)\n"
    )
    script.chmod(0o700)
    return ParaViewSession(tmp_path, executable=str(script), timeout=0.2), marker


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only POSIX executable protocol fixture")
def test_timeout_terminates_owned_host_no_late_mutation(tmp_path):
    session, marker = fake_host(tmp_path)
    with pytest.raises(OperationError, match="deadline") as error:
        session.request("inspect_pipeline", {})
    assert error.value.code == "host_timeout"
    assert session.process.poll() is not None
    time.sleep(0.6)
    assert not marker.exists()
    with pytest.raises(OperationError) as closed:
        session.request("inspect_pipeline", {})
    assert closed.value.code == "session_closed"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only POSIX executable protocol fixture")
def test_cancellation_propagates_owned_job_id_and_terminates(tmp_path):
    session, marker = fake_host(tmp_path)
    session.timeout = 2
    token = CancelToken(job_id="core-test-job")
    reset = set_cancel_token(token)
    timer = threading.Timer(0.1, token.cancel)
    try:
        session.start()
        timer.start()
        with pytest.raises(DccMcpCancelledError):
            session.request("inspect_pipeline", {})
    finally:
        try:
            if timer.ident is not None:
                timer.join()
        finally:
            try:
                reset_cancel_token(reset)
            finally:
                session.close()
    assert session.last_request["job_id"] == "core-test-job"
    assert session.process.poll() is not None
    time.sleep(0.6)
    assert not marker.exists()


@pytest.mark.parametrize("failure_phase", ["start", "join"])
def test_cancellation_cleanup_restores_context_and_closes_session(tmp_path, monkeypatch, failure_phase):
    failure = OSError(193, "startup rejected") if failure_phase == "start" else RuntimeError("timer join failed")
    events = []

    class Session:
        def start(self):
            events.append(("start", current_job_id()))
            if failure_phase == "start":
                raise failure

        def request(self, operation, params):
            assert operation == "inspect_pipeline" and params == {}
            raise DccMcpCancelledError("cancelled")

        def close(self):
            events.append(("close", current_job_id()))

    class Timer:
        ident = None

        def start(self):
            self.ident = 1

        def join(self):
            events.append(("join", current_job_id()))
            assert self.ident is not None, "timer was never started"
            raise failure

    session = Session()
    monkeypatch.setattr(sys.modules[__name__], "fake_host", lambda path: (session, path / "unused-marker"))
    monkeypatch.setattr(threading, "Timer", lambda interval, callback: Timer())
    outer = CancelToken(job_id="outer-cleanup-test-job")
    reset = set_cancel_token(outer)
    try:
        with pytest.raises(type(failure)) as error:
            test_cancellation_propagates_owned_job_id_and_terminates(tmp_path)
        assert error.value is failure
        assert current_job_id() == outer.job_id
        assert events[-1] == ("close", outer.job_id)
        assert (("join", "core-test-job") in events) == (failure_phase == "join")
    finally:
        reset_cancel_token(reset)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only POSIX executable protocol fixture")
def test_stale_response_is_never_reused(tmp_path):
    session, _ = fake_host(tmp_path, delay=0, mismatch=True)
    with pytest.raises(OperationError) as error:
        session.request("inspect_pipeline", {})
    assert error.value.code == "host_protocol_error"
    assert session.process.poll() is not None


def test_invalid_arguments_do_not_launch_host(tmp_path):
    session = ParaViewSession(tmp_path, executable=sys.executable)
    with pytest.raises(OperationError):
        session.request("create_sphere", {"name": "x", "radius": -1})
    assert session.process is None


def test_fake_host_script_is_valid(tmp_path):
    session, _ = fake_host(tmp_path)
    assert json.dumps({"job_id": "known"})
    compile(open(session.executable).read(), session.executable, "exec")


def test_pure_any_dispatch_does_not_wait_for_main_lane(tmp_path):
    from dcc_mcp_paraview.bridge import IpcDispatcher

    dispatcher = IpcDispatcher(ParaViewSession(tmp_path, executable=sys.executable))
    entered = threading.Event()
    release = threading.Event()
    dispatcher.start()

    def main_task():
        entered.set()
        assert release.wait(2)
        return "main done"

    thread = threading.Thread(target=lambda: dispatcher.dispatch_callable(main_task, affinity="main"))
    try:
        thread.start()
        assert entered.wait(1)
        assert dispatcher.dispatch_callable(lambda: "pure metadata", affinity="any") == "pure metadata"
    finally:
        release.set()
        thread.join()
        dispatcher.close()


def scripted_host(tmp_path, body, startup=""):
    script = tmp_path / "protocol-host"
    script.write_text(
        "#!"
        + sys.executable
        + "\nimport json,sys,time,pathlib,os\n"
        + startup
        + "\nprint(json.dumps({'ready':True,'pid':os.getpid(),'version':'5.13.2','protocol':1}),flush=True)\n"
        + "for line in sys.stdin:\n r=json.loads(line)\n "
        + body.replace("\n", "\n ")
        + "\n"
    )
    script.chmod(0o700)
    return ParaViewSession(tmp_path, executable=str(script), timeout=1)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only POSIX executable protocol fixture")
@pytest.mark.parametrize(
    "body",
    [
        "print('not-json',flush=True)",
        "print('[]',flush=True)",
        "print('{broken',flush=True)",
        "print(json.dumps({'id':r['id'],'job_id':r['job_id'],'result':[]}),flush=True)",
        "print(json.dumps({'id':r['id'],'job_id':r['job_id'],'result':{'message':'collision'}}),flush=True)",
        "print(json.dumps({'id':r['id'],'job_id':r['job_id'],'error':{}}),flush=True)",
        "print(json.dumps({'id':r['id'],'job_id':r['job_id'],'error':{'code':4,'message':'x'}}),flush=True)",
        "print(json.dumps({'id':r['id'],'job_id':r['job_id'],'result':{},'error':{}}),flush=True)",
        "print(json.dumps({'id':r['id'],'job_id':r['job_id'],'result':{},'extra':True}),flush=True)",
        "print(json.dumps({'id':r['id'],'job_id':'wrong','result':{}}),flush=True)",
        "print(json.dumps({'id':r['id'],'job_id':r['job_id'],'result':{'bad':float('nan')}}),flush=True)",
        'print(\'{"id":1,"id":2}\',flush=True)',
        "print('x'*(1024*1024+1),flush=True)",
    ],
)
def test_malformed_or_oversized_response_closes_session(tmp_path, body):
    session = scripted_host(tmp_path, body)
    with pytest.raises(OperationError) as error:
        session.request("inspect_pipeline", {})
    assert error.value.code == "host_protocol_error"
    assert session.process.poll() is not None
    assert session.stderr.closed


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only POSIX executable protocol fixture")
@pytest.mark.parametrize(
    "identity",
    [
        {"ready": True, "pid": 1, "version": "5.14.0", "protocol": 1},
        {"ready": True, "pid": 1, "version": "5.13.2", "protocol": 2},
        {"ready": True, "pid": True, "version": "5.13.2", "protocol": 1},
        {"ready": True, "pid": 1, "version": "5.13.2"},
    ],
)
def test_unqualified_or_malformed_readiness_never_admits_work(tmp_path, identity):
    session = scripted_host(
        tmp_path,
        "raise AssertionError('must not receive work')",
        "print(" + repr(json.dumps(identity)) + ",flush=True)",
    )
    with pytest.raises(OperationError) as error:
        session.request("inspect_pipeline", {})
    assert error.value.code in {"unsupported_host_version", "host_protocol_error"}
    assert session.last_request is None
    assert session.process.poll() is not None


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only POSIX executable protocol fixture")
def test_startup_diagnostics_are_bounded_and_only_allowed_before_handshake(tmp_path):
    session = scripted_host(
        tmp_path, "print('late diagnostic',flush=True)", "print('[native] bounded startup diagnostic',flush=True)"
    )
    session.start()
    with pytest.raises(OperationError) as error:
        session.request("inspect_pipeline", {})
    assert error.value.code == "host_protocol_error"
    session = scripted_host(tmp_path, "pass", "print('diagnostic'*10000,flush=True)")
    with pytest.raises(OperationError) as error:
        session.start()
    assert error.value.code == "host_protocol_error"


def test_outbound_size_limit_precedes_host_launch(tmp_path, monkeypatch):
    import dcc_mcp_paraview.bridge as bridge

    monkeypatch.setattr(bridge, "MAX_FRAME_BYTES", 32)
    session = ParaViewSession(tmp_path, executable=sys.executable)
    with pytest.raises(OperationError) as error:
        session.request("inspect_pipeline", {})
    assert error.value.code == "invalid_input"
    assert session.process is None


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only POSIX executable protocol fixture")
def test_close_interrupts_active_request_and_blocks_restart(tmp_path):
    session, marker = fake_host(tmp_path, delay=1)
    session.timeout = 10
    session.start()
    outcomes = []

    def request():
        try:
            session.request("inspect_pipeline", {})
        except OperationError as error:
            outcomes.append(error.code)

    thread = threading.Thread(target=request)
    thread.start()
    deadline = time.monotonic() + 1
    while session.last_request is None and time.monotonic() < deadline:
        time.sleep(0.01)
    session.close()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert outcomes
    assert session.process.poll() is not None
    time.sleep(1.1)
    assert not marker.exists()
    session.close()
    with pytest.raises(OperationError, match="closed"):
        session.start()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only POSIX executable protocol fixture")
def test_concurrent_context_job_identity_is_never_crossed(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    session = scripted_host(
        tmp_path, "print(json.dumps({'id':r['id'],'job_id':r['job_id'],'result':{'echo':r}}),flush=True)"
    )

    def request(index):
        token = CancelToken(job_id="job-" + str(index))
        reset = set_cancel_token(token)
        try:
            value = session.request("inspect_pipeline", {})["echo"]
            assert value["job_id"] == "job-" + str(index)
            return value["id"]
        finally:
            reset_cancel_token(reset)

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            identities = list(pool.map(request, range(12)))
        assert len(set(identities)) == 12
    finally:
        session.close()


def test_dispatch_timeout_cancels_queued_work_before_later_drain(tmp_path):
    from dcc_mcp_paraview.bridge import IpcDispatcher

    dispatcher = IpcDispatcher(ParaViewSession(tmp_path, executable=sys.executable))
    mutations = []
    try:
        with pytest.raises(OperationError) as error:
            dispatcher.dispatch_callable(lambda: mutations.append("late"), affinity="main", timeout_hint_secs=0.005)
        assert error.value.code == "dispatch_failed"
        dispatcher.drain_queue(10)
        assert mutations == []
    finally:
        dispatcher.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only POSIX executable protocol fixture")
def test_exited_launcher_cannot_leave_mutating_descendant(tmp_path):
    marker = tmp_path / "descendant-mutation"
    child = (
        "import signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(.7);pathlib.Path("
        + repr(str(marker))
        + ").write_text('late')"
    )
    session = scripted_host(
        tmp_path,
        "import subprocess\nsubprocess.Popen([sys.executable,'-c'," + repr(child) + "])\ntime.sleep(.1)\nsys.exit(0)",
    )
    with pytest.raises(OperationError):
        session.request("inspect_pipeline", {})
    time.sleep(0.8)
    assert not marker.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only POSIX executable protocol fixture")
def test_direct_dispatch_timeout_cancels_active_owned_host(tmp_path):
    from dcc_mcp_paraview.bridge import IpcDispatcher

    session, marker = fake_host(tmp_path, delay=0.8)
    session.timeout = 5
    session.start()
    dispatcher = IpcDispatcher(session)
    dispatcher.start()
    try:
        with pytest.raises(OperationError) as error:
            dispatcher.dispatch_callable(
                lambda: session.request("inspect_pipeline", {}), affinity="main", timeout_hint_secs=0.1
            )
        assert error.value.code == "dispatch_failed"
        deadline = time.monotonic() + 2
        while session.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert session.process.poll() is not None
        time.sleep(0.9)
        assert not marker.exists()
    finally:
        dispatcher.close()
