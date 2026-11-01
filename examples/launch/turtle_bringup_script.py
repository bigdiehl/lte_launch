# turtle_bringup_script.py -- an imperative (synchronous) launch.
#
# A launch file hands actions to launch and loses control. A *script* keeps it:
# this file starts turtlesim, waits for it to really be in the ROS graph, does
# its own checks, then starts a driver, watches its output, stops it, and
# finishes -- in that order, written as ordinary Python.
#
# Run it either way:
#   tlaunch ros2_typed_launch turtle_bringup_script.py
#   tlaunch ros2_typed_launch turtle_bringup_script.py --drive_for 10 --no-drive
#   python3 examples/launch/turtle_bringup_script.py --settle 2
#
# The session runs on this thread; the launch service owns the main thread (its
# own rule). Ctrl-C makes whichever wait we are in raise, so the `finally`
# below still runs.

from pydantic import BaseModel, Field
from launch.actions import ExecuteProcess
from ros2_typed_launch import script

# Absolute, not relative: under `python3 this_file.py` the module is __main__ and
# has no parent package, so a relative import would fail. A launch file that is
# only ever loaded by tlaunch can use the relative form (see turtle_demo_launch.py).
from ros2_typed_launch.examples.turtle_params_launch import (
    ParamsDemoConfig,
    turtle_params,
)


class BringupConfig(BaseModel):
    """Bringup with checks between the stages."""

    namespace: str = Field("demo", description="Namespace to bring the turtle up in")
    settle: float = Field(1.0, ge=0, description="Seconds of 'work' after the node is up")
    drive: bool = Field(True, description="Also start a driver that publishes cmd_vel")
    drive_for: float = Field(5.0, ge=0, description="Seconds to let the driver run")
    restart_driver: bool = Field(True, description="Restart the driver stage once, to show it")
    startup_timeout: float = Field(30.0, gt=0, description="How long to wait for bringup")


def driver_process(namespace: str) -> ExecuteProcess:
    """A plain launch action: anything you can launch, a stage can start."""
    return ExecuteProcess(
        name="driver",
        cmd=[
            "ros2", "topic", "pub", "-r", "2",
            f"/{namespace}/turtle1/cmd_vel", "geometry_msgs/msg/Twist",
            "{linear: {x: 1.2}, angular: {z: 0.9}}",
        ],
        output="screen",
    )


@script(config=BringupConfig)
def turtle_bringup(session, config: BringupConfig):
    sim = session.start(
        turtle_params,
        ParamsDemoConfig(namespace=config.namespace, node_name="turtlesim"),
    )
    try:
        # Two different waits: the process exists, and the node is really up.
        sim.wait_started(timeout=config.startup_timeout)
        session.log(f"turtlesim started, pid {sim['turtlesim'].pid}")
        session.wait_for_node(
            f"/{config.namespace}/turtlesim", timeout=config.startup_timeout
        )
        session.log("turtlesim is in the ROS graph -- bringup checks pass")

        session.sleep(config.settle)     # stand in for real work

        if config.drive:
            # Started from a function, not from actions: that is what makes the
            # stage restartable (launch will not run the same action twice).
            driver = session.start(
                lambda: driver_process(config.namespace), name="driver"
            )
            driver.wait_started(timeout=10.0)
            # Wait for what the process says, not for a guess about timing.
            driver["driver"].wait_output("publishing", timeout=15.0)
            session.log("driver is publishing; letting the turtle move")
            session.sleep(config.drive_for)

            if config.restart_driver:
                driver.restart()         # stop, rebuild, start -- same handle
                driver["driver"].wait_output("publishing", timeout=15.0)
                session.log(f"driver restarted, now pid {driver['driver'].pid}")
                session.sleep(1.0)

            driver.stop()                # one stage down, the session lives on
            session.log(f"driver stopped (rc={driver['driver'].returncode})")

        session.log(f"nodes now in the graph: {', '.join(sorted(session.node_names()))}")
        return 0
    finally:
        # Runs on Ctrl-C too, before the session shuts down.
        session.log("tearing down")
        sim.stop()


generate_launch_description = turtle_bringup.generate_launch_description

if __name__ == "__main__":
    turtle_bringup.main()
