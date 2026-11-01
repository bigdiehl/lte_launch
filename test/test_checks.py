"""Declared checks (checks.py), run in a real LaunchService."""

import sys

import pytest

pytest.importorskip("launch")
pytest.importorskip("launch_ros")

import launch  # noqa: E402
from launch.actions import ExecuteProcess, OpaqueFunction  # noqa: E402

from ros2_typed_launch import checks  # noqa: E402
from ros2_typed_launch import monitor as mon  # noqa: E402


def run(actions):
    """-> (exit code as tlaunch reports it, monitor)"""
    monitor = mon.Monitor(log=False)
    service = launch.LaunchService()
    service.include_launch_description(launch.LaunchDescription(monitor.handlers() + actions))
    return mon.report(monitor, service.run()), monitor


def py(code):
    return ExecuteProcess(cmd=[sys.executable, "-u", "-c", code], output="log")


def test_checks(capsys):
    with pytest.raises(ValueError, match="on_fail"):
        checks.node("/x", on_fail="ignore")

    # Output gates the next action. The ready line comes before the check runs
    # (it waits on another check first), so the monitor's lines must count.
    ran = []
    talker = py("print('ready', flush=True); import time; time.sleep(1.5)")
    first = checks.output(talker, r"^rea", timeout=5)
    code, monitor = run([
        talker,
        checks.output(talker, "ready", timeout=5, then=[
            checks.output(talker, "ready", timeout=5,
                          then=[OpaqueFunction(function=lambda c: ran.append(1))]),
        ]),
        first,
    ])
    assert code == 0 and ran == [1] and first.passed is True
    assert monitor.failed_checks == []

    # A timeout fails the run, skips `then`, and shuts the launch down: the
    # sleeper is stopped long before its 30 s.
    sleeper = py("import time; time.sleep(30)")
    check = checks.output(sleeper, "never", timeout=0.5,
                          then=[OpaqueFunction(function=lambda c: ran.append(2))])
    code, monitor = run([sleeper, check])
    assert code == 1 and check.passed is False and ran == [1]
    assert monitor.failed_checks == ["output /never/ from ExecuteProcess, not after 0.5s"]
    assert "1 check(s) failed" in capsys.readouterr().err

    # on_fail="continue": reported, and the launch carries on to its end.
    check = checks.output(py("print('x')"), "never", timeout=0.3, on_fail="continue")
    done = py("import time; time.sleep(1)")
    code, monitor = run([check, done])
    assert code == 1 and len(monitor.failed_checks) == 1
    assert monitor.get(done).returncode == 0          # ran to completion


def test_graph_checks():
    """Node and topic, against a real publisher."""
    pytest.importorskip("rclpy")
    pub = ExecuteProcess(cmd=["ros2", "topic", "pub", "-r", "5", "/tlaunch_check",
                              "std_msgs/msg/String", "{data: hi}",
                              "--node-name", "tlaunch_check_pub"], output="log")
    ran = []
    stop = OpaqueFunction(function=lambda c: [ran.append(1),
                                              c.emit_event_sync(launch.events.Shutdown())])
    code, monitor = run([
        pub,
        checks.node("tlaunch_check_pub", timeout=20, then=[
            checks.topic("/tlaunch_check", publishers=1, timeout=10, then=[stop]),
        ]),
    ])
    assert ran == [1] and monitor.failed_checks == []
