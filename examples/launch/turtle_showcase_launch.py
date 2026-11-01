# turtle_showcase_launch.py -- a tour of common ros2 launch Python constructs,
# each driven by typed config so it can be changed from the command line or a
# --config file. Stock launch / launch_ros actions are used as-is; the only
# tlaunch-specific parts are @launch and include().

from typing import Any, Literal

from pydantic import BaseModel, Field

from launch.actions import (
    ExecuteProcess,
    GroupAction,
    LogInfo,
    RegisterEventHandler,
    SetEnvironmentVariable,
    Shutdown,
    TimerAction,
)
from launch.event_handlers import OnProcessExit, OnProcessStart
from launch_ros.actions import Node, PushRosNamespace, SetParameter, SetRemap

from ros2_typed_launch import include, launch

from .localization_launch import localization, LocalizationConfig
from .turtle_teleop_launch import turtle_teleop, TeleopConfig


class ScopeConfig(BaseModel):
    """Settings applied to every node in the launch (via scoped actions)."""

    namespace: str = Field(
        "demo", description="Namespace for all nodes (PushRosNamespace)"
    )
    global_params: dict[str, Any] = Field(
        default_factory=lambda: {"use_sim_time": False},
        description="Parameters set on every node in scope (SetParameter)",
    )
    remap: dict[str, str] = Field(
        default_factory=dict,
        description="Remappings applied to every node in scope (SetRemap)",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables for every process (SetEnvironmentVariable)",
    )


class TimerConfig(BaseModel):
    """Delayed and timed actions (TimerAction)."""

    spawn: bool = Field(True, description="Spawn a second turtle after a delay")
    spawn_delay: float = Field(
        3.0, ge=0, description="Seconds to wait before spawning (TimerAction)"
    )
    stop_after: float = Field(
        0.0,
        ge=0,
        description="Shut the launch down after this many seconds (0 = never)",
    )


class LoggingConfig(BaseModel):
    """Logging (LogInfo, --log-level)."""

    level: Literal["debug", "info", "warn", "error"] = Field(
        "info", description="Log level passed to turtlesim (--ros-args --log-level)"
    )
    notes: list[str] = Field(
        default_factory=list, description="Messages to print at startup (LogInfo)"
    )


class ShowcaseConfig(BaseModel):
    """Tour of ros2 launch constructs, driven by typed config."""

    scope: ScopeConfig = ScopeConfig()
    timers: TimerConfig = TimerConfig()
    logging: LoggingConfig = LoggingConfig()
    teleop: bool = Field(False, description="Include the teleop launch (needs a tty)")


@launch(config=ShowcaseConfig)
def showcase(config: ShowcaseConfig):
    ns = config.scope.namespace

    # A node with typed parameters and its own arguments. Its namespace comes
    # from the PushRosNamespace in the group below, so it is left empty here.
    turtlesim = Node(
        package="turtlesim",
        executable="turtlesim_node",
        name="turtlesim",
        arguments=["--ros-args", "--log-level", config.logging.level],
        parameters=[{"background_r": 30, "background_g": 60, "background_b": 120}],
    )

    scoped: list = []

    # Namespaces: applies to everything below, including included launches.
    if ns:
        scoped.append(PushRosNamespace(ns))

    # "Set a parameter on all nodes", one SetParameter per entry. Nodes launched
    # by the included localization launch inherit it too.
    for name, value in config.scope.global_params.items():
        scoped.append(SetParameter(name=name, value=value))

    # Remap a topic for every node in scope.
    for src, dst in config.scope.remap.items():
        scoped.append(SetRemap(src=src, dst=dst))

    # Environment for every process started below.
    for key, value in config.scope.env.items():
        scoped.append(SetEnvironmentVariable(key, value))

    # Plain Python replaces IfCondition/UnlessCondition/LaunchConfiguration.
    for note in config.logging.notes:
        scoped.append(LogInfo(msg=note))

    scoped.append(turtlesim)
    scoped.append(include(localization, LocalizationConfig()))
    if config.teleop:
        scoped.append(include(turtle_teleop, TeleopConfig()))

    # ExecuteProcess: run an arbitrary command; it sees the environment set
    # above. Only when there is something to show.
    if config.scope.env:
        scoped.append(
            ExecuteProcess(
                cmd=["printenv", *config.scope.env],
                name="env_check",
                output="screen",
            )
        )

    # Timer + ExecuteProcess: call a ROS service after a delay.
    if config.timers.spawn:
        service = f"/{ns}/spawn" if ns else "/spawn"
        scoped.append(
            TimerAction(
                period=config.timers.spawn_delay,
                actions=[
                    LogInfo(msg=f"spawning a second turtle via {service}"),
                    ExecuteProcess(
                        cmd=[
                            "ros2",
                            "service",
                            "call",
                            service,
                            "turtlesim/srv/Spawn",
                            "{x: 2.0, y: 2.0, theta: 0.0, name: 'turtle2'}",
                        ],
                        name="spawn_turtle2",
                        output="screen",
                    ),
                ],
            )
        )

    # Timer + Shutdown: stop cleanly after a while.
    if config.timers.stop_after > 0:
        scoped.append(
            TimerAction(
                period=config.timers.stop_after,
                actions=[
                    LogInfo(msg="stop_after elapsed"),
                    Shutdown(reason="stop_after elapsed"),
                ],
            )
        )

    return [
        LogInfo(msg="showcase: starting"),
        # Event handlers: react to a process starting / exiting.
        RegisterEventHandler(
            OnProcessStart(
                target_action=turtlesim,
                on_start=[LogInfo(msg="turtlesim is up")],
            )
        ),
        RegisterEventHandler(
            OnProcessExit(
                target_action=turtlesim,
                on_exit=[
                    LogInfo(msg="turtlesim exited: shutting everything down"),
                    Shutdown(reason="turtlesim exited"),
                ],
            )
        ),
        GroupAction(scoped),
    ]


generate_launch_description = showcase.generate_launch_description
