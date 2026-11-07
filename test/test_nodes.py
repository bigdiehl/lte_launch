"""node() options reaching a real ROS node: params= (RosParams and dict),
param_files=, config-file node sections, ros_arguments=, arguments=,
remaps=, --log-level (both forms), --launch-prefix on a node."""

from __future__ import annotations

import pytest

from _live import need_ros, until
from ros2_typed_launch import RosParams, engine, namespace, node
from ros2_typed_launch import ros_config as rc

pytestmark = pytest.mark.live


class Blue(RosParams):
    background_b: int = 33


def cmdline(pid: int) -> list[str]:
    with open(f"/proc/{pid}/cmdline", "rb") as f:
        return f.read().decode().split("\0")[:-1]


def test_parameters_and_arguments_reach_the_node(make_run, probe, uniq, tmp_path):
    need_ros("turtlesim", "demo_nodes_cpp")
    ns = f"/{uniq}"
    given = tmp_path / "given.yaml"
    given.write_text(
        "/**:\n  ros__parameters:\n    background_r: 11\n    background_g: 1\n"
    )
    ros = rc.RosConfig(
        [rc.Section(f"{ns}/sim", {"background_g": 22, "background_b": 0}, {}, "f.yaml")]
    )
    run = make_run(ros=ros)
    with engine.active(run), namespace(uniq):
        sim = node(
            "turtlesim", "turtlesim_node", name="sim",
            params=Blue(),                         # typed: beats the section
            param_files=[str(given)],              # under the section and typed
            ros_arguments=["-p", "use_sim_time:=true"],
            arguments=["--tl-extra"],
        )  # fmt: skip
        sim.wait_for_node(20)
        assert probe.param(sim.fqn, "background_r") == 11
        assert probe.param(sim.fqn, "background_g") == 22
        assert probe.param(sim.fqn, "background_b") == 33
        assert probe.param(sim.fqn, "use_sim_time") is True

        argv = cmdline(sim.pid)
        assert argv[1] == "--tl-extra" and argv[2] == "--ros-args"
        assert argv[-2:] == ["-p", "use_sim_time:=true"]
        assert argv.count("--params-file") == 2 and str(given) in argv

        # params= as a plain dict; remaps= relative to the namespace.
        talker = node("demo_nodes_cpp", "talker", name="talker",
                      params={"use_sim_time": True}, remaps={"chatter": "heard"})  # fmt: skip
        talker.wait_for_node(20)
        assert probe.param(talker.fqn, "use_sim_time") is True
        until(lambda: probe.publishers(f"{ns}/heard") == 1, 10, "the remapped topic")
        assert probe.publishers(f"{ns}/chatter") == 0
    run.teardown()
    assert ros.sections[0].shadowed == {"background_b"}


def test_log_levels_and_prefix(make_run, uniq):
    need_ros("demo_nodes_cpp")

    # --log-level LEVEL: every logger; LOGGER:=LEVEL: that one.
    for levels, want in ((["debug"], "[DEBUG]"), (["rcl:=debug"], "[rcl]")):
        run = make_run(log_levels=levels)
        with engine.active(run), namespace(uniq):
            talker = node("demo_nodes_cpp", "talker", name="talker")
            assert talker.argv[-2:] == ["--log-level", levels[0]]
            line = talker.wait_for(r"\[DEBUG\]", 20)
            assert want in line
        run.teardown()

    # --launch-prefix on a node: the prefix is the process, the node its child.
    run = make_run(launch_prefix="env TL_PREFIX=1", launch_prefix_filter="talker$")
    with engine.active(run), namespace(uniq):
        talker = node("demo_nodes_cpp", "talker", name="talker2")
        assert talker.argv[:2] == ["env", "TL_PREFIX=1"]
        talker.wait_for_node(20)
        environ = open(f"/proc/{talker.pid}/environ", "rb").read().split(b"\0")
        assert b"TL_PREFIX=1" in environ
    run.teardown()
