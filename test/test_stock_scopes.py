"""A stock include gets the body's ros_params() and ros_remaps() scopes, and
universal config sections, through a generated wrapper file; not namespace()."""

from __future__ import annotations

import subprocess
import textwrap

import pytest
import yaml

from _live import need_ros, until
from lte_launch import engine, include, namespace, ros_params, ros_remaps
from lte_launch import ros_config as rc
from lte_launch import stock_scopes


def _section(pattern, params=None, remaps=None):
    return rc.Section(pattern, params or {}, remaps or {}, source="cli")


def test_stock_include_gets_the_scopes(tmp_path):
    every = _section("/**", {"rate": 5, "use_sim_time": False}, {"/b": "/c"})
    some = _section("/**/talker", {"other": 1})
    ros = rc.RosConfig([every, some])
    run = engine.Run(None, label="x", ros=ros, params_dir=str(tmp_path))
    with engine.active(run):
        plain = include("demo_nodes_cpp", "talker.launch.py", name="plain")
        with namespace("outer"), ros_params(use_sim_time=True, name="1"):
            with ros_remaps({"/tf": "tf"}):
                inc = include("demo_nodes_cpp", "talker.launch.py", n=3)
                again = include("demo_nodes_cpp", "talker.launch.py", n=4)
        by_path = include(str(tmp_path / "f.launch.py"))

    # no scopes, but a /** section: still wrapped (it carries the section)
    assert plain.argv[2].endswith("/x.plain.launch.py")
    assert inc.key == "outer:talker" and inc.argv[0:2] == ["ros2", "launch"]
    wrapper, args = inc.argv[2], inc.argv[3:]
    assert wrapper == str(tmp_path / "outer.talker.launch.py") and args == ["n:=3"]
    assert again.argv[2] == str(tmp_path / "outer.talker.2.launch.py")
    assert [p.command for p in run.plan][1].startswith("ros2 launch ")

    text = open(wrapper).read()
    compile(text, wrapper, "exec")
    assert "PushRosNamespace" not in text  # a stock file places its own nodes
    assert "SetRemap('/tf', 'tf')" in text and "SetRemap('/b', '/c')" in text
    assert "package_name='demo_nodes_cpp', file_name='talker.launch.py'" in text
    assert "for n in ['n']]" in text
    params_path = wrapper.replace(".launch.py", ".params.yaml")
    assert f"SetParametersFromFile({params_path!r})" in text
    # the config section beats the scope; a string stays a string
    assert yaml.safe_load(open(params_path)) == {
        "/**": {"ros__parameters": {"use_sim_time": False, "name": "1", "rate": 5}}
    }
    assert "other" not in open(params_path).read()  # not universal: not passed
    assert every.matched == [
        "x:plain (stock include)",
        "outer:talker (stock include)",
        "outer:talker (stock include)",
        "x:f (stock include)",
    ]
    assert some.matched == []
    assert f"path = {str(tmp_path / 'f.launch.py')!r}" in open(by_path.argv[2]).read()

    # nothing to apply: the target itself, as before
    run2 = engine.Run(None, label="y", params_dir=str(tmp_path / "y"))
    with engine.active(run2):
        bare = include("demo_nodes_cpp", "talker.launch.py", a=1)
    assert bare.argv == ["ros2", "launch", "demo_nodes_cpp", "talker.launch.py", "a:=1"]
    assert stock_scopes.wrapper(
        "p", "f.launch.py", params={}, remaps={},
        directory=str(tmp_path), stem="s",
    ) is None  # fmt: skip


def test_stock_wrapper_loads():
    """The wrapper is a real launch file: stock parses it into the actions."""
    pytest.importorskip("launch_ros")
    import importlib.util
    import tempfile

    from launch.actions import GroupAction, IncludeLaunchDescription
    from launch_ros.actions import SetParametersFromFile, SetRemap

    d = tempfile.mkdtemp(prefix="stockscopes_")
    path = stock_scopes.wrapper(
        None, "/nowhere/f.launch.py", params={"p": 1},
        remaps={"x": "y"}, directory=d, stem="w", args=["a"],
    )  # fmt: skip
    spec = importlib.util.spec_from_file_location("w", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (group,) = module.generate_launch_description().entities
    assert isinstance(group, GroupAction)
    kinds = [type(a) for a in group.get_sub_entities()]
    assert SetParametersFromFile in kinds and SetRemap in kinds
    assert IncludeLaunchDescription in kinds


STOCK = """\
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("node_name"),
        Node(package="turtlesim", executable="turtlesim_node",
             name=LaunchConfiguration("node_name")),
    ])
"""


def _param(node: str, name: str) -> str | None:
    got = subprocess.run(
        ["ros2", "param", "get", "--no-daemon", node, name],
        capture_output=True, text=True, timeout=30,
    )  # fmt: skip
    return got.stdout.strip() if got.returncode == 0 else None


@pytest.mark.live
def test_stock_include_scopes_live(make_run, probe, uniq, tmp_path):
    need_ros("turtlesim", "lte_launch")
    file = tmp_path / "sim.launch.py"
    file.write_text(textwrap.dedent(STOCK))
    run = make_run()
    with engine.active(run):
        with namespace(uniq), ros_params(background_g=7, use_sim_time=True):
            with ros_remaps({"turtle1/pose": f"{uniq}_pose"}):
                # not namespaced by the scope: its node is /<uniq>
                proc = include(str(file), node_name=uniq)
                # by package; its own parameters win over the scope's, as stock
                pkg = include("lte_launch", "spawn_turtle.launch.py",
                              namespace=f"{uniq}/inner", background_r=3)  # fmt: skip
        full = f"/{uniq}"
        probe.wait_node(full, 30)
        assert until(lambda: _param(full, "background_g"), 30, "the param") == (
            "Integer value is: 7"
        )
        assert probe.param(full, "use_sim_time") is True
        until(lambda: probe.publishers(f"/{uniq}_pose"), 15, "the remap")
        inner = f"/{uniq}/inner/turtlesim"
        probe.wait_node(inner, 30)
        assert probe.param(inner, "background_r") == 3
        assert probe.param(inner, "background_g") == 7
        assert probe.param(inner, "use_sim_time") is False  # its own "false"
    run.teardown()
    assert not proc.running and not pkg.running
