# localization_launch.py -- static map -> odom transform, a stand-in for a
# real localizer.

from pydantic import BaseModel, Field

from ros2_typed_launch import launch, node


class LocalizationConfig(BaseModel):
    """Static map -> odom transform (stand-in for a real localizer)."""

    namespace: str = Field("", description="ROS namespace")
    map_frame: str = Field("map", description="Parent frame id")
    odom_frame: str = Field("odom", description="Child frame id")
    x: float = Field(0.0, description="Translation along x (m)")
    y: float = Field(0.0, description="Translation along y (m)")
    yaw: float = Field(0.0, ge=-3.15, le=3.15, description="Rotation about z (rad)")


@launch(config=LocalizationConfig)
def localization(config: LocalizationConfig):
    node(
        "tf2_ros",
        "static_transform_publisher",
        name="map_to_odom",
        namespace=config.namespace,
        arguments=[
            "--x", str(config.x), "--y", str(config.y), "--yaw", str(config.yaw),
            "--frame-id", config.map_frame, "--child-frame-id", config.odom_frame,
        ],
    )  # fmt: skip
