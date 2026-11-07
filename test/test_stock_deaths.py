"""A process dying inside a stock include is a death of the run.

Stock ``ros2 launch`` exits 0 whatever happened to its processes; only its
``process has died`` line says so. The engine reads those lines.
"""

from __future__ import annotations

import os
import shutil
import textwrap

import pytest
from pydantic import BaseModel

from ros2_typed_launch import engine, include, launch, shutdown, sleep

CMD = "cmd '/opt/ros/jazzy/lib/demo_nodes_cpp/talker'"


def died(name: str, code: int, pid: int = 12) -> str:
    return f"[ERROR] [{name}]: process has died [pid {pid}, exit code {code}, {CMD}]."


def test_stock_line_parser():
    assert engine.stock_ended(died("talker-1", 3)) == ("talker-1", 12, 3)
    assert engine.stock_ended(died("talker-1", -11)) == ("talker-1", 12, -11)
    assert engine.stock_ended(died("my.node_x-12", 139, 7)) == ("my.node_x-12", 7, 139)
    # as tlaunch echoes it: behind the include's own prefix
    assert engine.stock_ended("[w:inc] " + died("talker-1", 1)) == ("talker-1", 12, 1)
    clean = "[INFO] [talker-1]: process has finished cleanly [pid 4242]"
    assert engine.stock_ended(clean) == ("talker-1", 4242, 0)
    for line in [
        "[INFO] [talker-1]: process started with pid [4242]",
        "[talker-1] [INFO] [1700000000.1] [talker]: Publishing: 'Hello'",
        "[WARNING] [launch]: user interrupted with ctrl-c (SIGINT)",
        "",
    ]:
        assert engine.stock_ended(line) is None, line


def test_stock_watch_rules(capsys):
    """Which ends are deaths: codes in ``exits`` and names in ``allow`` are not;
    nothing counts while stopping."""
    run = engine.Run(None, label="t")
    proc = engine.Proc(run, "inc", "w", "ros2 launch x y")
    clean = "[INFO] [talker-1]: process has finished cleanly [pid 5]"

    def deaths(lines, **kw):
        run.deaths.clear()
        proc.stopping = run.closing = False
        watch = run.stock_watch(**kw)
        for line in lines:
            watch(proc, line)
        return [(d.name, d.returncode) for d in run.deaths]

    assert deaths([died("talker-1", 3), clean]) == [("talker-1", 3)]
    assert deaths([died("a-1", -9)], exits=None) == [("a-1", -9)]
    assert deaths([clean], exits=None) == [("talker-1", 0)]
    assert deaths([died("talker-1", 3)], exits=(0, 3)) == []
    assert deaths([died("talker-1", 3)], exits=3) == []
    assert deaths([died("talker-1", 3), died("b-2", 1)], allow=["talker"]) == [("b-2", 1)]
    assert deaths([died("talker-1", 3)], allow=["talker-1"]) == []

    run.deaths.clear()
    watch = run.stock_watch()
    proc.stopping = True
    watch(proc, died("talker-1", -2))
    proc.stopping, run.closing = False, True
    watch(proc, died("talker-1", -2))
    assert run.deaths == []

    death = engine.InnerDeath(proc, "talker-1", 5, 3)
    assert death.who == "talker-1 in w:inc"
    assert engine.why(death.returncode) == "exit code 3"
    assert "talker-1 in w:inc died unexpectedly: exit code 3" in capsys.readouterr().out
    with pytest.raises(engine.ProcessDied, match="talker-1 in w:inc died"):
        run.deaths.append(death)
        run.check()


class PathCfg(BaseModel):
    path: str = "/x/dies.launch.py"


@launch
def by_path(config: PathCfg):
    include(config.path, name="inc", exits=None, allow=["a"], timeout=5, r=1)
    include("pkg", "f.launch.py", window="w", use_sim_time=True)


def test_include_plan():
    """Options are ours, not launch arguments; a path is a stock file too."""
    assert [(p.name, p.command) for p in by_path.plan(PathCfg())] == [
        ("by_path:inc", "ros2 launch /x/dies.launch.py r:=1"),
        ("w:f", "ros2 launch pkg f.launch.py use_sim_time:=true"),
    ]


# --------------------------------------------------------------- live
STOCK = textwrap.dedent(
    """
    from launch import LaunchDescription
    from launch.actions import ExecuteProcess


    def generate_launch_description():
        return LaunchDescription([
            ExecuteProcess(
                cmd=["sh", "-c", "echo hello; echo bye; sleep 1; exit 3"],
                name="dier", output="screen",
            ),
            ExecuteProcess(
                cmd=["sh", "-c", "sleep 0.5"], name="clean", output="screen",
            ),
            {survivor}
        ])
    """
)
SURVIVOR = 'ExecuteProcess(cmd=["sleep", "{secs}"], name="survivor", output="screen"),'


class LiveCfg(BaseModel):
    path: str
    mode: str = "wait"  # wait: the body waits; supervise: the body returns
    allow: list[str] = []


@launch
def live(config: LiveCfg):
    include(config.path, name="inc", allow=config.allow)
    if config.mode == "wait":
        sleep(4)
        shutdown("done")


def _running_with(marker: str) -> list[int]:
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if marker.encode() in f.read():
                    found.append(int(pid))
        except OSError:
            pass
    return found


@pytest.mark.live
@pytest.mark.parametrize(
    "mode, survivor, allow, code",
    [
        ("wait", True, [], 1),  # dies while the body waits
        ("supervise", False, [], 1),  # ros2 launch then exits 0 by itself
        ("wait", True, ["dier"], 0),  # allowed: shutdown() ends it cleanly
    ],
)
def test_live_stock_death(tmp_path, capsys, mode, survivor, allow, code):
    if shutil.which("ros2") is None:
        pytest.skip("ROS 2 not sourced")
    pytest.importorskip("launch")
    processes = pytest.importorskip("libtmux_easy.processes")
    secs = f"61.{os.getpid()}"  # unique: find it afterwards
    text = STOCK.format(survivor=SURVIVOR.format(secs=secs) if survivor else "")
    path = tmp_path / "dies.launch.py"
    path.write_text(text)

    procs = processes.Processes("t", log_dir=str(tmp_path / "logs"), prefix="")
    run = engine.Run(procs, label="live", params_dir=str(tmp_path))
    cfg = LiveCfg(path=str(path), mode=mode, allow=allow)
    assert engine.execute(live, cfg, run) == code
    out = capsys.readouterr().out

    if code:
        assert "dier-1 in live:inc died unexpectedly: exit code 3" in out
        assert "    | [dier-1] bye" in out
        assert "  dier-1 in live:inc: exit code 3" in out  # the final report
        assert [d.name for d in run.deaths] == ["dier-1"]
    else:
        assert "shutdown: done" in out and not run.deaths
    assert "clean-" not in "".join(d.name for d in run.deaths)
    # teardown: SIGINT to ros2 launch stopped what it started
    assert not any(p.running for p in run.started)
    assert _running_with(secs) == []
