cmake_minimum_required(VERSION 3.8)
project(lte_launch)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})

# examples/launch installs as a subpackage, lte_launch.examples, so the
# examples import each other with plain relative imports and run as
# `lte-launch lte_launch turtle_demo_launch.py`. Data goes to share/.
ament_python_install_package(examples
  PACKAGE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/examples/launch"
  DESTINATION "${PYTHON_INSTALL_DIR}/${PROJECT_NAME}"
)
install(DIRECTORY examples/config/ DESTINATION share/${PROJECT_NAME}/examples/config)
# Untyped stock launch files, where stock `ros2 launch` and includes look.
install(DIRECTORY examples/stock_launch/ DESTINATION share/${PROJECT_NAME}/launch)
# Stubs so stock launch can run and include the typed examples (Q17).
include(cmake/lte_launch-extras.cmake)
lte_launch_install_stubs(${PROJECT_NAME}.examples
  turtle_demo_launch turtle_params_launch turtle_teleop_launch localization_launch
  turtle_bringup_launch composition_launch lifecycle_launch)

# lte-launch: a plain script (no setuptools console_scripts indirection needed).
# Installed twice: lib/${PROJECT_NAME} is where `ros2 run lte_launch
# lte-launch` looks; bin is what ament_cmake_core's default "path" environment
# hook (environment/path.sh, registered for every package) prepends to PATH
# once install/setup.bash is sourced -- so plain `lte-launch` then just works,
# no shell function needed.
install(PROGRAMS scripts/lte-launch DESTINATION lib/${PROJECT_NAME})
install(PROGRAMS scripts/lte-launch DESTINATION bin)

# Registers tab completion the moment install/setup.bash is sourced -- no
# manual `eval "$(register-python-argcomplete lte-launch)"` needed. Must come
# before ament_package().
ament_environment_hooks(hook/lte-launch-completion.bash)

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(lte_launch_pytest test TIMEOUT 300)
endif()

# lte_launch_install_stubs(): for packages whose typed launches stock
# launch should find (see cmake/lte_launch-extras.cmake).
ament_package(CONFIG_EXTRAS cmake/lte_launch-extras.cmake)
