# Comparison

How `lte_launch` compares with stock ROS 2 launch and with
[simple_launch](https://github.com/oKermorgant/simple_launch). The simple_launch details come
from its README (`devel` branch, read 2026-11-05), not from its source code. Goals:
[goals.md](goals.md).

## At a glance

| | Stock Python | Stock XML/YAML | simple_launch | lte_launch |
|---|---|---|---|---|
| Argument types | Strings | Strings | Python defaults, but values are substitutions | Pydantic, nested models; or the function signature |
| Validation | None | None | None | Before start, every error named |
| Typo'd argument | Silently ignored | Silently ignored | Silently ignored | Error, with a suggestion |
| `-h` for a launch | No (needs `-s`) | No (needs `-s`) | No (needs `-s`) | Generated from the schema |
| Conditions | `IfCondition(PythonExpression(...))` | `if="$(eval ...)"` | `with sl.group(if_arg=...)`; `py_eval` to combine | Plain `if` |
| Loops / derived values | `OpaqueFunction` | `$(eval)` only | `OpaqueFunction` mode (`launch_setup`) | Plain Python |
| Namespaces | `GroupAction` + `PushRosNamespace` | `<group>` + `<push-ros-namespace>` | `with sl.group(ns=...)` | `with namespace()` |
| Params from YAML | `parameters=[file]` per node | Same | Same | Node patterns in `--config`, matched to every node |
| Wildcard remaps | Impossible (rcl refuses `/**`) | Same | Same | Yes, interpreted by lte-launch |
| Config layering | No | No | No | defaults < files < CLI, with provenance |
| Composable nodes | `ComposableNodeContainer` | Same | `with sl.container(...)` | `component(into=)`: standalone, our container, or one already running; a failed load is an error; unloaded at the end |
| Lifecycle nodes | `LifecycleNode` + event handlers | Same | Same as stock | `transition()`, `activate()` as lines of the body; a refused transition is an error; `shutdown` at teardown |
| Parameters for many nodes | `SetParameter` | `<set_parameter>` | Same as stock | `with ros_params(...)`, or `/**` in config |
| Shutdown hooks | `OnShutdown` | Same | Same | `on_shutdown(fn)`, before any process is signalled |
| Includes | Path + source + string args | String args | `sl.include(pkg, file, launch_arguments=)` | Typed: `import` + `include(fn, cfg)`; stock: `include("pkg", "file", **args)` (one process; its deaths are ours) |
| Dead node exit code | 0 | 0 | 0 | 1, with a summary |
| Teardown | SIGINT per process | Same | Same | All at once, each on its own `stop_timeout` |
| Ordered bringup | Event handlers | Event handlers | `When(node, OnProcessStart, ...)` | Plain code: `node()` starts now, waits block |
| Per-process TTY / panes | No | No | No | `--tmux`, dashboard |
| Engine | launch | launch | launch | libtmux_easy `Processes` |
| Tests | `launch_testing` | Same | Same | lte_launch_test: one process, `build`, `start(procs)` or the dry-run plan |
| Domain helpers | None | None | robot_state_publisher, rviz, Gazebo bridges | None (not planned for core) |

## simple_launch

A builder object over stock launch. Its README says it exists because "the ROS 2 launch
Python syntax may not be suited for many simple cases". It also notes that since Humble the
XML syntax is almost feature-equal to Python, so it targets simple cases and Gazebo.

```python
sl = SimpleLauncher()
sl.declare_arg('prefix', default_value='', description='name of the robot')
sl.declare_arg('use_gui', default_value=True)
with sl.group(ns=sl.arg('prefix')):
    sl.robot_state_publisher('simple_launch', 'turret.xacro', xacro_args=...)
    sl.joint_state_publisher(use_gui=sl.arg('use_gui'))
return sl.launch_description()
```

**Same aim as G1, different bet.** simple_launch shortens the *spelling* of stock launch but
keeps its *model*: arguments are still substitutions (`SimpleSubstitution`, with `+` and `/`).
To branch on a value you switch to `OpaqueFunction` mode, where the same `sl.arg('x')` returns
a plain Python value instead. lte-launch changes the model: values are resolved first, so the
whole body is ordinary Python and there is one meaning of an argument.

### Worth borrowing

| Idea | Here as |
|---|---|
| `with sl.group(ns=..., when=...)` scopes | Built: `with namespace()`, `window()`, `environment()`, `ros_params()`; `when=` is a plain `if` |
| Implicit registration: `sl.node()` adds itself | Moot: `node()` starts the process at once |
| Dicts instead of lists of pairs (params, remaps, include args) | Built |
| `sl.include(package, file, ...)` finds the file | Built: `include("pkg", "file.launch.py", **args)` |
| `with sl.container(...)` for composable nodes | Built: `component(into=)`, `container()` |
| `sl.find(package, file)` | Plain `get_package_share_directory`, or `$(find-pkg-share)` in config |

### Not borrowing

| Idea | Why not |
|---|---|
| Argument means a substitution or a value depending on the mode | The two-phase confusion we remove (goals.md, "Where the opacity actually comes from") |
| `use_sim_time='auto'` (true if `/clock` is advertised) | Magic that depends on the environment. Its README warns about side effects. |
| Gazebo, robot_state_publisher and rviz helpers in core | Specific to one domain. If wanted, they go in a separate module. |
| Module-level `SimpleLauncher` state | We have a decorated function plus a context variable, scoped per build |

## Stock Python launch

The verbosity is structural (goals.md, "Why does stock launch build a list and run it
later?"). The Python API is the internal form shared by the three frontends, exposed raw.
Every value is a substitution, so even simple logic needs `LaunchConfiguration`, `IfCondition`,
`PythonExpression` or `OpaqueFunction`. We replace both how a launch is written and the engine
(libtmux_easy), and keep `launch` only for interop: including stock files, and being included.

## Stock XML/YAML launch

The baseline G1 measures against. It is good for flat launches, and nothing beats it for "five
nodes, three args". It runs out at loops, derived values, combined conditions, reuse and types
(goals.md, "The YAML argument"). lte-launch keeps YAML where it shines: config files, including
untyped `ros__parameters` and `ros__remappings`. In fact a stock ROS parameter file is already
a valid lte-launch config.
