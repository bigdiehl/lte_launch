"""In-body ROS scopes (``ros_params``, ``ros_remaps``), per-process
``prefix=``, and the components ``TypedLaunch.start(procs)`` holds in a
container started elsewhere."""

from __future__ import annotations

import os
import subprocess
import time

import pytest
from _live import need_ros
import yaml
from pydantic import BaseModel

from ros2_typed_launch import (
    container, container_at, launch, namespace, node, process, ros_params,
    ros_remaps, shutdown,
)  # fmt: skip
from ros2_typed_launch import engine
from ros2_typed_launch import ros_config as rc

SIM = ("turtlesim", "turtlesim_node")
TALKER = ("composition", "composition::Talker")


class Empty(BaseModel):
    pass


@launch
def scoped(config: Empty):
    with ros_params(use_sim_time=True, rate=1), ros_remaps({"/tf": "tf"}):
        node(*SIM, name="a")
        with namespace("n"), ros_params({"rate": 2, "qos": {"depth": 5}}):
            node(*SIM, name="b", params={"rate": 3})
            node(*SIM, name="c", remaps={"/scan": "/own"})
        with ros_remaps({"/tf": "/tf_inner"}):
            box = container("box")
            box.load(*TALKER, name="talker")
    node(*SIM, name="d")


def _dry(launch_, ros=None, **run_args):
    run = engine.Run(None, label="x", ros=ros, **run_args)
    with engine.active(run):
        launch_.factory(launch_.config_type())
    return {p.name: p for p in run.plan}


def _params(argv):
    if "--params-file" not in argv:
        return {}
    path = argv[argv.index("--params-file") + 1]
    return yaml.safe_load(open(path))["/**"]["ros__parameters"]


def _remaps(argv):
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-r"]
    return dict(p.split(":=", 1) for p in pairs if not p.startswith("__"))


def test_ros_scopes_dry_run():
    pytest.importorskip("ament_index_python")
    # Config sections win over scopes; a node's own params=/remaps= over both.
    ros = rc.RosConfig(
        [
            rc.Section("/n/c", {"rate": 9}, {"/scan": "/sec", "/tf": "/sec_tf"}, "f"),
            rc.Section("/**/b", {"use_sim_time": False}, {}, "f"),
        ]
    )
    plan = _dry(scoped, ros=ros)

    a = plan["x:a"].argv
    assert _params(a) == {"use_sim_time": True, "rate": 1}
    assert _remaps(a) == {"/tf": "tf"}

    # Nested scopes merge, the inner wins; dicts flatten to dotted names.
    b = plan["n:b"].argv
    assert _params(b) == {"use_sim_time": False, "rate": 3, "qos.depth": 5}
    c = plan["n:c"].argv
    assert _params(c) == {"use_sim_time": True, "rate": 9, "qos.depth": 5}
    assert _remaps(c) == {"/tf": "/sec_tf", "/scan": "/own"}
    # Scope values are code, not config: no "shadowed" note for them.
    assert ros.sections[0].shadowed == set() and ros.sections[1].shadowed == set()

    # Containers and components loaded into them honour the scopes too.
    box = plan["x:box"].argv
    assert _params(box) == {"use_sim_time": True, "rate": 1}
    assert _remaps(box) == {"/tf": "/tf_inner"}
    load = plan["x:talker"].command
    assert "-p use_sim_time:=true -p rate:=1 -r /tf:=/tf_inner" in load

    # Outside every scope: nothing.
    assert _params(plan["x:d"].argv) == {} and _remaps(plan["x:d"].argv) == {}

    with engine.active(engine.Run(None, label="x")):
        with pytest.raises(TypeError, match="dict or a RosParams"):
            ros_params(["use_sim_time"])
        try:
            import rclpy  # noqa: F401  (the name check needs it)
        except ImportError:
            return
        with pytest.raises(rc.RosConfigError, match="not a valid ROS name"):
            ros_remaps({"/a b": "/c"})


@launch
def prefixed(config: Empty):
    node(*SIM, name="plain")
    node(*SIM, name="gdb", prefix="gdb -ex run --args")
    node(*SIM, name="bare", prefix="")
    process("shell", "echo hi", prefix="time")
    process("argv", ["echo", "hi"], prefix=["nice", "-n", "5"])
    container("box", prefix="valgrind --tool=callgrind")


def test_prefix_dry_run():
    pytest.importorskip("ament_index_python")
    plan = _dry(prefixed)
    assert plan["x:plain"].argv[0].endswith("/turtlesim_node")
    assert plan["x:gdb"].argv[:4] == ["gdb", "-ex", "run", "--args"]
    assert plan["x:gdb"].argv[4].endswith("/turtlesim_node")
    assert plan["x:shell"].command == "time echo hi"
    assert plan["x:argv"].argv == ["nice", "-n", "5", "echo", "hi"]
    assert plan["x:box"].argv[:2] == ["valgrind", "--tool=callgrind"]

    # A process's own prefix replaces --launch-prefix, filter or not ("": none).
    plan = _dry(prefixed, launch_prefix="valgrind -q", launch_prefix_filter="turtlesim")
    assert plan["x:plain"].argv[:2] == ["valgrind", "-q"]
    assert plan["x:gdb"].argv[0] == "gdb"
    assert plan["x:bare"].argv[0].endswith("/turtlesim_node")
    assert plan["x:box"].argv[0] == "valgrind" and "-q" not in plan["x:box"].argv
    assert plan["x:argv"].argv[0] == "nice"
    # A shell line never takes --launch-prefix (as before), only its own.
    assert plan["x:shell"].command == "time echo hi"


# ------------------------------------------------------------------ live
def _live_ros():
    need_ros()
    pytest.importorskip("libtmux_easy.processes")


class Probe(BaseModel):
    ns: str = f"scope_live_{os.getpid()}"


got: dict = {}


@launch
def live_scoped(config: Probe):
    with namespace(config.ns), ros_params(background_r=12):
        sim = node(*SIM, name="turtlesim", params={"background_g": 34})
        sim.wait_for_node()
    full = f"/{config.ns}/turtlesim"
    for name in ("background_r", "background_g"):
        got[name] = subprocess.run(
            ["ros2", "param", "get", "--no-daemon", full, name],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()  # fmt: skip
    shutdown("checked")


@pytest.mark.live
def test_live_scoped_param_reaches_node(tmp_path):
    _live_ros()
    from libtmux_easy.processes import Processes

    procs = Processes("t", log_dir=str(tmp_path), prefix="")
    run = engine.Run(procs, label="live", params_dir=str(tmp_path))
    got.clear()
    assert engine.execute(live_scoped, Probe(), run) == 0
    assert got == {
        "background_r": "Integer value is: 12",
        "background_g": "Integer value is: 34",
    }


@launch
def into_ext(config: Empty):
    container_at("/ext/box").load(*TALKER, name="talker")


def _components(box: str) -> list[str]:
    """The components ``box`` has loaded, from its ListNodes service."""
    import rclpy
    from composition_interfaces.srv import ListNodes
    from rclpy.context import Context

    context = Context()
    rclpy.init(args=[], context=context)
    probe = rclpy.create_node(f"_probe_{os.getpid()}", context=context)
    try:
        client = probe.create_client(ListNodes, f"{box}/_container/list_nodes")
        assert client.wait_for_service(timeout_sec=10)
        future = client.call_async(ListNodes.Request())
        from rclpy.executors import SingleThreadedExecutor

        executor = SingleThreadedExecutor(context=context)
        executor.add_node(probe)
        executor.spin_until_future_complete(future, timeout_sec=10)
        return list(future.result().full_node_names)
    finally:
        probe.destroy_node()
        context.try_shutdown()


@pytest.mark.live
def test_start_unloads_held_components_on_close(tmp_path):
    _live_ros()
    from libtmux_easy.processes import Processes

    exe = "/opt/ros/jazzy/lib/rclcpp_components/component_container"
    if not os.path.exists(exe) or not os.path.isdir("/opt/ros/jazzy/share/composition"):
        pytest.skip("rclcpp_components / composition not installed")
    ext = subprocess.Popen(
        [exe, "--ros-args", "-r", "__node:=box", "-r", "__ns:=/ext"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        procs = Processes("t", log_dir=str(tmp_path), prefix="")
        run = into_ext.start(procs)
        assert [c.name for c in run.held] == ["/talker"]
        assert _components("/ext/box") == ["/talker"]
        procs.close()  # what a test's teardown_class does
        assert run.held == [] and run._graph is None
        deadline = time.monotonic() + 10
        while _components("/ext/box") and time.monotonic() < deadline:
            time.sleep(0.2)
        assert _components("/ext/box") == []
        procs.close()  # a second close does nothing
    finally:
        ext.terminate()
        ext.wait(10)
