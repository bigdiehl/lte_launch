# turtle_teleop_launch.py -- keyboard teleop for a turtlesim turtle.
#
# An ordinary Python module (part of the lte_launch package): run
# it with `tlaunch lte_launch turtle_teleop_launch.py --tmux` (it needs a TTY), or import it
# directly, e.g. `from lte_launch.examples.turtle_teleop_launch import
# turtle_teleop, TeleopConfig`.
#
# The signature is the schema: one flag per parameter (--namespace), no config
# class. turtle_teleop.config_type is the generated model.

from lte_launch import launch, node, window


@launch
def turtle_teleop(namespace: str = ""):
    """Keyboard teleop for a turtlesim turtle."""
    with window("input"):
        node("turtlesim", "turtle_teleop_key", name="teleop", namespace=namespace)


TeleopConfig = turtle_teleop.config_type
