# working/ — project documents

`ros2_typed_launch` gives ROS 2 typed, schema-driven launch files: a Pydantic model is the
interface of a launch, and the `tlaunch` command turns it into a command line, YAML config
layering and validation. A launch can also be an imperative **script** that starts things, waits
for them and stops them in order. Status: **working prototype**, Jazzy, 66 tests,
`ament_cmake`.

## Quick start (run the demo)

The repo root is the package; it is built in the colcon workspace `~/ros2_ws`
(`src/ros2_typed_launch` links to this repo).

```bash
cd ~/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install        # once, and after adding a launch/config file
source install/setup.bash             # tlaunch on PATH + tab completion come from this alone

tlaunch ros2_typed_launch turtle_demo_launch.py -h    # see every option, generated from the schema
tlaunch ros2_typed_launch turtle_demo_launch.py       # run it (opens a turtlesim window)
tlaunch ros2_typed_launch turtle_demo_launch.py \
    --config ros2_typed_launch:examples/config/blue_turtle.yaml \
    --turtlesim.background_r 99 --no-teleop.enabled     # config file + CLI override
tlaunch ros2_typed_launch turtle_showcase_launch.py \
    --scope.global_params use_sim_time=true --timers.stop_after 15   # tour of launch constructs
tlaunch ros2_typed_launch turtle_bringup_script.py --settle 2   # imperative: bring up, check, drive, stop
python3 ~/projects/ros2_typed_launch/examples/launch/turtle_bringup_script.py -h   # run directly
```

Every example, with its own commands: `../examples/README.md`.

`<package> <file>` is the full filename (`.py` required), found by searching the package
for a `*_launch.py` file with that name (like `ros2 launch <package> <file>` searches
`share/<package>/`) and then loaded with a real Python import — launch files live in a
directory CMake installs as a real nested subpackage (the examples: `examples/launch/` →
`ros2_typed_launch.examples`). An explicit dotted (`examples.turtle_demo_launch`) or
on-disk-slash (`examples/turtle_demo_launch.py`) path also works, and is needed only if the filename is
ambiguous. Add `--show-config` to any of them to print the effective values and where each
came from, without launching. Stop a running launch with Ctrl-C. More detail and
headless/scripted use: `../CLAUDE.md`.

## Documents

| File | What it is | Read it when |
|---|---|---|
| `DESIGN.md` | The current design: a run end to end, modules, config, processes and panes, boundaries with stock launch | getting oriented |
| `PROGRESS.md` | Done, uncommitted, next steps | picking up work |
| `design-notes.md` | Principles, **decided** table, **open questions** (with current thinking), and reference notes (stock `ros2 launch` internals, schema→CLI mapping, Pydantic 1-vs-2, deferred tmux/overrides designs) | deciding what to build next, or checking why something is the way it is |
| `schema-interface-and-input-dict.md` | Study of darts `InputDict`: pros/cons, the pluggable-backend proposal, and the **units plan** (U1–U6) | revisiting units or schema backends |
| `prototypes/` | `nav_demo_launch.py` + `nav.yaml`: a richer sample schema, run by path | trying the CLI on a non-ROS schema |

## Status

`PROGRESS.md` (done, uncommitted, next). Open questions: `design-notes.md`.
