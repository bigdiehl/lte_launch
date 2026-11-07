"""The engine: command lines, dry runs, node parameters, composable loads, and
live runs of plain processes on libtmux_easy (no ROS needed for those).

ROS nodes live, tmux and lte_launch_test: examples/lte_test/run.sh.
"""

from __future__ import annotations

import time

import pytest
import yaml
from pydantic import BaseModel, Field

from lte_launch import (
    RosParams, component, container, container_at, environment, launch, namespace,
)  # fmt: skip
from lte_launch import include, node, process, shutdown, sleep, window
from lte_launch import command as cmd
from lte_launch import engine
from lte_launch import ros_config as rc
from _live import SLOW


def test_command_line():
    assert cmd.join_namespace("/", "demo") == "/demo"
    assert cmd.join_namespace("/a", "b", None, "") == "/a/b"
    assert cmd.join_namespace("/a", "/abs") == "/abs"
    assert cmd.join_namespace("") == "/"
    assert cmd.fqn("sim", "/demo") == "/demo/sim"
    assert cmd.fqn("sim", "/") == "/sim"
    assert cmd.fqn(None, "/demo") is None

    argv = cmd.node_command(
        "/x/turtlesim_node",
        name="sim",
        namespace="/demo",
        params_path="/p.yaml",
        remaps={"turtle1/cmd_vel": "/joy"},
        arguments=["--flag"],
        log_levels=["debug"],
        param_files=["/given.yaml"],
        ros_arguments=["--enclave", "/e"],
    )
    assert argv == [
        "/x/turtlesim_node", "--flag", "--ros-args",
        "-r", "__node:=sim", "-r", "__ns:=/demo",
        "--params-file", "/given.yaml", "--params-file", "/p.yaml",
        "-r", "turtle1/cmd_vel:=/joy",
        "--log-level", "debug", "--enclave", "/e",
    ]  # fmt: skip
    assert cmd.node_command("/x/e", name=None, namespace="/") == ["/x/e", "--ros-args"]

    assert cmd.with_prefix(["/x/a"], ["gdb", "--args"], None) == ["gdb", "--args", "/x/a"]
    assert cmd.with_prefix(["/x/a"], ["gdb"], "b$") == ["/x/a"]
    assert cmd.check_levels(["DEBUG", "rcl:=Warn"]) == ["debug", "rcl:=warn"]
    with pytest.raises(ValueError, match="LOGGER:=LEVEL"):
        cmd.check_levels(["loud"])


class Color(RosParams):
    background_r: int = Field(69, ge=0, le=255)
    background_g: int = 86


class Cfg(BaseModel):
    ns: str = "t"
    color: Color = Color()
    teleop: bool = True


@launch(config=Cfg)
def sample(config):
    with namespace(config.ns):
        sim = node("turtlesim", "turtlesim_node", name="sim", params=config.color)
        sim.wait_for_node()  # passes at once in a dry run
        process("driver", "ros2 topic pub /x std_msgs/msg/Empty", cwd="/tmp")
        if config.teleop:
            with window("input"), environment(TERM="xterm"):
                node("turtlesim", "turtle_teleop_key", name="teleop")
    process("helper", ["sh", "-c", "exit 0"])


def dry(config=None, ros=None, **run_args):
    run = engine.Run(None, label="sample", ros=ros, **run_args)
    with engine.active(run):
        sample.factory(config or Cfg())
    return run


def test_dry_run_plan(tmp_path):
    pytest.importorskip("ament_index_python")

    # In start order, named window:name; the window follows the namespace.
    assert [n for n, _ in sample.panes(ns="a/b")] == [
        "a-b:sim", "a-b:driver", "input:teleop", "sample:helper",
    ]  # fmt: skip
    assert [n for n, _ in sample.panes(teleop=False)] == ["t:sim", "t:driver", "sample:helper"]

    plan = {p.name: p for p in dry().plan}
    assert plan["t:driver"].command == "cd /tmp && ros2 topic pub /x std_msgs/msg/Empty"
    assert plan["sample:helper"].command == "sh -c 'exit 0'"
    sim = plan["t:sim"].argv
    assert sim[0].endswith("/turtlesim/turtlesim_node")
    assert sim[sim.index("-r", 3) : sim.index("-r", 3) + 2] == ["-r", "__ns:=/t"]

    # --launch-prefix, filtered by executable.
    plan = dry(launch_prefix="valgrind -q", launch_prefix_filter="teleop").plan
    assert [p.argv[:2] for p in plan if p.argv] == [
        [plan[0].argv[0], plan[0].argv[1]], ["valgrind", "-q"], ["sh", "-c"],
    ]  # fmt: skip

    # A typo'd executable fails at its line, before anything starts.
    with pytest.raises(cmd.CommandError, match="no executable 'nope'.*turtlesim_node"):
        cmd.find_executable("turtlesim", "nope")
    with pytest.raises(cmd.CommandError, match="package 'no_such_pkg' not found"):
        cmd.find_executable("no_such_pkg", "x")


def test_node_parameters(capsys):
    pytest.importorskip("ament_index_python")

    def params(run, key):
        argv = next(p.argv for p in run.plan if p.name == key)
        if "--params-file" not in argv:
            return {}
        return yaml.safe_load(open(argv[argv.index("--params-file") + 1]))["/**"][
            "ros__parameters"
        ]

    # Typed only.
    assert params(dry(), "t:sim") == {"background_r": 69, "background_g": 86}

    # Node sections reach the node through the namespace() scope; typed wins,
    # quietly, but it is recorded for --show-config.
    section = rc.Section(
        "/t/sim", {"background_r": 1, "extra": "x"}, {"turtle1/cmd_vel": "/joy"}, "f.yaml"
    )
    ros = rc.RosConfig([section, rc.Section("/nobody", {"a": 1}, {}, "f.yaml")])
    run = dry(ros=ros)
    assert params(run, "t:sim") == {"background_r": 69, "extra": "x", "background_g": 86}
    assert "turtle1/cmd_vel:=/joy" in run.plan[0].argv
    assert section.shadowed == {"background_r"}
    assert [s.pattern for s in ros.unmatched()] == ["/nobody"]
    assert capsys.readouterr().err == ""

    ros = rc.RosConfig([rc.Section("/**", {"background_r": 1}, {}, "f.yaml")], debug=True)
    dry(ros=ros)
    err = capsys.readouterr().err
    assert "background_r" in err and "typed value wins" in err

    class NotParams(BaseModel):
        x: int = 1

    with engine.active(engine.Run(None, label="x")):
        with pytest.raises(TypeError, match="takes a RosParams model"):
            node("turtlesim", "turtlesim_node", name="t", params=NotParams())


def test_composable_dry_run():
    pytest.importorskip("ament_index_python")
    ros = rc.RosConfig([rc.Section("/p/talker", {"rate": 5}, {}, "f.yaml")])
    run = engine.Run(None, label="x", ros=ros, log_levels=["debug"])
    with engine.active(run), namespace("p"):
        box = container("box", kind="multithreaded")
        talker = box.load("composition", "composition::Talker", name="talker",
                          remaps={"chatter": "/c"}, intra_process=True)  # fmt: skip
    assert box.fqn == "/p/box" and talker.name == "/p/talker"
    assert run.plan[0].argv[0].endswith("/rclcpp_components/component_container_mt")
    assert run.plan[1].name == "p:talker"
    assert run.plan[1].command == (
        "ros2 component load /p/box composition composition::Talker "
        "--node-namespace /p --node-name talker -p rate:=5 -r chatter:=/c "
        "-e use_intra_process_comms:=true --log-level DEBUG"
    )
    with engine.active(run), pytest.raises(ValueError, match="container kind"):
        container("c", kind="huge")


def test_component_placement():
    pytest.importorskip("ament_index_python")
    T = ("composition", "composition::Talker")
    run = engine.Run(None, label="x")
    with engine.active(run), namespace("p"):
        alone = component(*T, name="a")                     # a container of its own
        ours = component(*T, name="b", into="box")          # ours, started now
        again = component(*T, name="c", into="/p/box")      # the same one, by full name
        ext = component(*T, name="d", into="/robot/box")    # started elsewhere
        assert container_at("/robot/box") is ext.container
        with pytest.raises(engine.LaunchError, match="already started"):
            container("box")
    assert alone.container.fqn == "/p/a_container" and not alone.container.external
    assert ours.container is again.container and ours.container.fqn == "/p/box"
    assert ext.container.external and ext.name == "/p/d"
    loads = [p.command for p in run.plan if p.command.startswith("ros2 component")]
    assert [c.split()[3] for c in loads] == ["/p/a_container", "/p/box", "/p/box", "/robot/box"]
    assert len(run.plan) == 6  # two containers started, four loads
    with pytest.raises(AttributeError, match="started elsewhere"):
        ext.container.pid


def test_outside_a_run():
    with pytest.raises(RuntimeError, match="only work inside"):
        node("turtlesim", "turtlesim_node")


# ------------------------------------------------------------------ live


class Live(BaseModel):
    mode: str = "die"


@launch(config=Live)
def live(config):
    if config.mode == "die":
        a = process("a", SLOW, ready="up")
        process("b", SLOW, ready="up")
        process("c", "sleep 0.3; echo bye; exit 3")
        sleep(30)  # the death fails this wait
        assert a  # not reached
    elif config.mode == "required":
        process("a", SLOW, ready="up")
        process("main", "sleep 0.3", required=True)
    elif config.mode == "respawn":
        process("flaky", "echo run; sleep 0.2; exit 1", respawn=0.1, respawn_max=2)
    elif config.mode == "shutdown":
        process("a", SLOW, ready="up")
        shutdown("done")


def run_live(tmp_path, mode):
    processes = pytest.importorskip("libtmux_easy.processes")
    procs = processes.Processes("t", log_dir=str(tmp_path), prefix="")
    run = engine.Run(procs, label="live", params_dir=str(tmp_path))
    t = time.monotonic()
    code = engine.execute(live, Live(mode=mode), run)
    return code, time.monotonic() - t, run


@pytest.mark.live
def test_live_death_and_parallel_teardown(tmp_path, capsys):
    code, took, run = run_live(tmp_path, "die")
    out = capsys.readouterr().out
    assert code == 1
    assert "live:c died unexpectedly: exit code 3" in out and "| bye" in out
    assert [p.key for p in run.deaths] == ["live:c"]
    # a and b each take 1.5 s to stop: in parallel, not one after the other
    assert took < 4.5, took
    assert not any(p.running for p in run.started)


@pytest.mark.live
def test_live_required_shutdown_respawn(tmp_path, capsys):
    code, _, run = run_live(tmp_path / "r", "required")
    assert code == 0 and "live:main (required) ended" in capsys.readouterr().out

    code, _, run = run_live(tmp_path / "s", "shutdown")
    assert code == 0 and "shutdown: done" in capsys.readouterr().out

    code, _, run = run_live(tmp_path / "p", "respawn")
    out = capsys.readouterr().out
    assert out.count("respawning in 0.1 s") == 2
    assert code == 1 and "live:flaky died unexpectedly" in out  # the third end


class Annotated_(BaseModel):
    x: int = 1


def test_launch_decorator_forms():
    @launch
    def bare(config: Annotated_):
        pass

    @launch(config=Annotated_)
    def named(config):
        pass

    assert bare.config_type is Annotated_ and named.config_type is Annotated_
    assert bare.generate_launch_description.typed_target is bare
    @launch
    def sig(ns: str = "demo", rate: int = Field(5, ge=1), nested: Annotated_ = Annotated_()):
        """Short help."""
        seen.update(ns=ns, rate=rate, nested=nested.x)

    seen: dict = {}
    model = sig.config_type
    assert model.__name__ == "SigConfig" and model.__doc__ == "Short help."
    with engine.active(engine.Run(None, label="x")):
        include(sig, rate=7)
    assert seen == {"ns": "demo", "rate": 7, "nested": 1}
    with pytest.raises(engine.LaunchError, match="SigConfig: rate: "):
        include(sig, rate=0)  # Field constraints hold
    with pytest.raises(TypeError, match="needs a type annotation"):

        @launch
        def missing(config):
            pass

    with pytest.raises(TypeError, match=r"\*args"):

        @launch
        def star(*names: str):
            pass
