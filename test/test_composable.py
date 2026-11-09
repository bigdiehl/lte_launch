"""Composable nodes, live, with composition's real components: our
containers (every kind), standalone placements, a container started
elsewhere (held, unloaded at teardown, lost), failed loads, parameters,
remappings and log levels reaching a component. Verified from outside with
a probe node (ListNodes, GetParameters, the graph) and the output."""

from __future__ import annotations

import os
import re
import signal
import time

import pytest
from pydantic import BaseModel

from _live import Reader, later, need_ros, until
from lte_launch import RosParams, component, container, container_at, engine
from lte_launch import launch, log, namespace
from lte_launch import ros_config as rc
from lte_launch.composable import EXECUTABLES

pytestmark = pytest.mark.live

T = ("composition", "composition::Talker")


@pytest.fixture(autouse=True)
def _ros():
    need_ros("composition", "rclcpp_components", "composition_interfaces")


class Nothing(BaseModel):
    x: int = 0


def execute(make_run, body, **run_args):
    run = make_run(**run_args)
    code = engine.execute(launch(config=Nothing)(body), Nothing(), run)
    return code, run


@pytest.fixture
def external(spawn, probe, uniq):
    """An rclcpp_components container started by the test, not the run:
    -> (its full name, its Popen)."""
    from ament_index_python.packages import get_package_prefix

    exe = os.path.join(
        get_package_prefix("rclcpp_components"), "lib", "rclcpp_components",
        "component_container",
    )  # fmt: skip
    ns = f"/{uniq}_ext"
    proc = spawn([exe, "--ros-args", "-r", "__node:=box", "-r", f"__ns:={ns}"])
    probe.wait_node(f"{ns}/box")
    return f"{ns}/box", proc


def test_our_container_every_kind(make_run, probe, uniq):
    for kind in EXECUTABLES:
        run = make_run()
        with engine.active(run), namespace(uniq):
            box = container(f"box_{kind}", kind=kind)
            talker = box.load(*T, name=f"talker_{kind}")
            assert talker.wait_for_node(20) == f"/{uniq}/talker_{kind}"
            assert talker.name == f"/{uniq}/talker_{kind}" and talker.unique_id
            assert probe.components(box.fqn) == [talker.name]
            assert box.components == [talker] and not box.external
            assert run.held == []  # ours: it dies with the container

            # The process handle's attributes, through the container.
            assert box.proc.argv[0].endswith(f"/rclcpp_components/{EXECUTABLES[kind]}")
            assert box.pid == box.proc.pid and box.running and box.window == uniq
            assert box.wait_for_node() == box.fqn == f"/{uniq}/box_{kind}"

            # unload(): gone from the container, the node from the graph.
            talker.unload()
            assert probe.components(box.fqn) == []
            probe.wait_node(talker.name, present=False)
        run.teardown()
        assert not box.running and run.deaths == []


def test_our_container_dying_is_the_death(make_run, capsys):
    def body(config):
        box = container("box")
        box.load(*T, name="talker")
        later(0.3, os.kill, box.pid, signal.SIGKILL)

    code, run = execute(make_run, body)
    out = capsys.readouterr().out
    assert code == 1 and [p.fqn for p in run.deaths] == ["/box"]
    assert "/box (t:box) died unexpectedly: killed by SIGKILL" in out


def test_placement(make_run, probe, uniq):
    need_ros("demo_nodes_cpp")
    ns = f"/{uniq}"
    run = make_run()
    with engine.active(run), namespace(uniq):
        # "" / None: a container of its own, named after the component.
        alone = component(*T, name="alone")
        assert alone.container.fqn == f"{ns}/alone_container"
        assert probe.components(f"{ns}/alone_container") == [f"{ns}/alone"]

        # executable=: a plain node, no container at all.
        exe = component("demo_nodes_cpp", "demo_nodes_cpp::Talker", name="exe",
                        executable="talker")  # fmt: skip
        assert exe.container is None and exe.proc.fqn == exe.name == f"{ns}/exe"
        exe.wait_for_node(20)

        # into="box": started on first use, then reused (one process).
        a = component(*T, name="a", into="box")
        b = component(*T, name="b", into="box")
        c = component(*T, name="c", into=f"{ns}/box")  # the same, by full name
        assert a.container is b.container is c.container
        assert [p.name for p in run.started].count("box") == 1
        assert probe.components(f"{ns}/box") == [f"{ns}/a", f"{ns}/b", f"{ns}/c"]

        # container(kind=) first, then into= its name.
        mt = container("mt", kind="multithreaded")
        d = component(*T, name="d", into="mt")
        e = component(*T, name="e", into=mt)  # or the handle
        assert d.container is mt and e.container is mt
        assert probe.components(mt.fqn) == [f"{ns}/d", f"{ns}/e"]
        with pytest.raises(
            engine.LaunchError, match=f"container {ns}/mt is already started"
        ):
            container("mt")

        # A standalone node's unload() stops it.
        exe.unload()
        assert not exe.proc.running and run.deaths == []
    run.teardown()
    assert not any(p.running for p in run.started)
    probe.wait_node(f"{ns}/box", present=False)


def test_external_container(make_run, probe, external, uniq, capsys):
    ext, ext_proc = external
    seen = {}
    pid = os.getpid()

    def body(config):
        with namespace(uniq):
            held = component(*T, name="held", into=ext)  # absolute: container_at
            box = container_at(ext)
            assert held.container is box and box.external
            tmp = box.load(*T, name="tmp")
            seen["loaded"] = sorted(probe.components(ext))
            with pytest.raises(
                AttributeError, match=f"container {ext} was started elsewhere"
            ):
                box.pid
            tmp.unload()  # explicit: unloaded now, no longer held
            seen["unloaded"] = probe.components(ext)
            seen["held"] = [c.name for c in engine.current().held]

        # The run holds it with nothing of its own running: still up after
        # two of supervise's checks (every 2 s), then Ctrl-C.
        def stop():
            seen["during"] = probe.components(ext)
            os.kill(pid, signal.SIGINT)

        later(4.5, stop)

    t = time.monotonic()
    code, run = execute(make_run, body)
    took = time.monotonic() - t
    out = capsys.readouterr().out
    held = f"/{uniq}/held"
    assert seen["loaded"] == sorted([held, f"/{uniq}/tmp"])
    assert seen["unloaded"] == [held] and seen["held"] == [held]
    assert seen["during"] == [held] and took > 4
    assert code == 0, out
    assert "up: 0 process(es) running, 1 held elsewhere" in out
    assert f"{ext}: loaded composition::Talker as {held}" in out
    assert f"unloaded {held} from {ext}" in out
    # Unloaded at teardown; the container itself is not ours to stop.
    assert probe.components(ext) == [] and ext_proc.poll() is None


def test_external_container_leaving_fails_the_run(make_run, probe, external, capsys):
    ext, ext_proc = external

    def body(config):
        component(*T, name="held", into=ext)
        later(0.3, os.killpg, ext_proc.pid, signal.SIGINT)

    t = time.monotonic()
    code, run = execute(make_run, body)
    out = capsys.readouterr().out
    assert code == 1, out
    assert f"container {ext} left the ROS graph (started elsewhere)" in out
    assert run.held == [] and time.monotonic() - t < 15


def test_missing_container_and_failed_loads(make_run, uniq, capsys):
    run = make_run()
    with engine.active(run), namespace(uniq):
        # Nothing there: a timeout naming the service, and nothing recorded.
        t = time.monotonic()
        with pytest.raises(
            engine.WaitTimeout,
            match=f"container /{uniq}/nobody .its /{uniq}/nobody/_container/load_node",
        ):
            container_at("nobody", timeout=0.5)
        assert time.monotonic() - t < 3 and f"/{uniq}/nobody" not in run.containers

        box = container("box")
        with pytest.raises(engine.LaunchError, match=f"/{uniq}/box: failed to load "
                           f"composition::Nope .composition. as /{uniq}/x"):  # fmt: skip
            box.load("composition", "composition::Nope", name="x")
        with pytest.raises(engine.LaunchError, match="failed to load"):
            box.load("no_such_package", "x::Y")
        # Extra arguments reach the container: a bad one is refused there.
        with pytest.raises(engine.LaunchError, match="use_intra_process_comms"):
            box.load(*T, name="bad", extra={"use_intra_process_comms": "yes"})
        ipc = box.load(*T, name="ipc", intra_process=True)
        assert ipc.name == f"/{uniq}/ipc" and box.running and run.deaths == []

    # Through a whole run: the failed load stops it at that line, exit 1.
    reached = []

    def body(config):
        box = container("box2")
        box.load("composition", "composition::Nope")
        reached.append(box)

    code, run = execute(make_run, body)
    out = capsys.readouterr().out
    assert code == 1 and reached == []
    assert "[ERROR] [lte-launch]: /box2: failed to load composition::Nope" in out
    assert not any(p.running for p in run.started)


class SimTime(RosParams):
    use_sim_time: bool = True


def test_params_and_remaps_reach_components(make_run, probe, uniq):
    ns = f"/{uniq}"
    ros = rc.RosConfig(
        [
            rc.Section(f"{ns}/fromfile", {"use_sim_time": True}, {"chatter": "said_file"}, "f.yaml"),
            # typed params= win over a section
            rc.Section(f"{ns}/typed", {"use_sim_time": False}, {}, "f.yaml"),
        ]
    )  # fmt: skip
    run = make_run(ros=ros, log_commands=True)
    with engine.active(run), namespace(uniq):
        box = container("box")
        a = box.load(*T, name="fromfile")
        b = box.load(
            *T, name="typed", params=SimTime(), remaps={"chatter": "said_typed"}
        )
        c = box.load(*T, name="dict", params={"use_sim_time": True})
        plain = box.load(*T, name="plain")
        alone = component(*T, name="alone", params={"use_sim_time": True},
                          remaps={"chatter": f"{ns}/said_alone"})  # fmt: skip
        for comp in (a, b, c, alone):
            assert probe.param(comp.name, "use_sim_time") is True, comp.name
        assert probe.param(plain.name, "use_sim_time") is False
        for topic in ("said_file", "said_typed", "said_alone", "chatter"):
            until(lambda: probe.publishers(f"{ns}/{topic}") >= 1, 10, topic)
        assert probe.publishers(f"{ns}/chatter") == 2  # dict and plain
        log("checked")
    run.teardown()
    assert {s.pattern for s in ros.unmatched()} == set()
    assert ros.sections[1].shadowed == {"use_sim_time"}


def published(output: str, logger: str) -> int:
    """How many of a Talker's INFO lines (``Publishing: ...``) are there."""
    return len(re.findall(rf"\[{re.escape(logger)}\]: Publishing:", output))


def test_a_components_log_level(make_run, uniq):
    # Our container: --log-level is on its command line, so it reaches the
    # components in it -- here the LOGGER:=LEVEL form, for one of them (a
    # component's logger is its namespace and name, dotted).
    run = make_run(log_levels=[f"{uniq}.quiet:=warn"])
    with engine.active(run), namespace(uniq):
        box = container("box")
        assert f"{uniq}.quiet:=warn" in box.proc.argv
        box.load(*T, name="quiet")
        box.load(*T, name="loud")
        until(lambda: published(box.output(), f"{uniq}.loud") >= 2, 15, "loud's lines")
        assert published(box.output(), f"{uniq}.quiet") == 0, box.output()

        # ... and the bare LEVEL form, for the whole container.
        run.log_levels = ["warn"]
        hush = container("hush")
        assert hush.proc.argv[-2:] == ["--log-level", "warn"]
        hush.load(*T, name="hushed")
        n = published(box.output(), f"{uniq}.loud")  # talkers: 1 Hz
        until(lambda: published(box.output(), f"{uniq}.loud") >= n + 2, 15, "more")
        assert published(hush.output(), f"{uniq}.hushed") == 0, hush.output()
    run.teardown()
    assert run.deaths == []


@pytest.mark.xfail(
    reason="Jazzy's rclcpp_components ignores LoadNode's log_level (no "
    "logger call in libcomponent_manager), so --log-level cannot reach a "
    "component in a container started elsewhere",
    strict=True,
)
def test_a_components_log_level_in_a_container_started_elsewhere(
    make_run, external, uniq
):
    ext, ext_proc = external
    out = Reader(ext_proc)
    run = make_run(log_levels=["warn"])  # LoadNode.log_level = WARN (30)
    try:
        with engine.active(run), namespace(uniq):
            box = container_at(ext)
            box.load(*T, name="quiet")
            run.log_levels = []
            box.load(*T, name="loud")
            until(lambda: published(out.text, f"{uniq}.loud") >= 3, 15, "loud's")
            assert published(out.text, f"{uniq}.quiet") == 0
    finally:
        run.teardown()  # unload while the container is still up
