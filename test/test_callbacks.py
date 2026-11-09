"""Event callbacks: ``on_exit``, ``on_output``, ``on_state``. Queued from the
watcher/reader threads, run on the main thread inside waits and supervise."""

from __future__ import annotations

import threading
import time

import pytest
from pydantic import BaseModel

from _live import PY, QUIET, need_ros, when
from lte_launch import launch, lifecycle_node, namespace, process, shutdown, sleep
from lte_launch import engine

pytestmark = pytest.mark.live

#: prints "hi", then "collision", then exits 0
TALKY = "echo hi; sleep 0.3; echo collision; sleep 0.3; echo bye; exit 0"


class Empty(BaseModel):
    pass


def execute(make_run, body, **run_args):
    """Run ``body`` as a launch; -> (exit code, run)."""
    run = make_run(**run_args)
    return engine.execute(launch(config=Empty)(body), Empty(), run), run


def test_callbacks_in_bringup(make_run, capsys):
    seen = []

    # Callbacks run inside the body's own waits, on the main thread, with
    # the value if they take one.
    def body(config):
        p = process("talky", TALKY)

        @p.on_output(r"coll")
        def hit(line):
            seen.append(("output", line, threading.current_thread().name))

        p.on_output(r"^(hi|bye)$", lambda: seen.append(("plain",)))
        p.on_exit(lambda code: seen.append(("exit", code)))
        sleep(1.5)
        seen.append("body done")
        shutdown("checked")

    code, _ = execute(make_run, body)
    out = capsys.readouterr().out
    assert code == 0, out
    hits = [s for s in seen if s[0] == "output"]
    assert hits == [("output", "collision", threading.main_thread().name)]
    # "hi" may have been read before the line that registered it: not counted
    assert seen.count(("plain",)) in (1, 2)
    assert seen[-2:] == [("exit", 0), "body done"]
    assert seen.index(hits[0]) < seen.index(("exit", 0))


def test_callbacks_after_bringup(make_run, capsys):
    calls = []

    # After the body: supervise runs them. A callback starts processes and
    # registers more callbacks exactly as the body does; shutdown() in one
    # ends the run cleanly.
    def body(config):
        first = process("first", "sleep 0.3; exit 0")

        @first.on_exit
        def start_second(code):
            calls.append(("first", code))
            second = process("second", TALKY)
            second.on_output("collision", lambda: shutdown("collision"))

    code, run = execute(make_run, body)
    out = capsys.readouterr().out
    assert code == 0, out
    assert calls == [("first", 0)]
    assert "up: 1 process(es) running" in out
    assert "shutdown: collision" in out
    assert [p.name for p in run.started] == ["first", "second"]

    # The last process ending with a callback queued: the run stays up for it.
    def last(config):
        p = process("only", "exit 0")
        p.on_exit(lambda: process("after", "echo after; exit 0"))

    code, run = execute(make_run, last)
    out = capsys.readouterr().out
    assert code == 0, out
    assert [p.name for p in run.started] == ["only", "after"]
    assert "every process has finished" in out


def test_callbacks_and_deaths(make_run, capsys):
    calls = []

    # A death is not handed to on_exit: it fails the run (exits= decides).
    def dies(config):
        p = process("bad", "sleep 0.2; exit 3")
        p.on_exit(lambda code: calls.append(code))

    code, _ = execute(make_run, dies)
    assert code == 1 and calls == []
    assert "t:bad died unexpectedly: exit code 3" in capsys.readouterr().out

    # Allowed in exits=: a callback, not a death.
    def allowed(config):
        p = process("ok3", "sleep 0.2; exit 3", exits=(0, 3))
        p.on_exit(lambda code: calls.append(code))

    code, _ = execute(make_run, allowed)
    assert code == 0 and calls == [3], capsys.readouterr().out

    # Respawned: each end is a callback.
    calls.clear()

    def respawns(config):
        p = process("re", "exit 4", respawn=0.1, respawn_max=2)
        p.on_exit(lambda code: calls.append(code))
        sleep(2.0)
        shutdown("enough")

    code, _ = execute(make_run, respawns)
    out = capsys.readouterr().out
    assert code == 1, out  # the third end is past respawn_max: a death
    assert calls == [4, 4]

    # Teardown's exits, and stop(), are no callbacks.
    calls.clear()

    def stopped(config):
        a = process("a", QUIET, ready="up")
        b = process("b", QUIET, ready="up")
        a.on_exit(lambda: calls.append("a"))
        b.on_exit(lambda: calls.append("b"))
        a.stop()
        sleep(0.5)
        shutdown("done")

    code, _ = execute(make_run, stopped)
    assert code == 0 and calls == [], capsys.readouterr().out


def test_callback_once_off_and_errors(make_run, capsys):
    calls = []
    LINES = (f"{PY} -c \"import time\nfor i in range(5): "
             "print('tick', i, flush=True); time.sleep(0.1)\"")  # fmt: skip

    # once= runs a callback the first time only; off() unregisters.
    def body(config):
        p = process("ticks", LINES)
        p.on_output("tick", lambda line: calls.append(("once", line)), once=True)

        def every(line):
            calls.append(("every", line))
            if line.endswith("2"):
                assert p.off(every) == 1

        p.on_output("tick", every)
        p.wait_for_exit(5)
        assert p.off() == 0  # the once is gone, every unregistered itself
        shutdown("checked")

    code, _ = execute(make_run, body)
    assert code == 0, capsys.readouterr().out
    assert [c for c in calls if c[0] == "once"] == [("once", "tick 0")]
    assert [c[1] for c in calls if c[0] == "every"] == ["tick 0", "tick 1", "tick 2"]

    # A callback that raises fails the run as a body error does: exit 1,
    # logged with its name. After bringup too.
    def boom(config):
        p = process("talky", TALKY)

        @p.on_output("collision")
        def explode():
            raise RuntimeError("bang")

    code, _ = execute(make_run, boom)
    out, err = capsys.readouterr()
    assert code == 1
    assert "on_output callback explode failed" in out
    assert "RuntimeError: bang" in err + out


def test_callbacks_dry_run():
    run = engine.Run(None, label="x")
    with engine.active(run):
        p = process("p", "true")
        assert p.on_exit(print) is print
        assert p.off(print) == 1
        p.on_output("x")(print)
        run.dispatch()  # nothing queued, nothing happens
    assert len(run.handlers) == 1


# ------------------------------------------------------------------ lifecycle
def test_on_state_live(make_run, uniq, capsys):
    need_ros("lifecycle", "lifecycle_msgs")
    from lifecycle_msgs.msg import Transition
    from lifecycle_msgs.srv import ChangeState

    from _live import Probe

    states = []
    up = threading.Event()

    # Kept active: someone else deactivates it after bringup, the callback
    # (on the main thread, in supervise) activates it again.
    def body(config):
        with namespace(uniq):
            cam = lifecycle_node(
                "lifecycle", "lifecycle_talker", name="cam", autostart=True
            )

            @cam.on_state("inactive")
            def reactivate(label):
                states.append(label)
                cam.activate()

            @cam.on_state("active", once=True)
            def back():
                states.append("active")
                shutdown("reactivated")

            cam.on_state(None, lambda label: states.append(f"any:{label}"))
        up.set()

    probe = Probe()
    try:

        def deactivate():
            request = ChangeState.Request(transition=Transition(label="deactivate"))
            probe.call(ChangeState, f"/{uniq}/cam/change_state", request)

        when(up.is_set, deactivate)
        t = time.monotonic()
        code, run = execute(make_run, body)
    finally:
        probe.destroy()
    out = capsys.readouterr().out
    assert code == 0, out
    assert time.monotonic() - t < 30
    assert "shutdown: reactivated" in out
    assert [s for s in states if not s.startswith("any:")] == ["inactive", "active"]
    assert "any:deactivating" in states and "any:activating" in states
    assert not run.deaths
