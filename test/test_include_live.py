"""Includes: a typed include runs inside the parent's scopes (dry); a stock
include as one live `ros2 launch` process; stock `ros2 launch` including a
typed launch, live."""

from __future__ import annotations

import os
import shutil
import signal

import pytest
from pydantic import BaseModel

from _live import Reader, need_ros
from lte_launch import engine, environment, include, launch, namespace, node
from lte_launch import window


class Inner(BaseModel):
    name: str = "sim"


@launch
def inner(config: Inner):
    with namespace("inner"):
        sim = node("turtlesim", "turtlesim_node", name=config.name)
    run = engine.current()
    return sim, run.namespace, dict(run.env)


class Outer(BaseModel):
    pass


@launch
def outer(config: Outer):
    with namespace("outer"), window("w"), environment(X=1):
        got = include(inner, name="a")
    got2 = include(inner, Inner(name="b"))
    return got, got2


def test_typed_include_runs_in_the_parents_scopes():
    pytest.importorskip("ament_index_python")
    run = engine.Run(None, label="outer")
    with engine.active(run):
        (sim, ns, env), (sim2, ns2, env2) = outer.factory(Outer())
    assert (sim.fqn, sim.window, ns, env) == (
        "/outer/inner/a",
        "w",
        "/outer",
        {"X": "1"},
    )
    assert (sim2.fqn, sim2.window, ns2, env2) == ("/inner/b", "inner", "/", {})
    assert [p.name for p in run.plan] == ["w:a", "inner:b"]
    assert "__ns:=/outer/inner" in run.plan[0].argv


@pytest.mark.live
def test_stock_include(make_run, probe, uniq):
    need_ros("turtlesim", "lte_launch")
    run = make_run()
    with engine.active(run):
        proc = include(
            "lte_launch", "spawn_turtle.launch.py", name="spawn",
            namespace=uniq, use_sim_time=True, background_r=12,
        )  # fmt: skip
        assert proc.key == "t:spawn"
        assert proc.argv[:4] == [
            "ros2",
            "launch",
            "lte_launch",
            "spawn_turtle.launch.py",
        ]
        assert proc.argv[4:] == [
            f"namespace:={uniq}",
            "use_sim_time:=true",
            "background_r:=12",
        ]
        probe.wait_node(f"/{uniq}/turtlesim", 30)
        assert probe.param(f"/{uniq}/turtlesim", "background_r") == 12
        assert probe.param(f"/{uniq}/turtlesim", "use_sim_time") is True
    run.teardown()
    assert not proc.running
    probe.wait_node(f"/{uniq}/turtlesim", present=False)


@pytest.mark.live
def test_stock_launch_includes_typed(spawn, probe, uniq):
    need_ros("turtlesim", "lte_launch")
    pytest.importorskip("libtmux_easy.processes")  # lte-launch needs it
    if not shutil.which("lte-launch"):
        pytest.skip("lte-launch not on PATH (source the workspace's install/setup.bash)")
    out = Reader(spawn(["ros2", "launch", "lte_launch", "stock_parent.launch.xml",
                        f"namespace:={uniq}"]))  # fmt: skip
    probe.wait_node(f"/{uniq}/turtlesim", 40)
    out.wait_for(r"\[lte-launch\]: up: 1 process", 20)
    assert probe.param(f"/{uniq}/turtlesim", "background_r") == 200
    assert probe.param(f"/{uniq}/turtlesim", "background_b") == 90  # quiet.yaml
    assert f"/{uniq}/teleop" not in probe.nodes()  # quiet.yaml turns it off
    # SIGINT to ros2 launch alone (`kill -INT`): it passes one on to
    # lte-launch. (To the whole group, lte-launch gets ours and then ros2 launch's,
    # which is a second Ctrl-C: it kills, and a SIGKILLed node lingers in the
    # graph until its DDS lease runs out.)
    os.kill(out.proc.pid, signal.SIGINT)
    assert out.finish(30) == 0
    assert "interrupted; stopping" in out.text and "second interrupt" not in out.text
    probe.wait_node(f"/{uniq}/turtlesim", present=False)
