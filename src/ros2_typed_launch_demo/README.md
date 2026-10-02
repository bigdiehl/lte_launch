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

## The config files

| File | For | Shows |
|---|---|---|
| `config/blue_turtle.yaml` | `turtle_demo_launch.py` | a plain `launch_args:` file |
| `config/quiet.yaml` | `turtle_demo_launch.py` | an overlay: pass it *after* another file and watch it win |
| `config/params_demo.yaml` | `turtle_params_launch.py` | all three parts: `launch_args`, `ros__parameters`, `ros__remappings`, including a deliberate typed/untyped conflict and a pattern that matches nothing |

A config file is addressed as a path, or as `<package>:<path>` for one installed in a package's
share directory. `launch_args:` is checked against the launch's schema — an unknown or mistyped
key there is an error, and `config/params_demo.yaml` is therefore rejected by
`turtle_demo_launch.py` (its `namespace` is not a field of that launch). The
`ros__parameters`/`ros__remappings` sections are never checked: they are the node's business.

Every run also writes its effective config to `/tmp/tlaunch/<launch>-<pid>.yaml` and logs the
path; that file is valid `--config` input, so a run can be reproduced from it.

More on the design: `../CLAUDE.md` and `../working/design-notes.md`.
