# Comparison

How `ros2_typed_launch` compares with stock ROS 2 launch and with
[simple_launch](https://github.com/oKermorgant/simple_launch). The simple_launch details come
from its README (`devel` branch, read 2026-11-05), not from its source code. Goals:
[goals.md](goals.md).

## At a glance

| | Stock Python | Stock XML/YAML | simple_launch | tlaunch (branch engine-lte) |
|---|---|---|---|---|
| Argument types | Strings | Strings | Python defaults, but values are substitutions | Pydantic, nested models |
| Validation | None | None | None | Before start, every error named |
| Typo'd argument | Silently ignored | Silently ignored | Silently ignored | Error, with a suggestion |
| `-h` for a launch | No (needs `-s`) | No (needs `-s`) | No (needs `-s`) | Generated from the schema |
| Conditions | `IfCondition(PythonExpression(...))` | `if="$(eval ...)"` | `with sl.group(if_arg=...)`; `py_eval` to combine | Plain `if` |
| Loops / derived values | `OpaqueFunction` | `$(eval)` only | `OpaqueFunction` mode (`launch_setup`) | Plain Python |
| Namespaces | `GroupAction` + `PushRosNamespace` | `<group>` + `<push-ros-namespace>` | `with sl.group(ns=...)` | `with namespace()` |
| Params from YAML | `parameters=[file]` per node | Same | Same | Node patterns in `--config`, matched to every node |
| Wildcard remaps | Impossible (rcl refuses `/**`) | Same | Same | Yes, interpreted by tlaunch |
| Config layering | No | No | No | defaults < files < CLI, with provenance |
| Composable nodes | `ComposableNodeContainer` | Same | `with sl.container(...)` | `container()` + `load()`; a failed load is an error |
| Includes | Path + source + string args | String args | `sl.include(pkg, file, launch_arguments=)` | Typed: `import` + `include(fn, cfg)`; stock: `include("pkg", "file", **args)` (one process) |
| Dead node exit code | 0 | 0 | 0 | Non-zero, with a summary |
| Ordered bringup | Event handlers | Event handlers | `When(node, OnProcessStart, ...)` | Plain code: `node()` starts now, waits block |
| Per-process TTY / panes | No | No | No | `--tmux`, dashboard |
| Engine | launch | launch | launch | libtmux_easy (branch `engine-lte`) |
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
a plain Python value instead. tlaunch changes the model: values are resolved first, so the
whole body is ordinary Python and there is one meaning of an argument.

### Worth borrowing

| Idea | Here as |
|---|---|
| `with sl.group(ns=..., when=...)` scopes | P4 `with namespace()` / `with group()` |
| Implicit registration: `sl.node()` adds itself | P1 implicit collection |
| Dicts instead of lists of pairs (params, remaps, include args) | P5, P6 |
| `sl.include(package, file, ...)` finds the file | P6 `stock_include` |
| `with sl.container(...)` for composable nodes | P7 |
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
`PythonExpression` or `OpaqueFunction`. We keep its engine and its actions, and replace how a
launch is written.

## Stock XML/YAML launch

The baseline G1 measures against. It is good for flat launches, and nothing beats it for "five
nodes, three args". It runs out at loops, derived values, combined conditions, reuse and types
(goals.md, "The YAML argument"). tlaunch keeps YAML where it shines: config files, including
untyped `ros__parameters` and `ros__remappings`. In fact a stock ROS parameter file is already
a valid tlaunch config.
