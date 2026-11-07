# Design

How `lte_launch` works (branch `engine-lte`). Goals and direction: [goals.md](goals.md).
Stock launch feature by feature: [parity.md](parity.md). Background and history of each question:
[`working/design-notes.md`](../working/design-notes.md) (Q numbers).

## The idea

A launch is a **function of a validated config object that starts what it needs**. A Pydantic
model is its interface; the command line, YAML config files, help, JSON Schema and stock launch
arguments are adapters around it. The body runs top to bottom: `node()` starts the process
there and returns a handle, waits block, and any death fails the current line.

```python
@launch
def turtle_bringup(config: BringupConfig):
    with namespace(config.namespace):
        sim = node("turtlesim", "turtlesim_node", name="turtlesim", params=config.turtlesim)
        sim.wait_for_node()
        if config.drive:
            process("driver", "ros2 topic pub ...", ready="publishing")
```

| Schema form | Body gets |
|---|---|
| `@launch` + `def f(config: MyConfig)` (the only parameter, a model) | the config object |
| `@launch(config=MyConfig)` + `def f(config)` | the config object |
| `@launch` + `def f(ns: str = "demo", rate: int = Field(5, ge=1))` | each field by name; `f.config_type` is the generated model, the docstring its help |

## Decisions

| Decision | Why |
|---|---|
| Our own engine on libtmux_easy `Processes`, not `LaunchService` | Synchronous bodies, real handles, exact exit codes, one backend for subprocesses, tmux and tests |
| The body runs on the main thread; waits poll | Ctrl-C is a plain `KeyboardInterrupt` wherever the body is |
| Any unexpected exit is a death; a death fails the current line and the run (exit 1) | Stock exits 0 when a node died |
| Teardown signals every process at once | One at a time is too slow; each escalates on its own deadline |
| A failed component load or lifecycle transition raises at its line | Stock only logs it, and the system comes up half-configured |
| Components in a container started elsewhere are *held*: unloaded at teardown | The launch owns what it loaded; stock leaves them behind |
| Lifecycle nodes get `shutdown` before SIGINT | Their `on_shutdown` (hardware release) runs; stock only signals |
| A stock include is one process; its `process has died` lines are deaths | No per-node view, but no silent failures |
| Node parameters, lowest first: body scopes, config sections, the node's own | Stock's order (`SetParameter` under a node's own); the operator's config overrides the author's scope |
| All Pydantic access in `schema.py`; ROS and libtmux_easy imported lazily | Swappable schema backend; the core imports and tests without ROS |

## A run, end to end

| Phase | What happens | On error |
|---|---|---|
| 1. Configure | `loader.resolve`, one flag per field, defaults < `--config` < flags, validation, `$(var)` in node sections | Nothing started. Exit 2 |
| 2. Bring up | The body runs. Each `node()` checks the executable (ament index), writes a params file, starts | Teardown, exit 1; the error names the line |
| 3. Up | `[INFO] [tlaunch]: up: N process(es) running[, M held elsewhere]` (a ready line for tests) | -- |
| 4. Supervise | Until Ctrl-C, `shutdown()`, a `required` process ending, a death, every process done, or a held container leaving the graph | Exit 1 |
| 5. Teardown | The end sequence below | Summary of deaths |

Exit code: 0 clean or interrupted, 1 a death, failed bringup or failed `on_shutdown`, 2 a config
error.

### The end sequence (`Run._end`)

1. `on_shutdown` callbacks, last registered first; every process still up.
2. Lifecycle nodes: `shutdown` to each of ours still running, all at once (5 s cap).
3. Held components unloaded (before the stop: their container may be one of ours).
4. Every process: SIGINT, SIGTERM after its `stop_timeout` (default `--stop-timeout`, 10 s),
   SIGKILL 5 s later. In parallel. A second Ctrl-C kills everything and skips what is left.
5. The engine's rclpy node is dropped.

`tlaunch` runs it from `teardown()`; a test that used `start(procs)` gets the same sequence from
its own `procs.close()` (`bind_close`), so it leaves nothing loaded or running.

## Modules (`lte_launch/`)

| Module | Owns |
|---|---|
| `core.py` | `@launch` (three schema forms), `TypedLaunch` (`start(procs)`, `build`, `panes`, `plan`), `main()` |
| `api.py` | What a body calls: `node`, `process`, `include`, scopes (`namespace`, `window`, `environment`, `ros_params`, `ros_remaps`), waits, `shutdown`, `on_shutdown`, `share` |
| `engine.py` | `Run` (scopes, start, deaths, waits, supervise, end sequence), `Proc`, `ProcHandle`, stock-include death lines, `execute()`, tmux session |
| `composable.py` | `component(into=)`, `container()`, `container_at()`, `Container.load()`, `Component` |
| `lifecycle.py` | `lifecycle_node()`, `lifecycle_at()`, `LifecycleNode`, teardown `shutdown` |
| `command.py` | A node's argv without launch_ros: executable lookup, `--ros-args`, params file, prefix |
| `loader.py` | Target resolution: `<pkg> <file>` search, dotted/slash paths, standalone files |
| `schema.py` | **All** Pydantic access, including the model built from a signature |
| `config.py`, `subst.py` | YAML, `<pkg>:<path>` refs, deep merge, `launch_args` split; `$(...)` |
| `ros_config.py` | Node patterns, `ros__parameters`/`ros__remappings`, name checks |
| `effective.py` | The effective config as a loadable YAML file with provenance |
| `cli.py` | The command: parser, `configure`, `--show-config`, `--dry-run`, `run()` |
| `stock.py` | A typed launch as a stock `LaunchDescription` (one `tlaunch` process) |

## Processes

Every process runs in a **libtmux_easy `Processes` group** (`libtmux_easy.processes`):

| Mode | Group | Output |
|---|---|---|
| default | subprocesses | `[window:name] line` on stdout (line-buffered, also into a pipe); `<log dir>/<window>.<name>.log` |
| `--tmux`, `--tmux-dash` | wrap-mode panes in a new session, or the current one inside tmux | the pane (a TTY) and the log |
| `--dry-run` | none | the plan: `window:name` and command line; loads and transitions as `ros2 component load` / `ros2 lifecycle set` |
| a test | the test's own group (`start(procs)`, `build(session)`) | the test's logs |

| Option | Behaviour |
|---|---|
| `exits=` | Codes it may end with on its own (default `(0,)`; None: any end is a death) |
| `respawn=`, `respawn_max=` | Start again after a delay; still "pending" until the new run's end is handled, so supervise never sees a gap |
| `required=` | Its end ends the run |
| `stop_timeout=` | Its own SIGINT grace (libtmux_easy `start(sigint_timeout=)`) |
| `prefix=` | Replaces `--launch-prefix` for this process, filter ignored (`""`: none), as stock |
| `window=`, `with window()` | tmux window; default the namespace, else the launch's name |

`lte-run` gives exact exit codes and signals in panes. tmux session: closed on a clean end; kept
on a failure or with `--tmux-dash`.

## `on_shutdown(fn)`

| | |
|---|---|
| When | Any end of the run, at step 1 of the end sequence; never in a dry run |
| Argument | `fn(reason)` if it takes one: a str with `.kind` (`interrupted`, `shutdown`, `died`, `failed`, `finished`) |
| Inside | Waits, `process()`, service calls work; deaths and `shutdown()` no longer interrupt them |
| Errors | Logged, the rest still run, exit 1. Past `timeout` (default 10 s): logged, its next wait raises |
| How | Each on a daemon thread of its own, so it can time out; also a decorator (`@on_shutdown(timeout=30)`) |

## Composable nodes

`component(pkg, plugin, name=, into=)` runs a component where one value says, so a string config
field picks it from the command line:

| `into` | Runs |
|---|---|
| None, `""` | standalone: `executable=` as a node, else in a container of its own (`<name>_container`) |
| `"box"` | in our container `box`, started on first use (or earlier by `container("box", kind=)`) |
| `"/robot/box"` | in that container: ours if we started it, else one already running (`container_at`) |
| a `Container` | in it |

- `container()` starts `rclcpp_components`'s container as a node (`kind`: single, multithreaded,
  isolated). `load()` calls `_container/load_node` from the engine's rclpy node.
- `container_at()` waits for the load service. Components loaded there are held: the run stays
  up until Ctrl-C, and fails if the container leaves the graph (two misses, 2 s apart).
- `Container` and `LifecycleNode` are `ProcHandle`s: the process handle's attributes pass
  through, and one started elsewhere says so.

## Lifecycle nodes

```python
cam = lifecycle_node("pkg", "exe", name="camera")   # name required: it addresses the services
cam.transition("configure")                          # blocks; refused or failed: LaunchError here
cam.activate()                                       # set_state("active"): only what is left
cam.wait_for_state("active", timeout=10)             # someone else drives it
```

| Piece | Behaviour |
|---|---|
| `transition(label)` | `<node>/change_state` by label; -> the new state. Failure names the state it ended in and, if refused, what is available |
| `set_state(state)`, `activate()` | Walks configure/activate/deactivate/cleanup/shutdown to a primary state |
| `state`, `transitions()`, `wait_for_state()` | `get_state`, `get_available_transitions`; polled |
| `autostart=True` / `"inactive"` | Reached before `lifecycle_node()` returns |
| `lifecycle_at(name)` | A node started elsewhere; finalized at teardown only with `finalize=True` |

## Config and ROS parameters

```yaml
launch_args:              # this launch's schema; unknown keys are errors
  turtlesim: {namespace: demo}
/**/turtlesim:            # node pattern; untyped, never checked against a schema
  ros__parameters: {background_r: 99}
  ros__remappings: {turtle1/cmd_vel: /$(var turtlesim.namespace)/joy_vel}
```

A node's parameters and remaps, lowest first:

| Layer | From |
|---|---|
| Body scopes | `with ros_params(use_sim_time=True):`, `with ros_remaps({...}):` (nested: the inner wins) |
| Config | Matching node sections, `--ros-param`, `--ros-remap` |
| The node's own | `params=` (RosParams or dict; typed beats untyped, a note under `-d`), `remaps=` |

- `$(...)` in any value uses the stock grammar (`$(env V)`, `$(find-pkg-share p)`, and
  `$(var NAME)` for this launch's own arguments). A substituted parameter is read as YAML again,
  as stock does (`$(env RATE 30)` is 30); quote inside to keep a string (`"'$(env V)'"`); an
  empty result is an error. `--show-config` notes `from $(...)` on it.
- Applies to `node()`, `container()` and `load()`; not to `process()` or a stock include.
- Node sections reach a node only with a `name`; the namespace comes from `namespace()` scopes.
- Every run writes the effective config to `/tmp/tlaunch/<launch>-<pid>.yaml`; `--show-config`
  dry-runs the body, so it shows which node each pattern matched. Scope values are code, not
  config, so it does not list them.

## Boundaries with stock ROS 2 launch

| Direction | How |
|---|---|
| typed includes typed | Python import + `include(child, config or **fields)`: a function call |
| typed includes stock | `include("pkg", "file.launch.py", **args)` or `include(path, **args)`: one `ros2 launch` process |
| stock includes typed | Stub in `share/` (`lte_launch_install_stubs`); one launch argument per field, validated at include time; runs one `tlaunch` process (`stock.py`) |

A stock include's deaths: `ros2 launch` exits 0 whatever its processes did, so the engine reads
its `process has died [pid P, exit code C` lines (a per-line hook through
`Processes.start(fail_on=)`; regex as lte_launch_test's `launch_death`).

- An inner end outside `exits` is a death (`talker-1 in w:inc`, its `[talker-1]` lines in the
  report). `allow=["talker"]` exempts one (e.g. one stock respawns itself).
- Ignored while we stop the include. Supervise waits 0.3 s after the last process ends, for a
  death line still being read.
- `name=`, `window=`, `exits=`, `allow=`, `ready=`, `timeout=` are ours, not launch arguments.

## Testing

| Way | How |
|---|---|
| Unit and dry-run tests | `python3 -m pytest test -m "not live"`: under a second, no ROS needed |
| Live tests | `@pytest.mark.live`: real processes, nodes, containers, tlaunch end to end; skip without ROS/tmux/libtmux_easy |
| lte_launch_test | `examples/lte_test/`: tlaunch as one process, `procs.build(target.build)`, `target.start(procs)`, `target.panes()` |

Live tests use unique names per test and wait on conditions, not sleeps (`test/_live.py`).

## External pieces

| Piece | Used for |
|---|---|
| libtmux_easy (>= 0.4.0) | `Processes`, `TmuxSession`, `lte-run`, `lte-dash`. Improvements go back there |
| rclpy, composition_interfaces, lifecycle_msgs | Graph waits, component loading, lifecycle transitions |
| Pydantic 1 (system) | The schema. Only `schema.py` touches it |
