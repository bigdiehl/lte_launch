# turtle_params_launch.py -- the two ways a node gets its ROS parameters.
#
# 1. Typed, in the schema: a RosParams model. Validated, gets CLI flags and
#    --help text, and reaches the node without being restated anywhere.
# 2. Untyped, from a config file: ros__parameters / ros__remappings sections
#    keyed by node pattern (/**, /**/turtlesim, /demo/*), merged in by node().
#
# Try it:
#   lte-launch lte_launch turtle_params_launch.py --show-config
#   lte-launch lte_launch turtle_params_launch.py \
#       --ros-param '/**:use_sim_time=true' --show-config
#   lte-launch lte_launch turtle_params_launch.py --strict   # refuses untyped

from pydantic import BaseModel, Field

from lte_launch import RosParams, launch, node


class TurtlesimParams(RosParams):
    """ROS parameters of the turtlesim node itself."""

    background_r: int = Field(69, ge=0, le=255, description="Red channel")
    background_g: int = Field(86, ge=0, le=255, description="Green channel")
    background_b: int = Field(255, ge=0, le=255, description="Blue channel")
    holonomic: bool = Field(False, description="Holonomic motion model")


class ParamsDemoConfig(BaseModel):
    """turtlesim with typed ROS parameters, plus whatever config aims at it."""

    namespace: str = Field("demo", description="Namespace for the node")
    node_name: str = Field("turtlesim", description="Node name (what patterns match)")
    turtlesim: TurtlesimParams = TurtlesimParams()


@launch
def turtle_params(config: ParamsDemoConfig):
    node(
        "turtlesim",
        "turtlesim_node",
        name=config.node_name,
        namespace=config.namespace,
        params=config.turtlesim,
    )
