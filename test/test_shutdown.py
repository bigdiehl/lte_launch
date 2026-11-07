"""on_shutdown() callbacks and per-process stop timeouts, live on plain
processes (libtmux_easy; no ROS)."""

from __future__ import annotations

import os
import signal
import threading
import time

import pytest
from pydantic import BaseModel

from lte_launch import launch, on_shutdown, process, shutdown, sleep
from lte_launch import engine
from _live import DEAF, SLOW



class Mode(BaseModel):
    mode: str = "shutdown"


#: what the callbacks saw, per run
seen: list = []


@launch
def body(config: Mode):
    a = process("a", "echo up; sleep 60", ready="up")

    @on_shutdown
    def first(reason):
        seen.append(("first", reason, reason.kind, a.running))

    def second():  # no argument: called without the reason
        a.wait_for("up", timeout=1)  # waits work in a callback
        seen.append(("second", a.running))

    on_shutdown(second)
    m = config.mode
    if m == "shutdown":
        shutdown("done")
    elif m == "die":
        process("c", "sleep 0.2; exit 3")
        sleep(30)
    elif m == "required":
        process("main", "sleep 0.2", required=True)
    elif m == "fail":
        raise RuntimeError("boom")
    elif m == "interrupt":
        threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGINT)).start()
    elif m == "finished":
        a.stop()
    elif m == "errors":

        @on_shutdown
        def bad():
            raise ValueError("broken callback")

        @on_shutdown(timeout=0.3)
        def stuck():
            sleep(30)  # our wait: raises once the callback is cut
            seen.append("not reached")

        @on_shutdown(timeout=0.3)
        def blocked():
            time.sleep(2)  # not ours: the thread is left to end

        shutdown()
    elif m == "twice":  # a second Ctrl-C cuts the callbacks short

        @on_shutdown(timeout=None)
        def slow():
            os.kill(os.getpid(), signal.SIGINT)
            sleep(30)

        threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGINT)).start()


def run_body(tmp_path, mode, **kw):
    processes = pytest.importorskip("libtmux_easy.processes")
    procs = processes.Processes("t", log_dir=str(tmp_path), prefix="", **kw)
    run = engine.Run(procs, label="sd", params_dir=str(tmp_path))
    seen.clear()
    t = time.monotonic()
    code = engine.execute(body, Mode(mode=mode), run)
    return code, time.monotonic() - t, run


@pytest.mark.live
def test_on_shutdown_order_and_reason(tmp_path, capsys):
    expect = {
        "shutdown": (0, "shutdown", "done"),
        "die": (1, "died", "sd:c died"),
        "required": (0, "shutdown", "sd:main (required) ended"),
        "fail": (1, "failed", "boom"),
        "interrupt": (0, "interrupted", "interrupted"),
        "finished": (0, "finished", "every process has finished"),
    }
    for mode, (want, kind, text) in expect.items():
        code, _, run = run_body(tmp_path / mode, mode)
        assert code == want, (mode, capsys.readouterr().out)
        # last registered, first called; both while 'a' still runs, unless
        # the body stopped it itself
        alive = mode != "finished"
        assert [s[0] for s in seen] == ["second", "first"], mode
        if alive:
            assert seen[0] == ("second", True)
        _, reason, k, running = seen[1]
        assert (k, str(reason), running) == (kind, text, alive), mode
        assert not run.callbacks and not any(p.running for p in run.started)
    out = capsys.readouterr().out
    assert "on_shutdown: first (interrupted)" in out


@pytest.mark.live
def test_on_shutdown_errors_and_timeouts(tmp_path, capsys):
    code, took, run = run_body(tmp_path, "errors")
    out = capsys.readouterr().out
    assert code == 1 and run.callback_failed
    assert "on_shutdown: blocked still running after 0.3 s" in out
    assert "on_shutdown: stuck still running after 0.3 s" in out
    assert "on_shutdown: bad failed: broken callback" in out
    assert [s[0] for s in seen] == ["second", "first"]  # the rest still ran
    assert took < 3, took
    time.sleep(0.5)
    assert "not reached" not in seen

    # a second Ctrl-C during a callback: the rest are skipped, all is killed
    code, took, run = run_body(tmp_path / "twice", "twice")
    out = capsys.readouterr().out
    assert code == 0 and took < 5, (took, out)
    assert "callback(s) cut short" in out and not run.callbacks
    assert not any(p.running for p in run.started)


def test_on_shutdown_dry_run_and_start(tmp_path):
    seen.clear()
    run = engine.Run(None, label="sd", params_dir=str(tmp_path))
    assert engine.execute(body, Mode(mode="interrupt_never"), run) == 0
    assert len(run.callbacks) == 2 and seen == []  # registered, never called
    assert run.call_on_shutdown("x") and seen == []

    processes = pytest.importorskip("libtmux_easy.processes")
    procs = processes.Processes("st", log_dir=str(tmp_path / "s"), prefix="")
    run = body.start(procs, mode="none")
    assert seen == [] and len(run.callbacks) == 2  # the test calls them
    assert run.call_on_shutdown("test done")
    assert seen[1][1:3] == ("test done", "shutdown") and seen[1][3] is True
    procs.close(check=False)

    procs = processes.Processes("sf", log_dir=str(tmp_path / "f"), prefix="")
    seen.clear()
    with pytest.raises(RuntimeError, match="boom"):
        body.start(procs, mode="fail")
    assert seen[1][2] == "failed"  # a failed body calls them itself
    procs.close(check=False)


@launch
def stoppers(config: Mode):
    process("deaf", DEAF, ready="up", stop_timeout=0.3)
    process("slow", SLOW, ready="up")
    if config.mode == "deaf2":
        process("deaf2", DEAF, ready="up")  # the run's default
    shutdown()


@pytest.mark.live
def test_per_process_stop_timeout(tmp_path, capsys):
    processes = pytest.importorskip("libtmux_easy.processes")
    procs = processes.Processes(
        "st", log_dir=str(tmp_path), prefix="", sigint_timeout=30
    )
    run = engine.Run(procs, label="st", params_dir=str(tmp_path))
    t = time.monotonic()
    assert engine.execute(stoppers, Mode(), run) == 0
    took = time.monotonic() - t
    # all at once: deaf escalates after 0.3 s, slow stops on its SIGINT in
    # 1.5 s, and nobody waits for the group's 30 s
    assert took < 3.5, took
    deaf, slow = run.started
    assert deaf.stop_timeout == 0.3 and slow.stop_timeout is None
    assert procs.status("st:deaf").stopped == "SIGTERM"
    assert procs.status("st:slow").stopped == "SIGINT"

    # the run-wide default (--stop-timeout): the group's sigint_timeout
    procs = processes.Processes(
        "st2", log_dir=str(tmp_path / "2"), prefix="", sigint_timeout=0.5
    )
    run = engine.Run(procs, label="st", params_dir=str(tmp_path / "2"))
    t = time.monotonic()
    assert engine.execute(stoppers, Mode(mode="deaf2"), run) == 0
    assert time.monotonic() - t < 3.5
    assert procs.status("st:deaf2").stopped == "SIGTERM"


LAUNCH_FILE = f"""
from pydantic import BaseModel
from lte_launch import launch, on_shutdown, process, shutdown

class C(BaseModel):
    x: int = 1

@launch
def deaf(config: C):
    process("deaf", {DEAF!r}, ready="up")
    on_shutdown(lambda reason: print("callback:", reason.kind, flush=True))
    shutdown("bye")
"""


@pytest.mark.live
def test_stop_timeout_flag(tmp_path, capsys):
    pytest.importorskip("libtmux_easy.processes")
    from lte_launch import cli

    path = tmp_path / "deaf_launch.py"
    path.write_text(LAUNCH_FILE)
    t = time.monotonic()
    assert cli.main([str(path), "--stop-timeout", "0.3"]) == 0
    assert time.monotonic() - t < 4
    out = capsys.readouterr().out
    assert "callback: shutdown" in out and "shutdown: bye" in out
    assert "--stop-timeout" in cli.RESERVED
