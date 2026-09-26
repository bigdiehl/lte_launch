from __future__ import annotations

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
