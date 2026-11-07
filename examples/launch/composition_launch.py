# composition_launch.py -- composable nodes, and where they run: one config field.
# A failed load stops the run at its line.
#
#   tlaunch ros2_typed_launch composition_launch.py                    # our container "box"
#   tlaunch ros2_typed_launch composition_launch.py --container ""     # each in its own
#   tlaunch ros2_typed_launch composition_launch.py --container /robot/box   # one running
#   tlaunch ros2_typed_launch composition_launch.py --dry-run          # loads as commands
#   tlaunch ros2_typed_launch composition_launch.py --plugin composition::Nope

from pydantic import BaseModel, Field

from ros2_typed_launch import component, container, launch, log, namespace


class CompositionConfig(BaseModel):
    """composition's talker and listener, standalone or in a container."""

    namespace: str = Field("comp", description="Namespace for the container and components")
    container: str = Field(
        "box",
        description='Where the components run: "" each standalone, a name: our '
        'container (started here), "/ns/name": a container already running',
    )
    kind: str = Field("single", description="Our container: single, multithreaded, isolated")
    plugin: str = Field("composition::Listener", description="The second component's plugin")
    topic: str = Field("chatter", description="What the listener subscribes to")


@launch(config=CompositionConfig)
def composition(config: CompositionConfig):
    with namespace(config.namespace):
        into = config.container
        if into and not into.startswith("/"):
            into = container(into, kind=config.kind)  # ours, with a kind
        talker = component("composition", "composition::Talker", name="talker", into=into)
        component(
            "composition",
            config.plugin,
            name="listener",
            into=into,
            remaps={"chatter": config.topic},
        )
        talker.wait_for_node()
        log(f"composition up, into={config.container or 'standalone'}")
