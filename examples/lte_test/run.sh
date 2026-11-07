#!/usr/bin/env bash
# Typed launches under lte_launch_test: as a process, build(), panes(), start(procs).
#   ./run.sh -v            subprocesses (method 2 always uses tmux)
#   ./run.sh -v --tmux     the dashboard
set -e
here="$(cd "$(dirname "$0")" && pwd)"
ws="${TLAUNCH_WS:-$HOME/ros2_ws}"
[ -n "$AMENT_PREFIX_PATH" ] || source "/opt/ros/${ROS_DISTRO:-jazzy}/setup.bash"
source "$ws/install/setup.bash"
# lte_launch_test and libtmux_easy checkouts beside this repo, unless installed.
python3 -c 'import lte_launch_test' 2>/dev/null ||
    export PYTHONPATH="$here/../../../lte_launch_test/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -c 'import libtmux_easy' 2>/dev/null ||
    export PYTHONPATH="$here/../../../libtmux_easy/src:$PYTHONPATH"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
cd "$here"
exec python3 -m pytest -p no:cacheprovider "$@"
