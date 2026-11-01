"""Process tracking and death reporting (Q27).

What counts as a *death* is the whole point here: a process we stopped, one that
went down with the launch, and one that exited 0 on its own are all fine, and
only an unasked-for non-zero exit is a death. Those cases are driven with fake
events; the live behaviour is exercised by the demo (`flaky_demo_launch.py`).
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("launch")

from launch.actions import ExecuteProcess  # noqa: E402

from ros2_typed_launch import monitor as mon  # noqa: E402


def action(name="proc"):
    return ExecuteProcess(name=name, cmd=["x"])


class FakeNode:
    """Stands in for a launch_ros Node: all the monitor reads is `node_name`
    (which only resolves once launch is executing the action)."""

    def __init__(self, node_name):
        self.node_name = node_name


def started(act, pid=7, name="proc-1", env=None):
    return SimpleNamespace(
        action=act, pid=pid, process_name=name, cmd=["a", "b"], env=env or {}
    )


def exited(act, returncode=0, name="proc-1"):
    event = started(act, name=name)
    event.returncode = returncode
    return event


def run(returncode, *, stop=False, shutdown=False) -> mon.ProcessInfo:
    """Start a process, optionally stop it or shut down, then exit it."""
    monitor = mon.Monitor(log=False)
    act = action()
    monitor.on_start(started(act))
    if stop:
        monitor.note_stop(act)
    if shutdown:
        monitor.on_shutdown()
    monitor.on_exit(exited(act, returncode))
    return monitor.get(act)


# ------------------------------------------------------------- what is a death
def test_what_counts_as_a_death():
    assert run(3).died_unexpectedly                       # the real case
    assert run(-9).died_unexpectedly                      # killed from outside
    assert not run(0).died_unexpectedly                    # a oneshot finishing
    assert not run(3, stop=True).died_unexpectedly          # we asked it to stop
    assert not run(-2, shutdown=True).died_unexpectedly     # the launch was ending

    assert run(3).why() == "exit code 3"
    assert run(-9).why() == "killed by SIGKILL"
    assert run(-15).why() == "killed by SIGTERM"
    monitor = mon.Monitor(log=False)
    act = action()
    assert monitor.track(act).why() == "not started"
    monitor.on_start(started(act))
    assert monitor.get(act).why() == "still running"


def test_a_record_is_filled_in_and_labelled_by_node():
    """The decision: name the node, keep the process name alongside it."""
    monitor = mon.Monitor(log=False)
    act = action()
    monitor.on_start(started(act, pid=4242, name="talker-1", env={"A": "b"}))
    monitor.on_io(SimpleNamespace(action=act, process_name="talker-1", env=None,
                                  text=b"hello\nworld\n"))
    info = monitor.get(act)
    assert (info.pid, info.process_name, info.cmd) == (4242, "talker-1", ["a", "b"])
    assert info.env == {"A": "b"}          # how a session attributes it to a stage
    assert list(info.lines) == ["hello", "world"]
    assert info.running and not info.exited
    monitor.on_exit(exited(act, 0))
    assert info.exited and not info.running

    # The decision: name the node, keep the process name alongside it.
    monitor = mon.Monitor(log=False)
    node = FakeNode("/demo/turtlesim")
    monitor.on_start(started(node, name="turtlesim_node-1"))
    info = monitor.get(node)
    assert info.node_name == "/demo/turtlesim"
    assert info.label == "/demo/turtlesim (turtlesim_node-1)"

    plain = action("flaky")
    monitor.on_start(started(plain, name="flaky-2"))
    assert monitor.get(plain).node_name is None      # not a node
    assert monitor.get(plain).label == "flaky-2"

    assert mon.node_name_of(FakeNode("/ns/n")) == "/ns/n"
    assert mon.node_name_of(action()) is None                      # raises inside
    assert mon.node_name_of(FakeNode("<unset>")) is None
    # launch's placeholder for a node given no name: worse than no label
    assert mon.node_name_of(FakeNode("/<node_name_unspecified>")) is None


# ------------------------------------------------------------------ reporting
def test_reporting(capsys):
    """Launch itself returns 0 for a run where a node died; this is the fix."""
    monitor = mon.Monitor(log=False)
    good, bad = action("good"), action("bad")
    monitor.on_start(started(good, name="good-1"))
    monitor.on_start(started(bad, name="bad-2"))
    monitor.on_exit(exited(good, 0, name="good-1"))
    assert monitor.summary() == "" and monitor.deaths() == []

    monitor.on_exit(exited(bad, 3, name="bad-2"))
    summary = monitor.summary()
    assert "1 process(es) died unexpectedly" in summary
    assert "bad-2: exit code 3 [pid 7]" in summary
    assert "cmd: a b" in summary
    assert [info.key for info in monitor.deaths()] == ["bad-2"]

    # Launch itself returns 0 for a run where a node died; this is the fix.
    quiet = mon.Monitor(log=False)
    assert mon.report(quiet, 0) == 0
    assert capsys.readouterr().err == ""

    monitor = mon.Monitor(log=False)
    act = action()
    monitor.on_start(started(act))
    monitor.on_exit(exited(act, 3))
    assert mon.report(monitor, 0) == 1          # 0 -> failure
    assert mon.report(monitor, 2) == 2          # a real code is kept
    assert "died unexpectedly" in capsys.readouterr().err

    monitor = mon.Monitor()                      # logging on, as in a real run
    act = action()
    monitor.on_start(started(act, name="flaky-2"))
    monitor.on_exit(exited(act, 3, name="flaky-2"))
    logged = capsys.readouterr()
    assert "flaky-2 died unexpectedly: exit code 3" in logged.out + logged.err

    from launch.actions import RegisterEventHandler

    handlers = mon.Monitor(log=False).handlers()
    assert len(handlers) == 4
    assert all(isinstance(h, RegisterEventHandler) for h in handlers)


# --------------------------------------------------------------- diagnostics
def test_the_diagnostic_message_carries_the_level_as_bytes():
    """The level **must** be the bytes constant: assigning the int 2 aborts the
    whole process inside the C conversion, which no test can catch."""
    diagnostic_msgs = pytest.importorskip("diagnostic_msgs.msg")
    DiagnosticStatus = diagnostic_msgs.DiagnosticStatus

    monitor = mon.Monitor(log=False)
    publisher = mon.Diagnostics(monitor)
    act = action()

    not_started = publisher.status(monitor.track(act, key="flaky"))
    assert not_started.level == DiagnosticStatus.STALE
    assert isinstance(not_started.level, bytes)

    monitor.on_start(started(act, pid=99, name="flaky-2"))
    running = publisher.status(monitor.get(act))
    assert running.level == DiagnosticStatus.OK and running.message == "running"
    assert running.name == "flaky-2"
    assert dict((kv.key, kv.value) for kv in running.values) == {
        "process": "flaky-2", "pid": "99", "returncode": "", "cmd": "a b",
    }

    monitor.on_exit(exited(act, 3, name="flaky-2"))
    dead = publisher.status(monitor.get(act))
    assert dead.level == DiagnosticStatus.ERROR
    assert dead.message == "died unexpectedly: exit code 3"
