# turtle_teleop_launch.py -- keyboard teleop for a turtlesim turtle.
#
# An ordinary Python module (part of the ros2_typed_launch package): run
# it with `tlaunch ros2_typed_launch turtle_teleop_launch.py --tmux` (it needs a TTY), or import it
# directly, e.g. `from ros2_typed_launch.examples.turtle_teleop_launch import
# turtle_teleop, TeleopConfig`.

from pydantic import BaseModel

from ros2_typed_launch import launch, node, window


class TeleopConfig(BaseModel):
    namespace: str = ""


@launch
def turtle_teleop(config: TeleopConfig):
    with window("input"):
        node("turtlesim", "turtle_teleop_key", name="teleop", namespace=config.namespace)
