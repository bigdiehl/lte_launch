cmake_minimum_required(VERSION 3.8)
project(ros2_typed_launch)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})

# tlaunch: a plain script (no setuptools console_scripts indirection needed).
# Installed twice: lib/${PROJECT_NAME} is where `ros2 run ros2_typed_launch
# tlaunch` looks; bin is what ament_cmake_core's default "path" environment
# hook (environment/path.sh, registered for every package) prepends to PATH
# once install/setup.bash is sourced -- so plain `tlaunch` then just works,
# no shell function needed.
install(PROGRAMS scripts/tlaunch DESTINATION lib/${PROJECT_NAME})
install(PROGRAMS scripts/tlaunch DESTINATION bin)

# tlaunch-pane: the stand-in launch runs when a process goes in a tmux pane
# (see tmux.py). On PATH for the same reason, because tmux.py looks it up there.
install(PROGRAMS scripts/tlaunch-pane DESTINATION lib/${PROJECT_NAME})
install(PROGRAMS scripts/tlaunch-pane DESTINATION bin)

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
