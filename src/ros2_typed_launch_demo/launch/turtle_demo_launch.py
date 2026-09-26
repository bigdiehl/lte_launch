# turtle_demo_launch.py -- turtlesim, plus the teleop and localization
# launches, included as ordinary sibling modules of this package.
#
# Two ways to expose an included launch in this launch's config:
#   * teleop: a small parent-owned toggle; the child's config is built by hand,
#     entirely from other fields of this launch.
#   * localization: the child's own config class is embedded as a field (not
#     subclassed -- composition, not inheritance), so every field it has
#     (frames, x, y, yaw, ...) is directly overridable
#     (--localization.params.x, ...) alongside the extra `enabled` switch.
#
# turtle_teleop and localization are plain relative imports here because they
# are sibling modules of this file, both inside the ros2_typed_launch_demo.launch
# subpackage (installed from the top-level launch/ directory -- see CMakeLists.txt).
# This file is itself imported as ros2_typed_launch_demo.launch.turtle_demo_launch,
# which is how `tlaunch ros2_typed_launch_demo launch.turtle_demo_launch` loads it.
# For a launch in another package, or picked dynamically by name, use
# `ros2_typed_launch.load_launch("pkg", "module")` instead -- it is the same
# mechanism (a real import), just addressed by string.

from pydantic import BaseModel, Field

from launch_ros.actions import Node

from ros2_typed_launch import include, launch

from .localization_launch import localization, LocalizationConfig
from .turtle_teleop_launch import turtle_teleop, TeleopConfig


class TurtlesimConfig(BaseModel):
    namespace: str = ""
    background_r: int = Field(0, ge=0, le=255)
    background_g: int = Field(0, ge=0, le=255)
    background_b: int = Field(0, ge=0, le=255)


class TeleopToggle(BaseModel):
    enabled: bool = True


class LocalizationToggle(BaseModel):
    """Whether to run localization, and its own config."""

    enabled: bool = True
    params: LocalizationConfig = LocalizationConfig()


class TurtleDemoConfig(BaseModel):
    turtlesim: TurtlesimConfig = TurtlesimConfig()
    teleop: TeleopToggle = TeleopToggle()
    localization: LocalizationToggle = LocalizationToggle()


@launch(config=TurtleDemoConfig)
def turtle_demo(config: TurtleDemoConfig):
    actions = [
        Node(
            package="turtlesim",
            executable="turtlesim_node",
            namespace=config.turtlesim.namespace,
            parameters=[
                {
                    "background_r": config.turtlesim.background_r,
                    "background_g": config.turtlesim.background_g,
                    "background_b": config.turtlesim.background_b,
                }
            ],
        )
    ]

    if config.teleop.enabled:
        actions.append(
            include(
                turtle_teleop,
                TeleopConfig(namespace=config.turtlesim.namespace),
            )
        )

    if config.localization.enabled:
        # config.localization.params is already a LocalizationConfig -- pass it
        # straight through, unless the namespace was left at its default, in
        # which case default it to turtlesim's.
        params = config.localization.params
        if not params.namespace:
            params = params.copy(update={"namespace": config.turtlesim.namespace})
        actions.append(include(localization, params))

    return actions


generate_launch_description = turtle_demo.generate_launch_description
