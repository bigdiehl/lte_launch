# stock_include_launch.py -- a typed front end for a traditional (untyped)
# ros2 launch file: config comes from our schema, is converted to plain
# name:=value strings with actions.stock_args(), and passed to
# IncludeLaunchDescription exactly as `ros2 launch` itself would. See
# stock_launch/spawn_turtle.launch.py (the included file) and
# working/design-notes.md, Q17.

import os

from ament_index_python.packages import get_package_share_directory
from pydantic import BaseModel, Field

from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource

from ros2_typed_launch import launch
from ros2_typed_launch.actions import stock_args


class StockIncludeConfig(BaseModel):
    """Typed front end for stock_launch/spawn_turtle.launch.py."""

    namespace: str = Field("turtlesim", description="Namespace for the included node")
    use_sim_time: bool = Field(False, description="Use simulation time")
    background_r: int = Field(69, ge=0, le=255, description="Background red channel")


@launch(config=StockIncludeConfig)
def stock_include(config: StockIncludeConfig):
    stock_file = os.path.join(
        get_package_share_directory("ros2_typed_launch"),
        "launch", "spawn_turtle.launch.py",
    )
    return [
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(stock_file),
            launch_arguments=stock_args({
                "namespace": config.namespace,
                "use_sim_time": config.use_sim_time,
                "background_r": config.background_r,
            }),
        ),
    ]


generate_launch_description = stock_include.generate_launch_description
