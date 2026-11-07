# turtle_bringup_launch.py -- turtlesim brought up in order.
#
# The body runs top to bottom and each node() starts its process right there.
# Waits block. Ctrl-C or a death stops everything, all at once. No action
# lists, no generate_launch_description line (the decorator exports it).
#
#   tlaunch lte_launch turtle_bringup_launch.py                # subprocesses
#   tlaunch lte_launch turtle_bringup_launch.py --tmux         # libtmux_easy panes
#   tlaunch lte_launch turtle_bringup_launch.py --tmux-dash --teleop
#   tlaunch lte_launch turtle_bringup_launch.py --dry-run      # the plan only
#   tlaunch lte_launch turtle_bringup_launch.py --crash_after 3 ; echo $?

from pydantic import BaseModel, Field

from lte_launch import RosParams
from lte_launch import launch, log, namespace, node, process, window


class TurtleColor(RosParams):
    """turtlesim's own ROS parameters."""

    background_r: int = Field(69, ge=0, le=255, description="Red channel")
    background_g: int = Field(86, ge=0, le=255, description="Green channel")
    background_b: int = Field(255, ge=0, le=255, description="Blue channel")


class BringupConfig(BaseModel):
    """turtlesim, brought up in order."""

    namespace: str = Field("demo", description="Namespace for every node")
    turtlesim: TurtleColor = TurtleColor()
    drive: bool = Field(True, description="Publish cmd_vel once turtlesim is up")
    teleop: bool = Field(False, description="Keyboard teleop (needs a TTY: --tmux)")
    crash_after: float = Field(
        0, ge=0, description="A helper dies after this many seconds (0 = never)"
    )


@launch
def turtle_bringup(config: BringupConfig):
    with namespace(config.namespace):
        sim = node("turtlesim", "turtlesim_node", name="turtlesim", params=config.turtlesim)
        sim.wait_for_node()
        log(f"turtlesim is up (pid {sim.pid})")

        if config.drive:
            process(
                "driver",
                f"ros2 topic pub -r 2 /{config.namespace}/turtle1/cmd_vel "
                "geometry_msgs/msg/Twist '{linear: {x: 1.2}, angular: {z: 0.9}}'",
                ready="publishing",
            )
        if config.teleop:
            with window("input"):
                node("turtlesim", "turtle_teleop_key", name="teleop")

    if config.crash_after:
        process("flaky", f"sh -c 'sleep {config.crash_after}; echo giving up; exit 3'")
