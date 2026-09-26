import pytest

pytest.importorskip("launch")

from launch.actions import GroupAction  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from ros2_typed_launch import include, launch  # noqa: E402


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
