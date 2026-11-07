"""include(): a typed launch is a function call; a stock file is one process.
Stock including a typed launch: one tlaunch process."""

import enum
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, Field

from ros2_typed_launch import engine, include, launch, process, stock_arg


class ChildConfig(BaseModel):
    n: int = 1


class OtherConfig(BaseModel):
    n: int = 1


@launch(config=ChildConfig)
def child(config):
    process(f"child{config.n}", ["echo", str(config.n)])
    return config.n


@launch(config=ChildConfig)
def parent(config):
    assert include(child, ChildConfig(n=3)) == 3
    assert include(child, n=4) == 4
    include("demo_nodes_cpp", "talker.launch.py", name="talk", use_sim_time=True)


def test_include():
    plan = parent.plan(ChildConfig())
    assert [(p.name, p.command) for p in plan] == [
        ("parent:child3", "echo 3"),
        ("parent:child4", "echo 4"),
        ("parent:talk", "ros2 launch demo_nodes_cpp talker.launch.py use_sim_time:=true"),
    ]
    with engine.active(engine.Run(None, label="x")), pytest.raises(TypeError, match="include"):
        include(42)


# --------------------------------------------------------- stock interop
class Inner(BaseModel):
    r: int = Field(0, ge=0, le=255, description="Red")
    enabled: bool = True
    tags: list[str] = []


class StockConfig(BaseModel):
    name: str
    inner: Inner = Inner()


@launch(config=StockConfig)
def stocked(config):
    pass


def run_stock(typed, **args):
    """What stock does with an include: set the arguments, then run each entity.
    -> (the tlaunch command, its generated launch_args or None)."""
    from launch import LaunchContext

    context = LaunchContext()
    context.launch_configurations.update(args)
    *head, run = typed.generate_launch_description().entities
    for entity in head:
        entity.visit(context)
    (proc,) = run.visit(context)
    from launch.utilities import perform_substitutions

    cmd = [perform_substitutions(context, part) for part in proc.cmd]
    generated = None
    if cmd[-2] == "--config" and "tlaunch-stocked-" in cmd[-1]:
        generated = yaml.safe_load(open(cmd[-1]))["launch_args"]
    return cmd, generated


def test_stock_launch_includes_a_typed_one(tmp_path):
    from launch.actions import DeclareLaunchArgument

    declared = {
        e.name: e
        for e in stocked.generate_launch_description().entities
        if isinstance(e, DeclareLaunchArgument)
    }
    assert list(declared) == ["name", "inner.r", "inner.enabled", "inner.tags", "tlaunch_config"]
    assert declared["name"].default_value is None  # required: stock checks it
    assert declared["inner.r"].description == "Red (>=0, <=255)"

    cmd, generated = run_stock(stocked, name="a", **{"inner.r": "7", "inner.tags": "[x, y]"})
    assert cmd[:2] == ["tlaunch", __file__]  # not in a package: by path
    assert generated == {"name": "a", "inner": {"r": "7", "tags": ["x", "y"]}}

    # defaults < files < arguments: the files first, the arguments last.
    extra = tmp_path / "extra.yaml"
    extra.write_text("launch_args: {inner: {r: 5, enabled: false}}\n")
    cmd, generated = run_stock(stocked, name="a", tlaunch_config=str(extra))
    assert cmd[cmd.index("--config") + 1] == str(extra)
    assert generated == {"name": "a"}

    # Errors read as tlaunch's do, named by argument, at include time.
    with pytest.raises(Exception, match=r"(?s)typed launch 'stocked'.*inner\.r: ensure"):
        run_stock(stocked, name="a", **{"inner.r": "999"})
    with pytest.raises(Exception, match=r"did you mean 'inner\.enabled'"):
        run_stock(stocked, name="a", **{"inner.enabld": "x"})


def test_stock_target_args():
    from ros2_typed_launch.examples import turtle_demo
    from ros2_typed_launch.stock import target_args

    assert target_args(turtle_demo) == ["ros2_typed_launch", "examples.turtle_demo_launch"]


class Color(enum.Enum):
    RED = "red"


def test_stock_arg():
    assert stock_arg(True) == "true" and stock_arg(False) == "false"
    assert stock_arg(Color.RED) == "red"
    assert stock_arg(3) == "3" and stock_arg(1.5) == "1.5"
    assert stock_arg("x") == "x" and stock_arg(Path("/tmp/a")) == "/tmp/a"
