# Registers bash tab completion for `tlaunch` (package names, then that
# package's *_launch.py files), sourced automatically once install/setup.bash
# is sourced -- see lte_launch/CMakeLists.txt (ament_environment_hooks)
# and cli.py's argcomplete.autocomplete() call. A no-op if python3-argcomplete
# isn't installed.
if command -v register-python-argcomplete >/dev/null 2>&1; then
  eval "$(register-python-argcomplete tlaunch)"
fi
