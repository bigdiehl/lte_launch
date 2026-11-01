# turtle_checks_launch.py -- checks declared in a plain launch (checks.py).
#
# The script turtle_bringup_script.py does this imperatively. Here the same
# order is declared: the driver starts only once turtlesim is really in the ROS
# graph, and the run fails if the driver never says it is publishing. A check
# that times out is an error, a line in the summary, and a non-zero exit.
#
#   tlaunch ros2_typed_launch turtle_checks_launch.py ; echo "exit: $?"
#   tlaunch ros2_typed_launch turtle_checks_launch.py --expect nope   # a failing check
#   ros2 launch ros2_typed_launch turtle_checks_launch.py             # stock: same checks

from pydantic import BaseModel, Field

from launch.actions import Shutdown, TimerAction

from ros2_typed_launch import checks, launch, node

from .turtle_bringup_script import driver_process


class ChecksConfig(BaseModel):
    """Bringup with declared checks: node up, then driver, then its output."""

    namespace: str = Field("demo", description="Namespace for turtlesim")
    timeout: float = Field(20.0, gt=0, description="Seconds each check may take")
    expect: str = Field("publishing", description="What the driver must print")
    stop_after: float = Field(
        5.0, ge=0, description="Shut down this long after the driver checks out (0 = never)"
    )


@launch(config=ChecksConfig)
def turtle_checks(config: ChecksConfig):
    driver = driver_process(config.namespace)
    finish = (
        [TimerAction(period=config.stop_after, actions=[Shutdown(reason="demo done")])]
        if config.stop_after else []
    )
    return [
        node(package="turtlesim", executable="turtlesim_node", name="turtlesim",
             namespace=config.namespace, output="screen"),
        checks.node(f"/{config.namespace}/turtlesim", timeout=config.timeout, then=[
            driver,
            checks.output(driver, config.expect, timeout=config.timeout, then=finish),
        ]),
    ]


generate_launch_description = turtle_checks.generate_launch_description
