import enum
from pathlib import Path

import pytest

pytest.importorskip("launch")

from launch.actions import GroupAction  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from ros2_typed_launch import include, launch  # noqa: E402
from ros2_typed_launch.actions import stock_arg, stock_args  # noqa: E402


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
