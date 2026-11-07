cmake_minimum_required(VERSION 3.8)
project(ros2_typed_launch)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})

# examples/launch installs as a subpackage, ros2_typed_launch.examples, so the
# examples import each other with plain relative imports and run as
# `tlaunch ros2_typed_launch turtle_demo_launch.py`. Data goes to share/.
ament_python_install_package(examples
  PACKAGE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/examples/launch"
  DESTINATION "${PYTHON_INSTALL_DIR}/${PROJECT_NAME}"
)
install(DIRECTORY examples/config/ DESTINATION share/${PROJECT_NAME}/examples/config)
# Untyped stock launch files, where stock `ros2 launch` and includes look.
install(DIRECTORY examples/stock_launch/ DESTINATION share/${PROJECT_NAME}/launch)
# Stubs so stock launch can run and include the typed examples (Q17).
include(cmake/ros2_typed_launch-extras.cmake)
ros2_typed_launch_install_stubs(${PROJECT_NAME}.examples
  turtle_demo_launch turtle_params_launch turtle_teleop_launch localization_launch
  turtle_bringup_launch composition_launch)

# tlaunch: a plain script (no setuptools console_scripts indirection needed).
# Installed twice: lib/${PROJECT_NAME} is where `ros2 run ros2_typed_launch
# tlaunch` looks; bin is what ament_cmake_core's default "path" environment
# hook (environment/path.sh, registered for every package) prepends to PATH
# once install/setup.bash is sourced -- so plain `tlaunch` then just works,
# no shell function needed.
install(PROGRAMS scripts/tlaunch DESTINATION lib/${PROJECT_NAME})
install(PROGRAMS scripts/tlaunch DESTINATION bin)

# Registers tab completion the moment install/setup.bash is sourced -- no
# manual `eval "$(register-python-argcomplete tlaunch)"` needed. Must come
# before ament_package().
ament_environment_hooks(hook/tlaunch-completion.bash)

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(ros2_typed_launch_test test)
endif()

# ros2_typed_launch_install_stubs(): for packages whose typed launches stock
# launch should find (see cmake/ros2_typed_launch-extras.cmake).
ament_package(CONFIG_EXTRAS cmake/ros2_typed_launch-extras.cmake)
