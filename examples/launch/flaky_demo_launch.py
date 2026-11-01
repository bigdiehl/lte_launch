# flaky_demo_launch.py -- what tlaunch does when a process dies (Q27).
#
# Stock launch logs "process has died" and then forgets: the run still exits 0,
# so a failed bringup looks like a successful one. tlaunch tracks every process
# it starts and, at the end, names whatever died and exits non-zero.
#
# Three processes, on purpose:
#   * keeper  -- a node that behaves, and stays up
#   * flaky   -- dies on its own with a non-zero code: this is the death
#   * oneshot -- exits 0 straight away, which is *not* a death
#
# Try it (it stops itself, so you can see the summary without Ctrl-C):
#   tlaunch ros2_typed_launch flaky_demo_launch.py ; echo "exit: $?"
#   tlaunch ros2_typed_launch flaky_demo_launch.py --rc 0    # then nothing died
#   tlaunch ros2_typed_launch flaky_demo_launch.py --diagnostics
#       # ... and in another terminal: ros2 topic echo /diagnostics

from pydantic import BaseModel, Field

from launch.actions import ExecuteProcess, Shutdown, TimerAction

from ros2_typed_launch import launch, node


class FlakyConfig(BaseModel):
    """A launch with one process that fails, to show the death report."""

    namespace: str = Field("demo", description="Namespace for the well-behaved node")
    fail_after: float = Field(2.0, ge=0, description="Seconds before the flaky one dies")
    rc: int = Field(3, description="Exit code it dies with (0 = it behaves after all)")
    stop_after: float = Field(
        8.0, ge=0, description="Shut the launch down after this many seconds (0 = never)"
    )


@launch(config=FlakyConfig)
def flaky_demo(config: FlakyConfig):
    actions = [
        node(
            package="turtlesim",
            executable="turtlesim_node",
            name="keeper",
            namespace=config.namespace,
            output="screen",
        ),
        ExecuteProcess(
            name="flaky",
            output="screen",
            cmd=[
                "bash", "-c",
                f"echo working; sleep {config.fail_after}; "
                f"echo giving up >&2; exit {config.rc}",
            ],
        ),
        # Exits 0 immediately: a job that finished, not a failure. It shows up in
        # --diagnostics as "stopped: exit code 0" and never in the summary.
        ExecuteProcess(
            name="oneshot", output="screen",
            cmd=["bash", "-c", "echo did my job"],
        ),
    ]
    if config.stop_after > 0:
        actions.append(
            TimerAction(
                period=config.stop_after,
                actions=[Shutdown(reason="stop_after elapsed")],
            )
        )
    return actions


generate_launch_description = flaky_demo.generate_launch_description
