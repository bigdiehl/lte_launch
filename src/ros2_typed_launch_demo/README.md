# ros2_typed_launch_demo

Runnable examples for `ros2_typed_launch`. Each one shows a different piece of the system, and
every command below is meant to be copy-pasted as-is.

```bash
cd ~/projects/ros2_launch                              # the repo root
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install                         # once, and after adding a file
source install/setup.bash                              # puts tlaunch on PATH
```

Add `-h` to any of these for help generated from that launch's schema, `--show-config` to print
the effective values (and where each came from) without launching, and `--schema` for its JSON
Schema. Stop a running launch with Ctrl-C. Most demos open a turtlesim window; on a machine with
no display, `export QT_QPA_PLATFORM=offscreen` first.

## The demos

| File | What it shows |
|---|---|
| `turtle_demo_launch.py` | the main one: a launch including two other typed launches |
| `turtle_teleop_launch.py` | the smallest launch there is, and an includable child |
| `localization_launch.py` | a child whose own config class is embedded in its parent's |
| `turtle_showcase_launch.py` | stock launch constructs (namespaces, params, timers, events) driven by typed config |
| `turtle_params_launch.py` | the two ways a node gets ROS parameters: typed `RosParams`, and untyped config sections |
| `stock_include_launch.py` | including a **traditional** (untyped) `.launch.py` file |
| `turtle_bringup_script.py` | **imperative** launching: start, wait, check, restart, stop, in order |
| `flaky_demo_launch.py` | what happens when a process dies: the error, the summary, the exit code |
| `tmux_demo_launch.py` | `--tmux`: a pane per process, windows to group an include, `--tmux-dash` dashboard (or `./run.sh`) |

### turtle_demo_launch.py — the main demo

turtlesim plus the teleop and localization launches, included as ordinary Python imports.

```bash
tlaunch ros2_typed_launch_demo turtle_demo_launch.py -h
tlaunch ros2_typed_launch_demo turtle_demo_launch.py

# nested fields are flags named by their dotted path; bools get --x/--no-x
tlaunch ros2_typed_launch_demo turtle_demo_launch.py \
    --turtlesim.background_r 99 --no-teleop.enabled --localization.params.x 2.5

# config files layer: later files win, the command line wins over all of them
tlaunch ros2_typed_launch_demo turtle_demo_launch.py \
    --config ros2_typed_launch_demo:config/blue_turtle.yaml \
    --config ros2_typed_launch_demo:config/quiet.yaml \
    --turtlesim.background_r 99 --show-config
```

### turtle_teleop_launch.py / localization_launch.py — the children, on their own

Any includable launch is also runnable by itself.

```bash
tlaunch ros2_typed_launch_demo turtle_teleop_launch.py --namespace demo
tlaunch ros2_typed_launch_demo localization_launch.py --x 1.0 --y 2.0 --yaw 0.5 --show-config
```

(`turtle_teleop_key` needs a real terminal; in a scripted run it exits with "Failed to get old
console mode" — expected, not a bug.)

### turtle_showcase_launch.py — stock launch constructs

Namespaces, global parameters, remappings, environment variables, timers, log levels and event
handlers — all as typed config.

```bash
tlaunch ros2_typed_launch_demo turtle_showcase_launch.py -h
tlaunch ros2_typed_launch_demo turtle_showcase_launch.py \
    --scope.global_params use_sim_time=true --timers.stop_after 15
tlaunch ros2_typed_launch_demo turtle_showcase_launch.py \
    --scope.namespace alpha --logging.level debug --timers.spawn_delay 1.0
```

`--timers.stop_after 9` makes the launch stop itself, which is handy without a TTY.

### turtle_params_launch.py — node parameters, typed and untyped

Typed parameters are a `RosParams` model in the schema (flags, help, validation). Untyped ones
come from node-pattern sections in a config file and need no schema at all.

```bash
tlaunch ros2_typed_launch_demo turtle_params_launch.py --turtlesim.background_r 200

# untyped parameters and remappings from a config file, with the pattern -> node report
tlaunch ros2_typed_launch_demo turtle_params_launch.py \
    --config ros2_typed_launch_demo:config/params_demo.yaml --show-config

# the same two lanes, straight from the command line (pattern defaults to /**)
tlaunch ros2_typed_launch_demo turtle_params_launch.py \
    --ros-param '/**:use_sim_time=true' \
    --ros-param '/demo/turtlesim:holonomic=true' \
    --ros-remap '/demo/turtlesim:turtle1/cmd_vel=/joy_vel' --show-config

# --strict refuses the untyped lane outright; -d shows the advisory warnings
#   (a pattern that matched nothing, a typed field shadowing an untyped one)
tlaunch ros2_typed_launch_demo turtle_params_launch.py \
    --config ros2_typed_launch_demo:config/params_demo.yaml --strict
tlaunch ros2_typed_launch_demo turtle_params_launch.py \
    --config ros2_typed_launch_demo:config/params_demo.yaml -d --show-config
```

Check that the values arrived, with the launch still running:

```bash
ros2 param get /demo/turtlesim background_r
ros2 node info /demo/turtlesim
```

### stock_include_launch.py — including a traditional launch file

The included file, `stock_launch/spawn_turtle.launch.py`, is an ordinary untyped ROS launch file
installed to `share/`. Typed config is converted to plain `name:=value` launch arguments.

```bash
tlaunch ros2_typed_launch_demo stock_include_launch.py --namespace t1 --background_r 120
ros2 launch ros2_typed_launch_demo spawn_turtle.launch.py namespace:=t1   # the stock file alone
```

### turtle_bringup_script.py — imperative launching

A script, not a declaration: it brings turtlesim up, waits until it is really in the ROS graph,
starts a driver, waits for the driver's own output, restarts it, stops it, and tears down — in
that order. Runs the same way under `tlaunch` or plain `python3`.

```bash
tlaunch ros2_typed_launch_demo turtle_bringup_script.py
tlaunch ros2_typed_launch_demo turtle_bringup_script.py --settle 2 --drive_for 10
tlaunch ros2_typed_launch_demo turtle_bringup_script.py --no-drive        # bringup only

python3 src/ros2_typed_launch_demo/launch/turtle_bringup_script.py -h    # the same file, directly
python3 src/ros2_typed_launch_demo/launch/turtle_bringup_script.py --no-restart_driver
```

Ctrl-C at any point: the wait the script is in raises, the script's own `finally` cleanup runs,
and then everything it started is stopped (exit code 130).

### flaky_demo_launch.py — when something dies

Stock launch logs a death and still exits 0, so a failed bringup looks successful. This demo has a
process that fails on purpose, next to a node that behaves and a one-shot job that finishes
cleanly. It stops itself after a few seconds, so you can see the whole report.

```bash
tlaunch ros2_typed_launch_demo flaky_demo_launch.py ; echo "exit: $?"
```

```
[ERROR] [flaky-2]: process has died [pid 740474, exit code 3, cmd '...'].
[ERROR] [tlaunch]: flaky-2 died unexpectedly: exit code 3 [pid 740474]
tlaunch: 1 process(es) died unexpectedly:
  flaky-2: exit code 3 [pid 740474]
    cmd: bash -c echo working; sleep 1.0; echo giving up >&2; exit 3
exit: 1
```

The one-shot process exiting 0 is not a death, and neither is the node that was stopped when the
launch ended — only an unasked-for failure is. Make it behave and the run is quiet again:

```bash
tlaunch ros2_typed_launch_demo flaky_demo_launch.py --rc 0 ; echo "exit: $?"    # exit: 0
```

Any launch can also publish this as diagnostics (off by default, because it needs a ROS node
inside the launch):

```bash
tlaunch ros2_typed_launch_demo flaky_demo_launch.py --diagnostics
ros2 topic echo /diagnostics          # in another terminal
```

```
- level: "\x02"
  name: /demo/keeper
  message: 'died unexpectedly: killed by SIGKILL'
```

Each status is named by the node's fully qualified name where there is one, with the process name,
pid, return code and command in its key/value list. Only processes this launch started are
tracked: a node started by a wrapper script hides behind the wrapper's pid (have the wrapper pass
the exit code up), and a composable node has no pid of its own at all.

### tmux_demo_launch.py — a pane per process

Normally every process's output lands in one terminal, interleaved. `--tmux` gives each its own
pane instead, with its own scrollback and its own **real terminal** — which is also the only way
`turtle_teleop_key` works, since under plain launch it gives up with "Failed to get old console
mode".

Needs `lte-run` from libtmux_easy on `PATH` (once: `uv tool install --editable
~/projects/libtmux_easy`).

```bash
tlaunch ros2_typed_launch_demo tmux_demo_launch.py --tmux
tmux attach -t tlaunch-tmux_demo          # in another terminal

# only some of them; everything else stays in this terminal as usual
tlaunch ros2_typed_launch_demo tmux_demo_launch.py --tmux-only 'turtlesim*'
tlaunch ros2_typed_launch_demo tmux_demo_launch.py --tmux-only '/demo/*' --tmux-only chatter
```

```
[INFO] [tlaunch]: tmux session 'tlaunch-tmux_demo' closed (nothing failed in it)
```

`--tmux` works on **any** launch, not just this one — including the nodes of an included launch
file, typed or stock:

```bash
tlaunch ros2_typed_launch_demo turtle_demo_launch.py --tmux
tlaunch ros2_typed_launch_demo flaky_demo_launch.py --tmux ; echo "exit: $?"
```

Launch still owns every process, so nothing else changes: a node that dies is still reported and
still makes the run exit non-zero. What the panes add is where the output goes.

Windows group panes. This demo puts the teleop include in one of its own:

```python
window("teleop", [include(turtle_teleop, TeleopConfig(namespace=config.namespace))])
```

That works for anything an included launch starts, however deep, because all `window` does is set
an environment variable that the pane machinery reads. Without `--tmux` it does nothing at all.

Where the panes go:

| Situation | What happens |
|---|---|
| run from a normal terminal | a session `tlaunch-<launch>` is created; tlaunch prints how to attach |
| run from inside tmux | the windows are added to **your** session, next to what you are doing |
| `--tmux-session NAME` | that session is used, and joined if it already exists |

Nothing is ever killed to make room. At the end of the run, a session of tlaunch's own that
nobody is attached to is closed — unless a pane's process failed, in which case that pane is the
last of its output, so it stays and tlaunch says where:

```
[INFO] [tlaunch]: 1 failed pane(s) kept in tmux session 'tlaunch-flaky_demo' (flaky_demo)
                  -- read them with: tmux attach -t tlaunch-flaky_demo
```

**Dashboard and log viewer** (both imply `--tmux`). Quickest: `./run.sh` at the repo root
(`./run.sh -h` lists its modes) opens a tmux session if you are not in one, finds the log viewer
in a ros2-term checkout if it is not installed, and runs both:

```bash
./run.sh                    # --tmux-dash --tmux-logs;  also: dash, tmux, flaky, plain
tlaunch ros2_typed_launch_demo tmux_demo_launch.py --tmux-dash --tmux-only 'turtle*'
tlaunch ros2_typed_launch_demo tmux_demo_launch.py --tmux-dash --tmux-logs
```

| Flag | Adds | Needs |
|---|---|---|
| `--tmux-dash` | window `dash`: a sidebar of every pane with live status, the selected pane in the viewport, and **home** = this run's own output (launch's log and every process *not* in a pane) | `lte-dash`: `uv tool install --editable '~/projects/libtmux_easy[dash]'` |
| `--tmux-logs` | window `logs` running the `/rosout` viewer | `ros2-log-view` (ros2-term), or `TLAUNCH_LOG_VIEWER="cmd ..."` |

In the sidebar: `j`/`k` browse, `x` stops a process (through launch, so it is not reported as a
death), `Q` twice shuts the whole run down like Ctrl-C, `g` goes to the pane's own window. `r`
(rerun) is refused: launch cannot run an action twice. Run from inside tmux, the dashboard opens
in your session and is selected.

Keys, borrowed for the run (no `~/.tmux.conf` needed): `prefix g` back to the dashboard,
`prefix v` to the log window and again back. In any other session they keep their usual binding,
and they are given back when the run ends. `TLAUNCH_DASH_KEY` / `TLAUNCH_LOGS_KEY` pick other
keys; set to empty, nothing is borrowed. Needs `lte-key` (installed with libtmux_easy).

A process in a pane prints to the pane, not this terminal. Its output is still copied to
launch's log files and the death report, and a script can still `wait_output()` on it:

```bash
tlaunch ros2_typed_launch_demo turtle_bringup_script.py --tmux   # waits on the driver's output
```

## The config files

| File | For | Shows |
|---|---|---|
| `config/blue_turtle.yaml` | `turtle_demo_launch.py` | a plain `launch_args:` file |
| `config/quiet.yaml` | `turtle_demo_launch.py` | an overlay: pass it *after* another file and watch it win |
| `config/params_demo.yaml` | `turtle_params_launch.py` | all three parts: `launch_args`, `ros__parameters`, `ros__remappings`, including a deliberate typed/untyped conflict and a pattern that matches nothing |

Values can use the stock launch substitutions (`$(env HOME)`, `$(find-pkg-share pkg)`,
`$(command ...)`, `$(eval ...)`), plus **`$(var NAME)`, which reads one of this launch's own
arguments** by its dotted path. `config/params_demo.yaml` uses it for both a remap and a section
pattern, so everything follows `--namespace`:

```bash
tlaunch ros2_typed_launch_demo turtle_params_launch.py \
    --config ros2_typed_launch_demo:config/params_demo.yaml --namespace alpha --show-config
#   "/alpha/turtlesim":                  # -> /alpha/turtlesim
#     "turtle1/cmd_vel": "/alpha/joy_vel"
```

A config file is addressed as a path, or as `<package>:<path>` for one installed in a package's
share directory. `launch_args:` is checked against the launch's schema — an unknown or mistyped
key there is an error, and `config/params_demo.yaml` is therefore rejected by
`turtle_demo_launch.py` (its `namespace` is not a field of that launch). The
`ros__parameters`/`ros__remappings` sections are never checked: they are the node's business.

Every run also writes its effective config to `/tmp/tlaunch/<launch>-<pid>.yaml` and logs the
path; that file is valid `--config` input, so a run can be reproduced from it.

More on the design: `../CLAUDE.md` and `../working/design-notes.md`.
