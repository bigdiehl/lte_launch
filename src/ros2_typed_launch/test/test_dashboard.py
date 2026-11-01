"""The libtmux_easy dashboard over a tlaunch run (Q29).

What tlaunch owns here is three translations: tlaunch-pane's pane options into
lte-dash's status-file entries, the sidebar's commands into launch events, and
this process's output into the home log. Each is tested directly; opening the
window itself is lte-dash's job and is tested in libtmux_easy.
"""

import os
import subprocess
import sys

from ros2_typed_launch import dashboard as db
from ros2_typed_launch import monitor as mon


def ours(**values):
    base = {"name": "/demo/turtlesim", "window": "main", "command": "turtlesim_node",
            "shim": "100", "target": "101", "status": "", "stopped": ""}
    return {**base, **values}


# ------------------------------------------------------------- status entries
def test_status_entries():
    """A pane is listed by the window it belongs to, not the one it is in: on
    show, a pane sits in the dashboard's window."""
    entry = db.entry("%5", "0", ours())
    assert entry["name"] == "main:/demo/turtlesim"
    assert entry["pane_id"] == "%5"
    assert entry["running"] is True
    assert entry["target_pids"] == [101]
    assert entry["stopped"] is None

    code = db.entry("%5", "1", ours(status="3"))
    assert not code["running"]
    assert code["exit_status"] == {"code": 3, "signal": None, "inferred": False}

    killed = db.entry("%5", "1", ours(status="-11"))
    assert killed["exit_status"] == {"code": None, "signal": 11, "inferred": False}

    entry = db.entry("%5", "1", ours(status="-2", stopped="SIGINT"))
    assert entry["stopped"] == "SIGINT"

    # The dashboard's own panes, or a shell the user opened in the session.
    assert db.entry("%1", "0", ours(name="")) is None


# ------------------------------------------------------------------ commands
class FakeService:
    def __init__(self):
        self.events = []

    def emit_event(self, event):
        self.events.append(event)


def dashboard_with(rows):
    dash = db.Dashboard.__new__(db.Dashboard)     # no window, no threads
    dash.service, dash.monitor = FakeService(), mon.Monitor(log=False)
    dash.panes = lambda: rows
    return dash


def test_sidebar_commands_go_through_launch():
    """A stop is launch's own ShutdownProcess, and the monitor is told first --
    so the process ending is not reported as dying. Q is a launch Shutdown;
    rerun is refused (launch will not execute an action twice)."""
    from launch.events.process import ShutdownProcess

    rows = [("%5", "0", ours(shim="100")), ("%6", "0", ours(shim="200"))]
    dash = dashboard_with(rows)
    action = object()
    info = dash.monitor.track(action)
    info.pid, info.started = 100, True

    dash.command("stop", "%5")
    assert [type(e) for e in dash.service.events] == [ShutdownProcess]
    assert info.stop_requested

    dash.service.events.clear()
    dash.command("stop", "%6")          # no process of this run has pid 200
    assert dash.service.events == []

    from launch.events import Shutdown

    dash = dashboard_with([])
    dash.command("shutdown", None)
    assert [type(e) for e in dash.service.events] == [Shutdown]

    # Launch will not execute an action twice.
    dash = dashboard_with([("%5", "0", ours())])
    dash.command("respawn", "%5")
    assert dash.service.events == []


# --------------------------------------------------------------------- home
def test_home_gets_python_output_and_a_child_process_alike(tmp_path):
    """Copied at the file descriptor, so a process writing straight to the
    inherited fd 1 -- not through sys.stdout -- reaches home too. Run in a
    child interpreter: pytest captures fd 1 itself."""
    log = tmp_path / "home.log"
    code = (
        "import subprocess, sys\n"
        "from ros2_typed_launch.dashboard import HomeTee\n"
        f"tee = HomeTee({str(log)!r}); tee.start()\n"
        "print('from python', flush=True)\n"
        "subprocess.run(['sh', '-c', 'echo from a child; echo to stderr >&2'])\n"
        "tee.stop()\n"
        "print('after stop')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True, env={**os.environ})
    assert result.returncode == 0, result.stderr
    assert "from python" in result.stdout and "from a child" in result.stdout
    assert "to stderr" in result.stderr                    # the terminal keeps it
    home = log.read_text()
    assert "from python" in home and "from a child" in home and "to stderr" in home
    assert "after stop" not in home
