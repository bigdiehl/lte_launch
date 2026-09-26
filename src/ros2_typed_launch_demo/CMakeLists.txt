cmake_minimum_required(VERSION 3.8)
project(ros2_typed_launch_demo)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})

# launch/ lives at the package root (ROS-conventional, sibling to package.xml),
# but installs as a real subpackage of ros2_typed_launch_demo, not a copy to
# share/ -- so `from ros2_typed_launch_demo.launch import ...` and
# `tlaunch ros2_typed_launch_demo launch.<module>` both work. See
# working/design-notes.md.
ament_python_install_package(launch
  PACKAGE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/launch"
  DESTINATION "${PYTHON_INSTALL_DIR}/${PROJECT_NAME}"
)

install(DIRECTORY config/ DESTINATION share/${PROJECT_NAME}/config)

ament_package()
