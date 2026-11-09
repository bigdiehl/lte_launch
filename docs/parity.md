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
| `sigterm_timeout`, `sigkill_timeout` | `stop_timeout=` / `--stop-timeout S` (SIGINT's grace, default 10 s); `kill_timeout=` / `--kill-timeout S` (SIGTERM's grace, default 5 s), per process and run-wide | yes (`kill_timeout` needs libtmux_easy 0.5.0) |
| `output='screen'/'log'`, `output_format` | Always both: `[window:name] line` and a log file | partial |
| Launch logs (`~/.ros/log/<run>/launch.log`, `latest`) | `$ROS_LOG_DIR` (else `$ROS_HOME/log`, `~/.ros/log`)`/lte-launch/<launch>-<time>-<pid>/`: `launch.log` (the whole transcript), `<window>.<name>.log` per process, `config.yaml`, `params/`; `lte-launch/latest` links the newest; not on `--dry-run` | yes |
| `emulate_tty` | `--tmux`: a real TTY per process | yes |
| `prefix=` (gdb, valgrind) | `node(..., prefix=)`, also `process()`, `container()`; replaces `--launch-prefix` for that process, filter or not (`""`: none), as stock. A debugger needs `--tmux` | yes |
| `log_cmd` | `--log-commands` | yes |
| `--log-level` | `--log-level LEVEL` or `LOGGER:=LEVEL` | yes |
| `exec_name` | The process name is `name` | yes |

## ROS specifics

| Stock | Here | Status |
|---|---|---|
| `PushRosNamespace`, `GroupAction(scoped)` | `with namespace("ns"):` | yes |
| `SetParameter`, `SetParametersFromFile` | `with ros_params(use_sim_time=True):` in the body; `/**` node section or `--ros-param` from config | yes, also into a stock include |
| `SetRemap` | `with ros_remaps({"/tf": "tf"}):`; `/**` `ros__remappings` or `--ros-remap` | yes, also into a stock include |
| `SetUseSimTime` | `with ros_params(use_sim_time=True):`, `--ros-param use_sim_time=true` | yes |
| `ComposableNodeContainer` | `container(name, kind=)` | yes |
| `LoadComposableNodes`, `ComposableNode` | `box.load(package, plugin, name=, params=, remaps=, intra_process=, extra=)` | yes; a failed load is an error (stock only logs) |
| Loading into a container started elsewhere | `container_at("/ns/name")`, or `into="/ns/name"` | yes; unloaded at teardown, or at `procs.close()` after `start(procs)` (stock leaves them) |
| Standalone or composed, by config (nav2's `use_composition`) | `component(..., into=config.container)` | yes: one string field |
| `LifecycleNode` | `lifecycle_node(pkg, exe, name=, autostart=)`; `lifecycle_at(name)` for one started elsewhere | yes |
| `LifecycleTransition`, `ChangeState` event | `lc.transition("configure")`, `lc.set_state("active")`, `lc.activate()` | yes; a failed transition is an error at its line (stock only logs) |
| `OnStateTransition` | `lc.wait_for_state("active")`, `lc.state`; `@lc.on_state("inactive")` (from `transition_event`) | yes, in the body and after |
| Lifecycle nodes at shutdown | `shutdown` transition to each of ours before the SIGINT (`finalize=False` opts out) | yes; stock only sends SIGINT |
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
| `OnProcessIO` | `proc.wait_for(regex)`, `ready=`; `@proc.on_output(regex)` | yes, in the body and after |
| `OnProcessExit` | `proc.wait_for_exit()`; `@proc.on_exit` | yes; a death is no callback, it fails the run |
| `OnShutdown` | `on_shutdown(fn, timeout=)` (or `@on_shutdown`): LIFO, before any process is signalled, `fn(reason)` | yes |
| `OnExecutionComplete`, `OnIncludeLaunchDescription` | Sequential code | yes |
| `RegisterEventHandler`, `UnregisterEventHandler` | `proc.on_exit(fn)` etc., `proc.off(fn)`, `once=` | partial: these three events only |
| `EmitEvent` | -- | no |
| `Shutdown` | `shutdown(reason)` | yes |
| `LogInfo` | `log(message)` | yes |
| `SetLaunchConfiguration`, push/pop/reset configurations | Python variables | yes |
| `SetEnvironmentVariable`, `AppendEnvironmentVariable`, push/pop/reset/unset | `with environment(...)` | partial: no append/unset |

## Includes

| Stock | Here | Status |
|---|---|---|
| `IncludeLaunchDescription` of a typed file | `include(typed, config)`: a function call | yes |
| `IncludeLaunchDescription` of a stock file | `include("pkg", "f.launch.py", **args)` or `include(path)`: one process; a `process has died` line is a death (`exits=`, `allow=`); `ros_params()`, `ros_remaps()` and `/**` config sections reach its nodes (a generated wrapper: `SetParametersFromFile`, `SetRemap`); `namespace()` does not | partial: no per-node view; other node patterns do not reach it |
| Stock includes us | Stub in `share/`; one `lte-launch` process | yes |
| XML / YAML front ends | -- | no: stock files are included instead |

## Substitutions

All replaced by Python; `$(...)` in config files still uses the stock grammar (`subst.py`), and a
substituted parameter is typed again from YAML, as launch_ros does.

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
| `ros2 launch pkg file` | `lte-launch pkg file` | yes |
| `name:=value` | `--name value`, `--config` files | yes, typed |
| `-s` / `--show-args` | `-h` (generated from the schema) | yes |
| `-p` / `--print` | `--dry-run`, `--show-config` | yes |
| `-d` / `--debug` | `-d` (tracebacks, advisory warnings) | yes |
| `-n` / `--noninteractive` | Not needed: no TTY assumptions | yes |
| `--launch-prefix`, `--launch-prefix-filter` | Same | yes |
| `-a` / `--show-all-subprocesses-output` | Always shown | yes |

## Not in stock, here

Typed schema and flags, a failed lifecycle transition fails the run, lifecycle `shutdown` at teardown, `--config` layering with provenance, node patterns, `--strict`, a death
fails the run (stock exits 0), also one inside a stock include, a ready line, `--tmux`, `--tmux-dash`, restarting one process
(`proc.restart()`), lte_launch_test integration (`start(procs)`, `build`, `panes`).

## Next, by value

Nothing open at parity level. Not planned: the **no** rows above (front ends, `EmitEvent`).
Open gaps: config sections with a specific node pattern into a stock include; a `SIGKILL` wait
(fixed 2 s, as stock).
