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


def test_include_builds_the_child_eagerly_into_a_group_action():
    group = include(child, ChildConfig(n=3))
    assert isinstance(group, GroupAction)
    # A scoped GroupAction wraps its children in push/pop scope entities.
    assert "action-3" in group.get_sub_entities()


def test_include_rejects_the_wrong_config_type():
    with pytest.raises(TypeError, match="ChildConfig expected, got OtherConfig"):
        include(child, OtherConfig())


def test_stock_launch_entry_point_explains_itself():
    with pytest.raises(RuntimeError, match="run it with 'tlaunch'"):
        child.generate_launch_description()


# --------------------------------------------------------- stock interop
class Color(enum.Enum):
    RED = "red"


def test_stock_arg_converts_each_scalar_kind():
    assert stock_arg(True) == "true" and stock_arg(False) == "false"
    assert stock_arg(Color.RED) == "red"
    assert stock_arg(3) == "3" and stock_arg(1.5) == "1.5"
    assert stock_arg("x") == "x" and stock_arg(Path("/tmp/a")) == "/tmp/a"


def test_stock_arg_rejects_collections_and_models():
    with pytest.raises(TypeError, match="no stock launch argument string form"):
        stock_arg([1, 2])


def test_stock_args_builds_the_launch_arguments_list():
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


def test_fqn_of():
    assert fqn_of("talker", None) == "/talker"
    assert fqn_of("talker", "ns") == "/ns/talker"
    assert fqn_of("talker", "/ns/") == "/ns/talker"
    assert fqn_of(None, "ns") is None       # unknowable without a name


def test_node_passes_typed_ros_params():
    action = node("turtlesim", "turtlesim_node", name="t", params=DemoParams())
    assert params_of(action) == {"background_r": 69, "holonomic": False}


def test_node_rejects_a_plain_model_as_params():
    class NotParams(BaseModel):
        x: int = 1

    with pytest.raises(TypeError, match="takes a RosParams model"):
        node("p", "e", name="t", params=NotParams())


def test_node_merges_matching_untyped_sections():
    ros = rc.RosConfig([
        rc.Section("/**", {"use_sim_time": True}, {"/in": "/out"}, "f.yaml"),
        rc.Section("/ns/other", {"nope": 1}, {}, "f.yaml"),
    ])
    with rc.active(ros):
        action = node("turtlesim", "turtlesim_node", name="t", namespace="ns")
    assert params_of(action) == {"use_sim_time": True}
    assert remaps_of(action) == [("/in", "/out")]


def test_node_typed_wins_over_untyped(capsys):
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


def test_node_strict_refuses_untyped_values():
    ros = rc.RosConfig([rc.Section("/**", {"use_sim_time": True}, {}, "f.yaml")])
    with rc.active(ros):
        with pytest.raises(ValueError, match="strict=True"):
            node("turtlesim", "turtlesim_node", name="t", strict=True)


def test_node_outside_a_build_has_no_untyped_config():
    # No active RosConfig: the wrapper still works, it just adds nothing.
    action = node("turtlesim", "turtlesim_node", name="t")
    assert params_of(action) == {} and remaps_of(action) == []
