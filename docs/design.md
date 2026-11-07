# Design

How `ros2_typed_launch` works today (branch `engine-lte`). Goals and direction:
[goals.md](goals.md). Stock launch feature by feature: [parity.md](parity.md). Why each piece
is the way it is: [`working/design-notes.md`](../working/design-notes.md) (Q numbers below).

## The idea

A launch is a **function of a validated config object that starts what it needs**. A Pydantic
model is its interface; the command line, YAML config files, help, JSON Schema and stock launch
arguments are adapters around it. The body runs top to bottom: `node()` starts the process
there and returns a handle, waits block, and any death fails the current line.

```python
@launch
def turtle_bringup(config: BringupConfig):    # the model is the annotation
    with namespace(config.namespace):
        sim = node("turtlesim", "turtlesim_node", name="turtlesim", params=config.turtlesim)
        sim.wait_for_node()
        if config.drive:
            process("driver", "ros2 topic pub ...", ready="publishing")
```

## A run, end to end

| Phase | What happens | On error |
|---|---|---|
| 1. Configure | `loader.resolve`, one flag per field, defaults < `--config` < flags, validation, `$(var)` in node sections | Nothing started. Exit 2 |
| 2. Bring up | The body runs. Each `node()` checks the executable (ament index), writes a params file, starts | Teardown, exit 1; the error names the line |
| 3. Up | `[INFO] [tlaunch]: up: N process(es) running` (a ready line for tests) | -- |
| 4. Supervise | Until Ctrl-C, `shutdown()`, a `required` process ending, or a death | A death: exit 1 |
| 5. Teardown | SIGINT to **every** process at once, then SIGTERM, SIGKILL; a second Ctrl-C kills | Summary of deaths |

Exit code: 0 clean or interrupted, 1 a death or failed bringup, 2 a config error.

## Modules (`ros2_typed_launch/`)

| Module | Owns |
|---|---|
| `core.py` | `@launch`, `TypedLaunch` (`start(procs)`, `build(session)`, `panes()`, `plan()`), `main()` |
| `api.py` | What a body calls: `node`, `process`, `include`, `namespace`, `window`, `environment`, waits, `shutdown`, `share` |
| `composable.py` | `container()`, `Container.load()` (LoadNode service), `Component` |
| `engine.py` | `Run` (scopes, start, waits, supervise, teardown), `Proc` handles, `execute()`, tmux session |
| `command.py` | A node's argv without launch_ros: executable lookup, `--ros-args`, params file, prefix |
| `loader.py` | Target resolution: `<pkg> <file>` search, dotted/slash paths, standalone files |
| `schema.py` | **All** Pydantic access |
| `config.py`, `subst.py` | YAML, `<pkg>:<path>` refs, deep merge, `launch_args` split; `$(...)` |
| `ros_config.py` | Node patterns, `ros__parameters`/`ros__remappings`, name tidying |
| `effective.py` | The effective config as a loadable YAML file with provenance |
| `cli.py` | The command: parser, `configure`, `--show-config`, `--dry-run`, `run()` |
| `stock.py` | A typed launch as a stock `LaunchDescription` (one `tlaunch` process) |

## Processes

Every process runs in a **libtmux_easy `Processes` group** (`libtmux_easy.processes`):

| Mode | Group | Output |
|---|---|---|
| default | subprocesses | `[window:name] line` on stdout; `<log dir>/<window>.<name>.log` |
| `--tmux`, `--tmux-dash` | wrap-mode panes in a new session, or the current one when inside tmux | the pane (a TTY) and the log |
| `--dry-run` | none | the plan: `window:name` and command line |
| a test | the test's own group (`start(procs)`, `build(session)`) | the test's logs |

- `lte-run` gives exact exit codes and signals in panes.
- A death is any exit nobody asked for, with a code outside `exits` (default `(0,)`). The
  group's `on_exit` tells the engine at once; the current wait raises.
- `respawn=`, `required=`: handled by the engine on that same event.
- Window names: the namespace (`demo`), or `window("input")`, or the launch's name.
- tmux session: closed on a clean end; kept on a failure or with `--tmux-dash`.

## Composable nodes

`component(pkg, plugin, name=, into=)` puts a component where `into` says:

| `into` | Runs |
|---|---|
| None, `""` | standalone: `executable=` as a node, else in a container of its own (`<name>_container`) |
| `"box"` | in our container `box`, started on first use (or by `container("box", kind=)`) |
| `"/robot/box"` | in that container: ours if we started it, else one already running (`container_at`) |
| a `Container` | in it |

- `container()` starts `rclcpp_components`'s container as a node. `load()` calls its
  `_container/load_node` service from the engine's rclpy node, on the same loop as every wait. A
  failed load raises at that line.
- A container started elsewhere: `container_at()` waits for its load service. Components loaded
  there are **held**: the run stays up until Ctrl-C, unloads them at teardown, and fails (exit 1)
  if the container leaves the graph.
- Parameters and remaps are collected as for `node()`. Dry run: each load is a
  `ros2 component load` command.

## Config

```yaml
launch_args:              # this launch's schema; unknown keys are errors
  turtlesim: {namespace: demo}
/**/turtlesim:            # node pattern; untyped, never checked against a schema
  ros__parameters: {background_r: 99}
  ros__remappings: {turtle1/cmd_vel: /$(var turtlesim.namespace)/joy_vel}
```

- Node sections reach a node through `node()`/`load()` with a `name`; the namespace comes from
  `namespace()` scopes, so patterns see it. Typed `RosParams` win; `--strict` refuses the
  untyped lane (Q8).
- Every run writes the effective config to `/tmp/tlaunch/<launch>-<pid>.yaml`.
  `--show-config` dry-runs the body, so it shows which node each pattern matched.

## Boundaries with stock ROS 2 launch

| Direction | How |
|---|---|
| typed includes typed | Python import + `include(child, config)`: a function call |
| typed includes stock | `include("pkg", "file.launch.py", **args)`: one `ros2 launch` process |
| stock includes typed | Stub in `share/` (`ros2_typed_launch_install_stubs`); one launch argument per field, validated at include time; runs one `tlaunch` process (`stock.py`) |

## External pieces

| Piece | Used for |
|---|---|
| libtmux_easy (`~/projects/libtmux_easy`, branch `processes-core`) | `Processes`, `TmuxSession`, `lte-run`, `lte-dash`. Improvements go back there |
| rclpy, composition_interfaces | Graph waits, component loading |
| Pydantic 1 (system) | The schema. Only `schema.py` touches it |
