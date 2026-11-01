import enum
from pathlib import Path

import pytest

pytest.importorskip("launch")
pytest.importorskip("launch_ros")

from launch.actions import GroupAction  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from ros2_typed_launch import RosParams, include, launch, node  # noqa: E402
from ros2_typed_launch import ros_config as rc  # noqa: E402
from ros2_typed_launch.actions import fqn_of, stock_arg, stock_args  # noqa: E402


class ChildConfig(BaseModel):
    n: int = 1


class OtherConfig(BaseModel):
    n: int = 1


@launch(config=ChildConfig)
def child(config):
    return ["action-%d" % config.n]


def test_include():
    group = include(child, ChildConfig(n=3))
    assert isinstance(group, GroupAction)
    # A scoped GroupAction wraps its children in push/pop scope entities.
    assert "action-3" in group.get_sub_entities()

    with pytest.raises(TypeError, match="ChildConfig expected, got OtherConfig"):
        include(child, OtherConfig())



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
    return [config]                 # the built config, to look at


def run_stock(typed, **args):
    """What stock does with an include: set the arguments, then run each entity.
    -> the launch's actions (here: its config)."""
    from launch import LaunchContext

    context = LaunchContext()
    context.launch_configurations.update(args)
    *head, build = typed.generate_launch_description().entities
    for entity in head:
        entity.visit(context)
    return build.visit(context)[0]


def test_stock_launch_includes_a_typed_one(tmp_path):
    from launch.actions import DeclareLaunchArgument

    declared = {
        e.name: e for e in stocked.generate_launch_description().entities
        if isinstance(e, DeclareLaunchArgument)
    }
    assert list(declared) == ["name", "inner.r", "inner.enabled", "inner.tags", "tlaunch_config"]
    assert declared["name"].default_value is None           # required: stock checks it
    assert declared["inner.r"].description == "Red (>=0, <=255)"

    config = run_stock(stocked, name="a", **{"inner.r": "7", "inner.tags": "[x, y]"})
    assert config == StockConfig(name="a", inner=Inner(r=7, tags=["x", "y"]))

    # defaults < files < arguments; a declared default is not "given".
    extra = tmp_path / "extra.yaml"
    extra.write_text("launch_args: {inner: {r: 5, enabled: false}}\n")
    config = run_stock(stocked, name="a", tlaunch_config=str(extra))
    assert (config.inner.r, config.inner.enabled) == (5, False)
    config = run_stock(stocked, name="a", tlaunch_config=str(extra), **{"inner.r": "9"})
    assert config.inner.r == 9

    # Errors read as tlaunch's do, named by argument.
    with pytest.raises(Exception, match=r"(?s)typed launch 'stocked'.*inner\.r: ensure"):
        run_stock(stocked, name="a", **{"inner.r": "999"})
    with pytest.raises(Exception, match=r"did you mean 'inner\.enabled'"):
        run_stock(stocked, name="a", **{"inner.enabld": "x"})

    # A script has no launch description.
    from ros2_typed_launch import script

    @script(config=ChildConfig)
    def scripted(session, config):
        pass

    with pytest.raises(RuntimeError, match="run it with 'tlaunch'"):
        scripted.generate_launch_description()


class Color(enum.Enum):
    RED = "red"


def test_stock_args():
    assert stock_arg(True) == "true" and stock_arg(False) == "false"
    assert stock_arg(Color.RED) == "red"
    assert stock_arg(3) == "3" and stock_arg(1.5) == "1.5"
    assert stock_arg("x") == "x" and stock_arg(Path("/tmp/a")) == "/tmp/a"

    with pytest.raises(TypeError, match="no stock launch argument string form"):
        stock_arg([1, 2])

    assert stock_args({"name": "t1", "use_sim_time": True}) == [
        ("name", "t1"), ("use_sim_time", "true"),
    ]


# ------------------------------------------------------------- node() wrapper
class DemoParams(RosParams):
    background_r: int = Field(69, ge=0, le=255)
    holonomic: bool = False


def params_of(action) -> dict:
    """The parameters the node will actually receive.

    Read through launch_ros's own `evaluate_parameters`, not off the action's
    internals: stock Node normalizes a dict's keys into Substitution objects, so
    this asserts the real launch-time result rather than our input.
    """
    from launch import LaunchContext
    from launch_ros.utilities import evaluate_parameters

    merged = {}
    for entry in evaluate_parameters(LaunchContext(), action._Node__parameters or []):
        merged.update(entry)
    return merged


def remaps_of(action) -> list[tuple[str, str]]:
    from launch import LaunchContext
    from launch.utilities import perform_substitutions

    ctx = LaunchContext()
    return [
        (perform_substitutions(ctx, a), perform_substitutions(ctx, b))
        for a, b in action._Node__remappings or []
    ]


def test_node_typed_params():
    assert fqn_of("talker", None) == "/talker"
    assert fqn_of("talker", "ns") == "/ns/talker"
    assert fqn_of("talker", "/ns/") == "/ns/talker"
    assert fqn_of(None, "ns") is None       # unknowable without a name

    action = node("turtlesim", "turtlesim_node", name="t", params=DemoParams())
    assert params_of(action) == {"background_r": 69, "holonomic": False}

    class NotParams(BaseModel):
        x: int = 1

    with pytest.raises(TypeError, match="takes a RosParams model"):
        node("p", "e", name="t", params=NotParams())


def test_node_untyped_sections(capsys):
    # No active RosConfig: the wrapper still works, it just adds nothing.
    action = node("turtlesim", "turtlesim_node", name="t")
    assert params_of(action) == {} and remaps_of(action) == []

    ros = rc.RosConfig([
        rc.Section("/**", {"use_sim_time": True}, {"/in": "/out"}, "f.yaml"),
        rc.Section("/ns/other", {"nope": 1}, {}, "f.yaml"),
    ])
    with rc.active(ros):
        action = node("turtlesim", "turtlesim_node", name="t", namespace="ns")
    assert params_of(action) == {"use_sim_time": True}
    assert remaps_of(action) == [("/in", "/out")]

    ros = rc.RosConfig([rc.Section("/**", {"use_sim_time": True}, {}, "f.yaml")])
    with rc.active(ros):
        with pytest.raises(ValueError, match="strict=True"):
            node("turtlesim", "turtlesim_node", name="t", strict=True)

    def build(**flags):
        section = rc.Section("/**", {"background_r": 1}, {}, "f.yaml")
        with rc.active(rc.RosConfig([section], **flags)):
            action = node("turtlesim", "turtlesim_node", name="t", params=DemoParams())
        return action, section, capsys.readouterr().err

    action, section, err = build()
    assert params_of(action)["background_r"] == 69   # typed value wins
    assert err == ""                                # advisory only: quiet
    assert section.shadowed == {"background_r"}     # but recorded, for --show-config

    _, _, err = build(debug=True)
    assert "background_r" in err and "typed value wins" in err


