# stock_include_launch.py -- a typed front end for a traditional (untyped)
# ros2 launch file: config comes from our schema, and include() runs the stock
# file as one `ros2 launch` process with name:=value arguments (stock_arg()
# converts each value). See stock_launch/spawn_turtle.launch.py and
# working/design-notes.md, Q17.

from pydantic import BaseModel, Field

from ros2_typed_launch import include, launch


class StockIncludeConfig(BaseModel):
    """Typed front end for stock_launch/spawn_turtle.launch.py."""

    namespace: str = Field("turtlesim", description="Namespace for the included node")
    use_sim_time: bool = Field(False, description="Use simulation time")
    background_r: int = Field(69, ge=0, le=255, description="Background red channel")


@launch
def stock_include(config: StockIncludeConfig):
    include(
        "ros2_typed_launch",
        "spawn_turtle.launch.py",
        namespace=config.namespace,
        use_sim_time=config.use_sim_time,
        background_r=config.background_r,
    )
