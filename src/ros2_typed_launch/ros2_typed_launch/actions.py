from __future__ import annotations

import enum
from pathlib import Path
from typing import Any

from .core import TypedLaunch


def include(child: TypedLaunch, config: Any):
    """Include another typed launch, passing it a typed config object.

    The child's actions are built right away (so mistakes surface before
    anything starts) and returned as a scoped ``GroupAction``: a normal launch
    action that can be used anywhere one can (inside another ``GroupAction``,
    a ``TimerAction``, an event handler...). Parameters, remappings, namespaces
    and environment set by the parent scope are inherited by the child.
    """
    from launch.actions import GroupAction

    if not isinstance(config, child.config_type):
        raise TypeError(
            f"{child.config_type.__name__} expected, "
            f"got {type(config).__name__}"
        )
    return GroupAction(actions=child.build(config))


def stock_arg(value: Any) -> str:
    """Convert one typed config value to the plain string a stock (untyped)
    launch file's arguments are always made of -- for ``IncludeLaunchDescription
    (launch_arguments=...)``, a ``DeclareLaunchArgument``'s ``default_value``, or
    anywhere else stock ``launch``/``launch_ros`` expects a string that gets
    substituted with ``LaunchConfiguration`` inside the included file.

    Bools become ``"true"``/``"false"`` (ROS's own spelling, e.g. ``use_sim_time``);
    an ``Enum``/``Literal`` value becomes its plain value. Collections and nested
    models have no single agreed string form -- convert those yourself first.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, enum.Enum):
        return str(value.value)
    if isinstance(value, (str, int, float, Path)):
        return str(value)
    raise TypeError(
        f"no stock launch argument string form for {type(value).__name__} "
        f"({value!r}) -- stock launch arguments are flat strings; convert "
        "collections/nested models yourself first"
    )


def stock_args(mapping: dict[str, Any]) -> list[tuple[str, str]]:
    """``{name: value}`` -> the ``launch_arguments`` list ``IncludeLaunchDescription``
    (and friends) expect -- convert a handful of your typed config's values to
    stock launch argument strings in one call::

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(path),
            launch_arguments=stock_args({"turtle_name": config.name, "x": config.x}),
        )

    One-way only: stock launch args are untyped strings with no validation, so
    there is nothing to convert back the other way (typed -> stock only; see
    working/design-notes.md, Q17).
    """
    return [(name, stock_arg(value)) for name, value in mapping.items()]
