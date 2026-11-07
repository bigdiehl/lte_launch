#!/usr/bin/env bash
# Demo of tlaunch's tmux modes, on turtle_bringup_launch.py.
#
#   ./run.sh            dashboard (default)
#   ./run.sh tmux       a pane per process, no dashboard
#   ./run.sh flaky      dashboard over a run where a process dies
#   ./run.sh plain      subprocesses, no tmux
#
# Outside tmux it opens a tmux session and runs itself there. Sources the
# workspace at $TLAUNCH_WS (default ~/ros2_ws).
set -euo pipefail

WS="${TLAUNCH_WS:-$HOME/ros2_ws}"
MODE="${1:-dash}"
SESSION="tlaunch-demo"

set +u
source /opt/ros/jazzy/setup.bash
source "$WS/install/setup.bash"
set -u

for tool in tmux tlaunch lte-run; do
    command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }
done

launch=(ros2_typed_launch turtle_bringup_launch.py --teleop)
case "$MODE" in
    dash)  args=(--tmux-dash) ;;
    tmux)  args=(--tmux) ;;
    flaky) args=(--tmux-dash --crash_after 5) ;;
    plain) exec tlaunch ros2_typed_launch turtle_bringup_launch.py ;;
    *)     sed -n '2,10p' "$0"; exit 2 ;;
esac

if [[ -z "${TMUX:-}" ]]; then
    tmux has-session -t "=$SESSION" 2>/dev/null && tmux kill-session -t "=$SESSION"
    exec tmux new-session -s "$SESSION" -n launch \
        "'$0' '$MODE'; echo; read -rp 'run.sh done, Enter to close '"
fi

tlaunch "${launch[@]}" "${args[@]}" && status=0 || status=$?
echo "tlaunch exit: $status"
