# Design

How `ros2_typed_launch` works today. Why each piece is the way it is: `design-notes.md` (Q
numbers below). What is done and what is next: `PROGRESS.md`.

## The idea

A launch is a **function from a validated config object to launch actions**. A Pydantic model
is its interface. Everything else is an adapter around that: the command line, YAML config
files, help, JSON Schema, and stock launch arguments.

```python
@launch(config=TurtleDemoConfig)
def turtle_demo(config):
    return [node(package="turtlesim", executable="turtlesim_node", name="turtlesim", ...)]

generate_launch_description = turtle_demo.generate_launch_description
```

## A run, end to end

```
tlaunch <pkg> <file> [--config F]... [--field value]... [--tmux...]
  │
  ├─ loader.resolve        find <file> in the package, import it, take .typed_target
  ├─ cli.build_parser      one flag per schema field (schema.walk), plus tlaunch's own
  ├─ cli.layer_config      defaults < config files < CLI; launch_args vs node sections
  ├─ schema.validate       every error named by flag/key, before anything starts
  ├─ cfg.expand_sections   $(var ...) in node sections, now that the args are final
  ├─ cli.build_actions     typed.build(config) with the node sections active (node())
  ├─ tmux.patch            optional: processes into panes (Executable.prepare hook)
  └─ LaunchService.run     + monitor handlers; exit code says if a process died
```

A **script** (`@script`) replaces the last two steps with `session.run_session`: the service
owns the main thread, the script runs on a worker and drives it through stage handles (Q26).

## Modules (`src/ros2_typed_launch/ros2_typed_launch/`)

| Module | Owns |
|---|---|
| `core.py` | `@launch`, `@script`, `TypedLaunch`/`TypedScript`; `main()` for `python3 <file>` |
| `loader.py` | Target resolution: `<pkg> <file>` search, dotted/slash paths, standalone files |
| `schema.py` | **All** Pydantic access: `walk` (fields as `FieldSpec`), `validate`, `unknown_keys` |
| `config.py` | YAML loading, `<pkg>:<path>` refs, deep merge, `launch_args` split, `$(var)` checks |
| `subst.py` | `$(...)` expansion through the stock launch parser |
| `ros_config.py` | Node patterns (`/**`, `/ns/*`), `ros__parameters`/`ros__remappings`, name tidying |
| `actions.py` | `include`, `node` (collects matching sections), `window`, `stock_args` |
| `effective.py` | The effective config as a loadable YAML file with provenance comments |
| `cli.py` | The command: parser, layering (`configure`), `--show-config`, running a launch or a script |
| `stock.py` | A typed launch as a stock `LaunchDescription` (`generate_launch_description`) |
| `session.py` | `Session`/`Stage`/`Process`: start, wait (process, output, ROS graph), stop, restart |
| `checks.py` | Declared checks: `node`/`topic`/`output` actions with `then` (Q30) |
| `commands.py` | Final command lines: `--log-level` for nodes, `--log-commands` (Q31) |
| `monitor.py` | Every process's pid/status/output tail; the death report; `/diagnostics` |
| `tmux.py` | Session/windows for panes; the `Executable.prepare` hook; `off_screen` |
| `dashboard.py` | `--tmux-dash`: status file, control socket, home log (`HomeTee`) |
| `scripts/tlaunch-pane` | Per-process pane stand-in (stdlib only) |

## Config

```yaml
launch_args:              # this launch's schema; unknown keys are errors
  turtlesim: {namespace: demo}
/**/turtlesim:            # node pattern; untyped, never checked against a schema
  ros__parameters: {background_r: 99}
  ros__remappings: {turtle1/cmd_vel: /$(var turtlesim.namespace)/joy_vel}
```

- Layering: model defaults < `--config` files (deep-merged, later wins, lists replaced) < flags.
- Node sections reach a node only through `node(...)` with a `name`. Typed `RosParams` beat
  untyped values; `--strict` refuses the untyped lane (Q8).
- `$(...)` is the stock grammar. `$(var NAME)` reads this launch's own arguments, so node
  sections expand after validation (Q25).
- Every run writes the effective config to `/tmp/tlaunch/<launch>-<pid>.yaml` (loadable).

## Processes

**Monitoring (Q27).** Launch returns 0 even when a node died. The monitor tracks every process
launch starts and makes the exit code non-zero for a death nobody asked for (not stopped by us,
not during shutdown). Direct children only.

**Panes (Q28).** Launch keeps owning every process; tmux only displays it.

```
launch ── tlaunch-pane (launch's child: pid, signals, exit status)
             │  split-window -e <launch's env>      unix socket (LTE_DIR)
             ▼                                       ▲ started / exited{code,signal}
          tmux pane: lte-run ──▶ the command
             │ --tee ─▶ out FIFO ─▶ tlaunch-pane stdout ─▶ launch's log, wait_output
```

- `tmux.patch` rewrites each selected process's argv in `Executable.prepare`, the one place
  every process passes through (nodes, includes, timers, stock files).
- `lte-run` (libtmux_easy) is the command's real parent: exact code or signal, no shell.
  The stand-in ends the same way (re-raises the signal), so launch sees `-15`, `3`, ...
- Output is copied back, so log files, the death report and `wait_output()` work as without
  panes; `off_screen` drops only the terminal echo.
- Grouping is an environment variable (`window(name, actions)` → `TLAUNCH_TMUX_WINDOW`).
- No pane available → the stand-in `exec`s the command; the run carries on.

**Dashboard (Q29).** `--tmux-dash` opens libtmux_easy's `lte-dash`. tlaunch writes its status
file from the `@tlaunch_*` pane options and answers its control socket **through launch**
(stop = `ShutdownProcess`, Q = `Shutdown`). Home = this run's own fd 1/2. `--tmux-logs` adds
a `ros2-log-view` window. Prefix keys `g`/`v` are borrowed for the run (`lte-key`).

## Boundaries with stock ROS 2 launch

| Direction | State |
|---|---|
| typed includes stock | Built: stock `IncludeLaunchDescription` + `stock_args(...)` (Q17) |
| stock includes typed | Built (Q17): `ros2_typed_launch_install_stubs` puts a stub in `share/`; one launch argument per field (`stock.py`) |
| typed includes typed | Plain Python import + `include(child, config)` |
| stock actions in typed | Pass through untouched (`TimerAction`, event handlers, ...) |

## External pieces

| Piece | Used for |
|---|---|
| libtmux_easy (`~/projects/libtmux_easy`) | `lte-run`, `lte-dash`, `lte-key`. Improvements go back there |
| ros2-term | `ros2-log-view` for `--tmux-logs` (`TLAUNCH_LOG_VIEWER` overrides) |
| Pydantic 1 (system) | The schema. Only `schema.py` touches it |
