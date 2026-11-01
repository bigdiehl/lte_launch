"""Every process's final command line: node log levels, and logging it.

``--log-level`` gives every ROS node ``--ros-args --log-level ...``;
``--log-commands`` logs each command line as it starts. Both hook
``Executable.prepare``, the same place ``tmux.patch`` does, so they reach the
processes launch creates on its own too -- from an included file, a timer or a
condition -- which no walk over the launch description can see. Installed
before tmux's hook, so the command logged is the real one, not its pane
stand-in.
"""

from __future__ import annotations

import shlex
from typing import Callable

#: rcutils' severities, as ``--log-level`` takes them.
LEVELS = ("debug", "info", "warn", "error", "fatal")


def check_levels(values: list[str]) -> list[str]:
    """``LEVEL`` or ``LOGGER:=LEVEL`` each; -> the values, levels lower-cased.
    Raises ``ValueError`` naming the bad one."""
    checked = []
    for value in values:
        logger, sep, level = value.rpartition(":=")
        if level.lower() not in LEVELS or (sep and not logger):
            raise ValueError(
                f"--log-level '{value}': expected LEVEL or LOGGER:=LEVEL, "
                f"LEVEL one of {', '.join(LEVELS)}"
            )
        checked.append(f"{logger}{sep}{level.lower()}")
    return checked


def node_args(levels: list[str]) -> list[str]:
    """What a node's command line gets for these levels."""
    if not levels:
        return []
    args = ["--ros-args"]
    for level in levels:
        args += ["--log-level", level]
    return args


def patch(levels: list[str], log_commands: bool) -> Callable[[], None]:
    """Hook ``Executable.prepare``. Returns an undo function (a no-op when there
    is nothing to do)."""
    if not levels and not log_commands:
        return lambda: None
    import launch.logging
    from launch.descriptions import Executable

    original = Executable.prepare
    extra = node_args(levels)

    def prepare(description, context, action):
        original(description, context, action)
        cmd = description.final_cmd
        if not cmd:
            return
        if extra and _is_node(action):
            cmd = [*cmd, *extra]
            description._Executable__final_cmd = cmd
        if log_commands:
            name = description.final_name or cmd[0]
            launch.logging.get_logger("tlaunch").info(f"{name}: {shlex.join(cmd)}")

    Executable.prepare = prepare

    def undo():
        Executable.prepare = original

    return undo


def _is_node(action) -> bool:
    try:
        from launch_ros.actions import Node
    except ImportError:
        return False
    return isinstance(action, Node)
