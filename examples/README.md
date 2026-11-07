# Examples

Runnable examples, installed as `ros2_typed_launch.examples` (configs in
`share/ros2_typed_launch/examples/config/`). Every command is meant to be copy-pasted.

```bash
cd ~/ros2_ws                                           # the colcon workspace
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install                         # once, and after adding a file
source install/setup.bash                              # puts tlaunch on PATH
```

Any launch takes `-h` (help from its schema), `--show-config` (effective values and their
sources, no launch), `--dry-run` (every command line, nothing started) and `--schema`. Ctrl-C
stops a run. Without a display: `export QT_QPA_PLATFORM=offscreen`.

| File | What it shows |
|---|---|
| `turtle_bringup_launch.py` | the synchronous style: start, wait, log, a window, a crash |
| `turtle_demo_launch.py` | a launch including two other typed launches |
| `turtle_teleop_launch.py` | the smallest launch, and an includable child (needs a TTY: `--tmux`) |
| `localization_launch.py` | a child whose config class is embedded in its parent's |
| `turtle_params_launch.py` | ROS parameters, typed (`RosParams`) and untyped (config sections) |
| `composition_launch.py` | composable nodes: standalone, our container, or one already running |
| `stock_include_launch.py` | including a **traditional** `.launch.py` file |
| `stock_launch/stock_parent.launch.xml` | a **stock** launch file including a typed one |
| `lte_test/` | the bringup under lte_launch_test, four ways |

### turtle_bringup_launch.py

```bash
tlaunch ros2_typed_launch turtle_bringup_launch.py                # subprocesses
tlaunch ros2_typed_launch turtle_bringup_launch.py --tmux --teleop  # a pane per process
tlaunch ros2_typed_launch turtle_bringup_launch.py --tmux-dash    # + libtmux_easy dashboard
tlaunch ros2_typed_launch turtle_bringup_launch.py --dry-run      # the plan only
tlaunch ros2_typed_launch turtle_bringup_launch.py --crash_after 3 ; echo $?   # a death: 1
```

A death prints `[ERROR] [tlaunch]: <who> died unexpectedly` with its last output, stops
everything at once and exits 1. A second Ctrl-C while stopping kills everything.

### turtle_demo_launch.py

```bash
tlaunch ros2_typed_launch turtle_demo_launch.py \
    --turtlesim.background_r 99 --no-teleop.enabled --localization.params.x 2.5
tlaunch ros2_typed_launch turtle_demo_launch.py \
    --config ros2_typed_launch:examples/config/blue_turtle.yaml \
    --config ros2_typed_launch:examples/config/quiet.yaml \
    --turtlesim.background_r 99 --show-config      # later files win, flags win over all
tlaunch ros2_typed_launch localization_launch.py --x 1.0 --y 2.0 --yaw 0.5 --show-config
```

### turtle_params_launch.py

```bash
tlaunch ros2_typed_launch turtle_params_launch.py --turtlesim.background_r 200
tlaunch ros2_typed_launch turtle_params_launch.py \
    --config ros2_typed_launch:examples/config/params_demo.yaml --show-config
tlaunch ros2_typed_launch turtle_params_launch.py \
    --ros-param '/**:use_sim_time=true' \
    --ros-remap '/demo/turtlesim:turtle1/cmd_vel=/joy_vel' --show-config
tlaunch ros2_typed_launch turtle_params_launch.py \
    --config ros2_typed_launch:examples/config/params_demo.yaml --strict   # refuses untyped
ros2 param get /demo/turtlesim background_r       # while it runs
```

### composition_launch.py

```bash
tlaunch ros2_typed_launch composition_launch.py --topic /chat          # our container "box"
tlaunch ros2_typed_launch composition_launch.py --container ""         # each standalone
ros2 run rclcpp_components component_container --ros-args -r __node:=box -r __ns:=/robot &
tlaunch ros2_typed_launch composition_launch.py --container /robot/box # into that one
tlaunch ros2_typed_launch composition_launch.py --kind multithreaded --dry-run
tlaunch ros2_typed_launch composition_launch.py --plugin composition::Nope ; echo $?  # 1
ros2 component list                               # while it runs
```

Into a container started elsewhere, the run stays up until Ctrl-C, then unloads its
components; it fails if that container goes away.

### Stock interop

```bash
tlaunch ros2_typed_launch stock_include_launch.py --namespace t1 --background_r 120
ros2 launch ros2_typed_launch stock_parent.launch.xml
ros2 launch ros2_typed_launch turtle_demo_launch.py -s          # one argument per field
ros2 launch ros2_typed_launch turtle_demo_launch.py turtlesim.namespace:=a teleop.enabled:=false
```

A typed launch includes a stock file as one `ros2 launch` process. A stock file includes a
typed one through its stub in `share/` (`ros2_typed_launch_install_stubs` in CMake); the
arguments are validated at include time, then it runs as one `tlaunch` process.

### lte_test/

```bash
examples/lte_test/run.sh -v            # subprocesses
examples/lte_test/run.sh -v --tmux     # under the libtmux_easy dashboard
```

| Class | How the launch runs |
|---|---|
| `TestAsProcess` | `tlaunch` as one process, gated on its `[tlaunch]: up` line |
| `TestBuild` | `procs.build(turtle_bringup.build)`: every node a process of the test |
| `TestStart` | `turtle_bringup.start(procs)`: the same, in any mode |
| `TestLayout` | `turtle_bringup.panes()`: the dry-run plan, started by the test |

## tmux

| Situation | What happens |
|---|---|
| `--tmux` from a normal terminal | a session `tlaunch-<launch>-<pid>`; tlaunch prints how to attach |
| `--tmux` inside tmux | windows `<launch>/<window>` in **your** session |
| `--tmux-session NAME` | a new session of that name |
| `--tmux-dash` | libtmux_easy's dashboard (`lte-dash`); the session is kept at the end |
| a failure | the session (or windows) kept to look at |

`examples/run.sh [dash|tmux|flaky|plain]` opens tmux if needed and runs the bringup there.

## The config files

| File | For | Shows |
|---|---|---|
| `config/blue_turtle.yaml` | `turtle_demo_launch.py` | a plain `launch_args:` file |
| `config/quiet.yaml` | `turtle_demo_launch.py` | an overlay: pass it after another file |
| `config/params_demo.yaml` | `turtle_params_launch.py`, `turtle_bringup_launch.py` | `launch_args`, `ros__parameters`, `ros__remappings`, a typed/untyped conflict, a pattern matching nothing |

Values may use stock substitutions (`$(env HOME)`, `$(find-pkg-share pkg)`, ...) and
`$(var NAME)`, which reads this launch's own arguments by dotted path. `launch_args:` is checked
against the schema; node sections never are. Every run writes its effective config to
`/tmp/tlaunch/<launch>-<pid>.yaml`, valid `--config` input.
