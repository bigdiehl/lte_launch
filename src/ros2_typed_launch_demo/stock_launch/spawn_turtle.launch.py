# spawn_turtle.launch.py -- a traditional (untyped) ros2 launch file: no
# schema, no validation, args are name:=value strings, declared with
# DeclareLaunchArgument and read back with LaunchConfiguration. Installed to
# share/ros2_typed_launch_demo/launch/, like any stock launch file -- not part
# of the Python package, so tlaunch's own search never finds it (by design:
# this file is for `ros2 launch` or IncludeLaunchDescription, not `tlaunch`).
#
# Included from a typed launch via stock_include_launch.py, which converts its
# own typed config into this file's launch_arguments with actions.stock_args().

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, PushRosNamespace


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("namespace", default_value="turtlesim"),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("background_r", default_value="69"),
        GroupAction([
            PushRosNamespace(LaunchConfiguration("namespace")),
            Node(
                package="turtlesim",
                executable="turtlesim_node",
                name="turtlesim",
                parameters=[{
                    "use_sim_time": LaunchConfiguration("use_sim_time"),
                    "background_r": LaunchConfiguration("background_r"),
                }],
            ),
        ]),
    ])
