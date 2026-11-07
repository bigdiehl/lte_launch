# Parity with stock ROS 2 launch

Every stock `launch`/`launch_ros` feature (Jazzy) and its equivalent here. Goal: rough parity,
not a copy; where stock needs an action, a body usually needs plain Python.

Status: **yes** built, **partial** built with a gap, **todo** planned, **no** not planned.

## Running processes

| Stock | Here | Status |
|---|---|---|
| `Node(package, executable, name, namespace)` | `node(package, executable, name=, namespace=)` | yes |
| `Node(parameters=[dict, file])` | `params=` (RosParams or dict), `param_files=[...]`, config-file node sections | yes |
| `Node(remappings=)` | `remaps=`, `ros__remappings` sections (wildcards too) | yes |
| `Node(arguments=, ros_arguments=)` | `arguments=`, `ros_arguments=` | yes |
| `ExecuteProcess(cmd, shell=True)` | `process(name, "shell line")` or `process(name, [argv])` | yes |
| `cwd=`, `env=`, `additional_env=` | `cwd=`, `env=`; `with environment(VAR=...)` | yes |
| `respawn`, `respawn_delay`, `respawn_max_retries` | `respawn=<delay or True>`, `respawn_max=` | yes |
| `on_exit=Shutdown()` | `required=True` | yes |
| `sigterm_timeout`, `sigkill_timeout` | Fixed: SIGINT 10 s, SIGTERM 5 s, SIGKILL | partial |
| `output='screen'/'log'`, `output_format` | Always both: `[window:name] line` and a log file | partial |
| `emulate_tty` | `--tmux`: a real TTY per process | yes |
| `prefix=` (gdb, valgrind) | `--launch-prefix`, `--launch-prefix-filter` (all executables) | partial: no per-node prefix |
| `log_cmd` | `--log-commands` | yes |
| `--log-level` | `--log-level LEVEL` or `LOGGER:=LEVEL` | yes |
| `exec_name` | The process name is `name` | yes |

## ROS specifics

| Stock | Here | Status |
|---|---|---|
| `PushRosNamespace`, `GroupAction(scoped)` | `with namespace("ns"):` | yes |
| `SetParameter`, `SetParametersFromFile` | `/**` node section or `--ros-param` | yes, from config; no in-body scope |
| `SetRemap` | `/**` `ros__remappings` or `--ros-remap` | yes, from config |
| `SetUseSimTime` | `--ros-param use_sim_time=true` | yes |
| `ComposableNodeContainer` | `container(name, kind=)` | yes |
| `LoadComposableNodes`, `ComposableNode` | `box.load(package, plugin, name=, params=, remaps=, intra_process=, extra=)` | yes; a failed load is an error (stock only logs) |
| Loading into a container started elsewhere | `container_at("/ns/name")`, or `into="/ns/name"` | yes; unloaded at teardown (stock leaves them) |
| Standalone or composed, by config (nav2's `use_composition`) | `component(..., into=config.container)` | yes: one string field |
| `LifecycleNode`, `LifecycleTransition`, `OnStateTransition` | -- | todo: `node.transition("configure")`, `wait_for_state("active")` |
| `RosTimer` | `sleep()` (wall clock) | partial: no sim time |
| `SetROSLogDir` | Environment `ROS_LOG_DIR` via `environment()` | yes |

## Flow and events

| Stock | Here | Status |
|---|---|---|
| `DeclareLaunchArgument`, `LaunchConfiguration` | The schema, `config.x` | yes, typed |
| `IfCondition`, `UnlessCondition`, `LaunchConfigurationEquals` | `if` | yes |
| `TimerAction` | `sleep(s)` | yes |
| `OpaqueFunction`, `OpaqueCoroutine` | The body is Python | yes |
| `OnProcessStart` | Code after `node()` | yes |
| `OnProcessIO` | `proc.wait_for(regex)`, `ready=` | yes |
| `OnProcessExit` | `proc.wait_for_exit()` | yes, in the body; no callback after bringup |
| `OnShutdown` | `try/finally` in the body covers bringup only | todo: `on_shutdown(fn)` |
| `OnExecutionComplete`, `OnIncludeLaunchDescription` | Sequential code | yes |
| `EmitEvent`, `RegisterEventHandler`, `UnregisterEventHandler` | -- | no |
| `Shutdown` | `shutdown(reason)` | yes |
| `LogInfo` | `log(message)` | yes |
| `SetLaunchConfiguration`, push/pop/reset configurations | Python variables | yes |
| `SetEnvironmentVariable`, `AppendEnvironmentVariable`, push/pop/reset/unset | `with environment(...)` | partial: no append/unset |

## Includes

| Stock | Here | Status |
|---|---|---|
| `IncludeLaunchDescription` of a typed file | `include(typed, config)`: a function call | yes |
| `IncludeLaunchDescription` of a stock file | `include("pkg", "f.launch.py", **args)`: one process | partial: no per-node view or death |
| Stock includes us | Stub in `share/`; one `tlaunch` process | yes |
| XML / YAML front ends | -- | no: stock files are included instead |

## Substitutions

All replaced by Python; `$(...)` in config files still uses the stock grammar (`subst.py`).

| Stock | Here |
|---|---|
| `FindPackageShare`, `FindPackagePrefix` | `share("pkg", "path")`, `ament_index_python` |
| `PathJoinSubstitution` | `os.path.join` |
| `EnvironmentVariable` | `os.environ` |
| `Command`, `FileContent` | `subprocess`, `open` |
| `PythonExpression`, `EqualsSubstitution`, `IfElseSubstitution`, `BooleanSubstitution` | Python |
| `AnonName` | `f"name_{os.getpid()}"` |
| `ThisLaunchFile`, `ThisLaunchFileDir` | `__file__` |
| `ExecutableInPackage`, `FindExecutable` | `node()` looks it up; `shutil.which` |
| `Parameter` (a node's parameter value) | `ros2 param get`; not in the body |

## `ros2 launch` command line

| Stock | Here | Status |
|---|---|---|
| `ros2 launch pkg file` | `tlaunch pkg file` | yes |
| `name:=value` | `--name value`, `--config` files | yes, typed |
| `-s` / `--show-args` | `-h` (generated from the schema) | yes |
| `-p` / `--print` | `--dry-run`, `--show-config` | yes |
| `-d` / `--debug` | `-d` (tracebacks, advisory warnings) | yes |
| `-n` / `--noninteractive` | Not needed: no TTY assumptions | yes |
| `--launch-prefix`, `--launch-prefix-filter` | Same | yes |
| `-a` / `--show-all-subprocesses-output` | Always shown | yes |

## Not in stock, here

Typed schema and flags, `--config` layering with provenance, node patterns, `--strict`, a death
fails the run (stock exits 0), a ready line, `--tmux`, `--tmux-dash`, restarting one process
(`proc.restart()`), lte_launch_test integration (`start(procs)`, `build`, `panes`).

## Next, by value

1. Lifecycle: `transition()`, `wait_for_state()`; a failed transition is an error.
2. Stock include deaths: parse `process has died` lines (lte_launch_test's `launch_death`).
3. `on_shutdown(fn)`, and per-process stop timeouts.
4. In-body `SetParameter`-style scope.
