"""Every process's final command line (commands.py): node log levels, logging it."""

from types import SimpleNamespace

import pytest

from ros2_typed_launch import commands


def test_log_levels():
    assert commands.check_levels(["DEBUG", "rcl:=Warn"]) == ["debug", "rcl:=warn"]
    for bad in ("loud", ":=debug", "rcl:=", "rcl=debug"):
        with pytest.raises(ValueError, match="LOGGER:=LEVEL"):
            commands.check_levels([bad])
    assert commands.node_args([]) == []
    assert commands.node_args(["debug", "rcl:=warn"]) == [
        "--ros-args",
        "--log-level",
        "debug",
        "--log-level",
        "rcl:=warn",
    ]
    assert commands.patch([], False)() is None  # nothing to do: no hook


def test_the_hook_reaches_nodes_only_and_logs(capsys):
    pytest.importorskip("launch_ros")
    from launch.descriptions import Executable
    from launch_ros.actions import Node
    from launch.actions import ExecuteProcess

    original = Executable.prepare

    def fake_prepare(description, context, action):  # what launch would resolve
        description._Executable__final_cmd = ["prog", "a b"]
        description._Executable__final_name = "prog-1"

    Executable.prepare = fake_prepare
    try:
        undo = commands.patch(["debug"], True)
        for action, want in (
            (
                Node(package="p", executable="e"),
                ["prog", "a b", "--ros-args", "--log-level", "debug"],
            ),
            (ExecuteProcess(cmd=["prog"]), ["prog", "a b"]),  # not a node: untouched
        ):
            description = Executable(cmd=["prog"])
            Executable.prepare(description, SimpleNamespace(), action)
            assert description.final_cmd == want
        undo()
        assert Executable.prepare is fake_prepare
    finally:
        Executable.prepare = original
    logged = capsys.readouterr()
    assert "prog-1: prog 'a b' --ros-args --log-level debug" in logged.out + logged.err
