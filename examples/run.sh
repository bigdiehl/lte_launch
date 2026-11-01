#!/usr/bin/env bash
# Demo of tlaunch's tmux modes.
#
#   ./run.sh            dashboard + ROS log window (default)
#   ./run.sh dash       dashboard only
#   ./run.sh tmux       a pane per process, no dashboard
#   ./run.sh flaky      dashboard over a launch where a node fails
#   ./run.sh plain      ordinary launch, no tmux
#
# Outside tmux it opens a tmux session and runs itself there. Sources the
# workspace at $TLAUNCH_WS (default ~/ros2_ws).
#
# Keys (prefix = your tmux prefix):
#   prefix g    back to the dashboard sidebar
#   prefix v    ROS log window <-> where you were
#   sidebar: j/k select, x stop, Q Q quit the whole run
set -euo pipefail

# The colcon workspace this package is built in.
WS="${TLAUNCH_WS:-$HOME/ros2_ws}"
MODE="${1:-all}"
SESSION="tlaunch-demo"

set +u
source /opt/ros/jazzy/setup.bash
source "$WS/install/setup.bash"
set -u

for tool in tmux tlaunch lte-run lte-dash lte-key; do
    command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }
done

# ros2-log-view from the local ros2-term checkout, if the installed one lacks it.
if ! command -v ros2-log-view >/dev/null && [[ -z "${TLAUNCH_LOG_VIEWER:-}" ]]; then
    export TLAUNCH_LOG_VIEWER="env PYTHONPATH=$HOME/projects/ros2-term/src:${PYTHONPATH:-} \
$HOME/.local/share/pipx/venvs/ros2-term/bin/python -m ros2_term.log_app"
fi

case "$MODE" in
    all)   args=(tmux_demo_launch.py --tmux-dash --tmux-logs) ;;
    dash)  args=(tmux_demo_launch.py --tmux-dash) ;;
    tmux)  args=(tmux_demo_launch.py --tmux) ;;
    flaky) args=(flaky_demo_launch.py --tmux-dash) ;;
    plain) exec tlaunch ros2_typed_launch tmux_demo_launch.py ;;
    *)     sed -n '2,15p' "$0"; exit 2 ;;
esac

if [[ -z "${TMUX:-}" ]]; then
    tmux has-session -t "=$SESSION" 2>/dev/null && tmux kill-session -t "=$SESSION"
    exec tmux new-session -s "$SESSION" -n launch \
        "'$0' '$MODE'; echo; read -rp 'run.sh done, Enter to close '"
fi

tlaunch ros2_typed_launch "${args[@]}" && status=0 || status=$?
echo "tlaunch exit: $status"
