from __future__ import annotations

import enum
import sys
from pathlib import Path
from typing import Any

from . import ros_config as rc
from . import schema
from .core import TypedLaunch

PROG_WARN = "tlaunch: warning:"


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
            f"{child.config_type.__name__} expected, " f"got {type(config).__name__}"
        )
    return GroupAction(actions=child.build(config))


def window(name: str, actions: list):
    """Group `actions` into one tmux window, when a run uses ``--tmux``.

    Without ``--tmux`` this changes nothing at all: it only sets an environment
    variable, which the pane machinery reads to decide which window a process
    belongs in (see tmux.py). That is why it works around an *include* -- the
    variable is inherited by every process the included launch starts, however
    deep, including a stock launch file's.

        window("teleop", [include(turtle_teleop, TeleopConfig())])

    The group is scoped, so the variable does not leak to anything after it.
    """
    from launch.actions import GroupAction, SetEnvironmentVariable

    from .tmux import WINDOW_ENV

    return GroupAction(
        scoped=True,
        actions=[SetEnvironmentVariable(WINDOW_ENV, name), *actions],
    )


def fqn_of(name: str | None, namespace: str | None) -> str | None:
    """A node's fully-qualified name, as the node patterns match against it.

    None when no ``name`` was given: rcl then picks the executable's own default
    name at startup, which we cannot know here, so only a pattern that matches
    every node can apply. An enclosing ``PushRosNamespace`` is *not* accounted
    for either -- it is resolved at launch time, after this -- so a node that
    wants targeted config should carry its own ``name``/``namespace``.
    """
    if not name:
        return None
    namespace = (namespace or "").strip("/")
    return f"/{namespace}/{name}" if namespace else f"/{name}"


def node(
    package: str,
    executable: str,
    *,
    name: str | None = None,
    namespace: str | None = None,
    params: Any = None,
    parameters: list | None = None,
    remappings: list | None = None,
    strict: bool = False,
    **kwargs,
):
    """A ``launch_ros`` ``Node`` that also receives the config aimed at it.

    Everything is passed through to stock ``Node``; on top of that:

    * ``params`` takes a `schema.RosParams` model from the launch's own typed
      config -- its fields become this node's ROS parameters, no restating;
    * any ``ros__parameters`` / ``ros__remappings`` section of a config file (or
      ``--ros-param`` / ``--ros-remap``) whose node pattern matches this node is
      merged in as well.

    A typed field and an untyped section setting the same parameter is not an
    error: the **typed value wins** and a warning names both, so the effective
    value is never a surprise (design-notes.md, Q8 decision 5). ``strict=True``
    makes that, and any untyped value reaching this node, an error instead.
    """
    from launch_ros.actions import Node

    typed: dict[str, Any] = {}
    if params is not None:
        if not schema.is_ros_params(params):
            raise TypeError(
                f"node(params=...) takes a RosParams model, got "
                f"{type(params).__name__}; subclass ros2_typed_launch.RosParams "
                "to mark a config section as a node's ROS parameters"
            )
        typed = rc.flatten_params(schema.to_plain(params))

    fqn = fqn_of(name, namespace)
    ros = rc.current()
    untyped, remaps = ros.for_node(fqn)
    where = fqn or f"{package}/{executable}"

    if strict and (untyped or remaps):
        offending = sorted(untyped) + [f"{k}:={v}" for k, v in remaps.items()]
        raise ValueError(
            f"{where}: strict=True, but config sets untyped values for it "
            f"({', '.join(offending)}); declare them in the schema as a "
            "RosParams model, or drop strict"
        )

    clashes = sorted(set(typed) & set(untyped))
    if clashes:
        # Recorded always, so --show-config can mark the shadowed value; printed
        # only under -d, since the effective value is the typed one either way.
        ros.note_shadowed(fqn, clashes)
        if ros.debug:
            print(
                f"{PROG_WARN} {where}: ros__parameters also sets "
                f"{', '.join(repr(c) for c in clashes)}, which this launch "
                f"declares as typed config; the typed value wins "
                f"({', '.join(f'{c}={typed[c]!r}' for c in clashes)})",
                file=sys.stderr,
            )

    merged = {**untyped, **typed}  # typed wins
    return Node(
        package=package,
        executable=executable,
        name=name,
        namespace=namespace,
        parameters=([merged] if merged else []) + list(parameters or []),
        remappings=list(remaps.items()) + list(remappings or []),
        **kwargs,
    )


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
