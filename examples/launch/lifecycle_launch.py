# lifecycle_launch.py -- a lifecycle node, transitioned line by line.
# A failed transition stops the run at its line; teardown sends `shutdown`.
#
#   tlaunch lte_launch lifecycle_launch.py                     # configure, activate
#   tlaunch lte_launch lifecycle_launch.py --autostart         # same, one keyword
#   tlaunch lte_launch lifecycle_launch.py --blink 3           # deactivate, reactivate
#   tlaunch lte_launch lifecycle_launch.py --skip_configure    # refused: exit 1
#   tlaunch lte_launch lifecycle_launch.py --dry-run           # `ros2 lifecycle set` lines

from pydantic import BaseModel, Field

from lte_launch import launch, lifecycle_node, log, namespace, node, sleep


class LifecycleConfig(BaseModel):
    """lifecycle's demo talker (a lifecycle node) and its listener."""

    namespace: str = Field("lc", description="Namespace for both nodes")
    autostart: bool = Field(False, description="Configure and activate as it starts")
    listener: bool = Field(True, description="Start the listener too")
    blink: float = Field(0.0, ge=0, description="Seconds active before one deactivate/activate")
    skip_configure: bool = Field(False, description="Activate straight away (refused)")


@launch
def lifecycle(config: LifecycleConfig):
    with namespace(config.namespace):
        talker = lifecycle_node(
            "lifecycle", "lifecycle_talker", name="lc_talker", autostart=config.autostart
        )
        if config.listener:
            node(
                "lifecycle",
                "lifecycle_listener",
                name="lc_listener",
                remaps={"/lc_talker/transition_event": f"{talker.fqn}/transition_event"},
            )
        if not config.autostart:
            if not config.skip_configure:
                talker.transition("configure")
            talker.transition("activate")
        talker.wait_for_state("active", timeout=10)
        if config.blink:
            sleep(config.blink)
            talker.transition("deactivate")
            sleep(1.0)
            talker.activate()
        log(f"{talker.fqn} is {talker.state}")
