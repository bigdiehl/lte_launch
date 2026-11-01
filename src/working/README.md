# working/ — project documents

`ros2_typed_launch` gives ROS 2 typed, schema-driven launch files: a Pydantic model is the
interface of a launch, and the `tlaunch` command turns it into a command line, YAML config
layering and validation. A launch can also be an imperative **script** that starts things, waits
for them and stops them in order. Status: **working prototype**, Jazzy, 60 tests,
`ament_cmake`.

## Quick start (run the demo)

This file lives in `src/working/`; the repo root (with `.venv/` and the colcon
`build/`/`install/`/`log/`) is two levels up.

```bash
cd ~/projects/ros2_launch
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install        # once, and after adding a launch/config file or package
source install/setup.bash             # tlaunch on PATH + tab completion come from this alone

tlaunch ros2_typed_launch_demo turtle_demo_launch.py -h    # see every option, generated from the schema
tlaunch ros2_typed_launch_demo turtle_demo_launch.py       # run it (opens a turtlesim window)
tlaunch ros2_typed_launch_demo turtle_demo_launch.py \
    --config ros2_typed_launch_demo:config/blue_turtle.yaml \
    --turtlesim.background_r 99 --no-teleop.enabled     # config file + CLI override
tlaunch ros2_typed_launch_demo turtle_showcase_launch.py \
    --scope.global_params use_sim_time=true --timers.stop_after 15   # tour of launch constructs
tlaunch ros2_typed_launch_demo turtle_bringup_script.py --settle 2   # imperative: bring up, check, drive, stop
python3 src/ros2_typed_launch_demo/launch/turtle_bringup_script.py -h   # the same script, run directly
```

Every demo, with its own commands: `../ros2_typed_launch_demo/README.md`.

`<package> <file>` is the full filename (`.py` required), found by searching the package
for a `*_launch.py` file with that name (like `ros2 launch <package> <file>` searches
`share/<package>/`) and then loaded with a real Python import — launch files live in a
top-level `launch/` directory (sibling to `package.xml`, ROS-conventional) built as a real
nested subpackage. An explicit dotted (`launch.turtle_demo_launch`) or on-disk-slash
(`launch/turtle_demo_launch.py`) path also works, and is needed only if the filename is
ambiguous. Add `--show-config` to any of them to print the effective values and where each
came from, without launching. Stop a running launch with Ctrl-C. More detail and
headless/scripted use: `../CLAUDE.md` (one level up, at the repo's `src/`).

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
