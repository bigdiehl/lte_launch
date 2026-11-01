# tmux_demo_launch.py -- one tmux pane per process, and windows to group them.
#
# Run it normally and it is an ordinary launch: every node's output lands in
# this terminal, interleaved. Add --tmux and each process gets its own pane,
# with its own scrollback and its own *real* terminal -- which is also why
# turtle_teleop_key only works this way (under plain launch it gives up with
# "Failed to get old console mode").
#
# Try it:
#   tlaunch ros2_typed_launch tmux_demo_launch.py --tmux
#   tmux attach -t tlaunch-tmux_demo          # in another terminal
#
#   # only some of them; the rest stay in this terminal as usual
#   tlaunch ros2_typed_launch tmux_demo_launch.py --tmux-only 'turtlesim*'
#
# The teleop include is wrapped in window("teleop", ...), so everything it
# starts -- however deep -- shares one window. That is all `window` does: set
# an environment variable the pane machinery reads. Without --tmux it has no
# effect at all.

from pydantic import BaseModel, Field

from launch.actions import ExecuteProcess

from ros2_typed_launch import include, launch, node, window

from ros2_typed_launch.examples.turtle_teleop_launch import (
    TeleopConfig,
    turtle_teleop,
)


class TmuxDemoConfig(BaseModel):
    """turtlesim, a chatty process, and teleop in a window of its own."""

    namespace: str = Field("demo", description="Namespace for the nodes")
    teleop: bool = Field(True, description="Include the teleop launch")
    chatter: bool = Field(True, description="Add a process that prints steadily")


@launch(config=TmuxDemoConfig)
def tmux_demo(config: TmuxDemoConfig):
    actions = [
        node(
            package="turtlesim",
            executable="turtlesim_node",
            name="turtlesim",
            namespace=config.namespace,
            output="screen",
        )
    ]
    if config.chatter:
        # Something that keeps printing, to show a pane filling up on its own.
        actions.append(
            ExecuteProcess(
                name="chatter",
                output="screen",
                cmd=["bash", "-c", "i=0; while true; do echo \"tick $((i++))\"; sleep 1; done"],
            )
        )
    if config.teleop:
        actions.append(
            window("teleop", [include(turtle_teleop, TeleopConfig(namespace=config.namespace))])
        )
    return actions


generate_launch_description = tmux_demo.generate_launch_description
