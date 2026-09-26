# turtle_teleop_launch.py -- keyboard teleop for a turtlesim turtle.
#
# An ordinary Python module (part of the ros2_typed_launch_demo package): run
# it with `tlaunch ros2_typed_launch_demo turtle_teleop_launch`, or import it
# directly, e.g. `from ros2_typed_launch_demo.turtle_teleop_launch import
# turtle_teleop, TeleopConfig`.

from pydantic import BaseModel

from launch_ros.actions import Node

from ros2_typed_launch import launch


class TeleopConfig(BaseModel):
    namespace: str = ""


@launch(config=TeleopConfig)
def turtle_teleop(config: TeleopConfig):
    return [
        Node(
            package="turtlesim",
            executable="turtle_teleop_key",
            namespace=config.namespace,
        )
    ]


generate_launch_description = turtle_teleop.generate_launch_description
