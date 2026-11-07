# Goals and direction

What `ros2_typed_launch` is for, the principles that follow, and where the design should go
next. Sections marked **Proposed** are not built yet. The rest describes what exists.
How it works today: [design.md](design.md). How it compares with other tools:
[comparison.md](comparison.md).

## Goals

| # | Goal | Measure |
|---|---|---|
| G1 | **Simple, line-efficient launch files** that compete with YAML launch on brevity and readability | A simple launch is no longer than its YAML equivalent. The author never writes a `Substitution`, a `LaunchConfiguration` or an `OpaqueFunction`. |
| G2 | **A well-defined schema per launch** | Every argument has a type, a default (or `[required]`), constraints and help. `-h`, `--schema`, config files and errors are generated from that schema. |
| G3 | **Fail loudly and early** | Everything is validated before anything starts. Unknown keys, typos and unmatched patterns are reported. A node that dies gives a non-zero exit code. |
| G4 | **Mix freely with stock launch** | A typed launch can include a stock one, and a stock launch can include a typed one. Stock actions pass through unchanged. |
| G5 | **Answer "what actually ran?"** | Every run saves its effective config with provenance. It can also log final command lines, and with `--tmux`, give each process its own pane. |
| G6 | **Control when you want it** | Ordered bringup (start, wait, check, continue) as a script or as declared checks. Nobody has to write an event-handler chain. |
| G7 | **Fit the test and tmux tools we use** | A typed launch is testable with [lte_launch_test](../../lte_launch_test) (the preferred launch test method) and runs on [libtmux_easy](../../libtmux_easy) panes and dashboard. Improvements go back to libtmux_easy, not around it. |

Non-goals, for now: a node-side parameter library (Q24, deferred), our own XML/YAML frontend,
distros other than Jazzy, and replacing ROS's process model.

## Principles

1. **The schema is the contract.** A launch is a function from a validated config object to
   the things it starts. The CLI, YAML, JSON Schema and help are adapters around that.
2. **One spelling everywhere.** The field path in code is also the flag and the YAML key
   (`planner.max_iterations`).
3. **Validate before anything starts**, and name the flag or key in every error.
4. **Silently accepting a mistake is a bug.**
5. **Config values are real Python values**, not deferred substitutions. Because of this, `if`,
   `for` and f-strings work in a launch body.
6. **Stock actions pass through untouched.** We add to stock launch. We do not fork it.
7. **All Pydantic access lives in `schema.py`**, so the schema backend can be swapped cheaply.
8. **A plain `import` beats a dynamic lookup** when the target is known when the file is
   written.
9. **YAML for values, Python for structure.** Config files carry what varies between runs.
   Python carries what is launched and how it connects.

## The YAML argument

YAML launch reads well because it is flat, needs no imports, and is mostly data. It breaks
down when a launch needs any of these:

| Need | YAML launch | Typed Python |
|---|---|---|
| N robots, sensors or cameras | Copy and paste | `for` |
| A derived value (frames from a robot name) | `$(eval ...)` strings | f-string |
| Two conditions combined | `$(eval '... and ...')` | `if a and b:` |
| Reuse | Include a file, with string args | Call or include a function, with typed args |
| Types and ranges | Everything is a string | Pydantic, checked before start |
| Errors | At run time, often mid-launch | Before anything starts, naming the key |

Point 9 is the answer for a YAML-minded colleague: they keep YAML for everything they would
actually edit (`launch_args`, `ros__parameters`, `ros__remappings`). A param-heavy bringup is
mostly YAML already. Python is left with structure, where it beats YAML. G1 makes sure that
remaining Python does not lose to YAML on line count.

### The same launch three ways

turtlesim in a namespace, with an optional teleop and one parameter with a range.

Stock YAML (13 lines, no type or range check):

```yaml
launch:
- arg: {name: ns, default: demo}
- arg: {name: teleop, default: "true"}
- arg: {name: background_r, default: "0"}
- group:
  - push-ros-namespace: {namespace: $(var ns)}
  - node:
      pkg: turtlesim
      exec: turtlesim_node
      name: sim
      param: [{name: background_r, value: $(var background_r)}]
  - node: {pkg: turtlesim, exec: turtle_teleop_key, name: teleop, if: $(var teleop)}
```

Stock Python (about 25 lines, no type or range check, `IfCondition` instead of `if`).

Typed, **today** (14 lines plus imports):

```python
class Config(BaseModel):
    ns: str = "demo"
    teleop: bool = True
    background_r: int = Field(0, ge=0, le=255)

@launch(config=Config)
def turtles(config):
    actions = [node("turtlesim", "turtlesim_node", name="sim", namespace=config.ns,
                    parameters=[{"background_r": config.background_r}])]
    if config.teleop:
        actions.append(node("turtlesim", "turtle_teleop_key", name="teleop",
                            namespace=config.ns))
    return actions

generate_launch_description = turtles.generate_launch_description
```

Typed, **proposed** (8 lines plus imports, typed and validated):

```python
@launch
def turtles(ns: str = "demo", teleop: bool = True,
            background_r: int = Field(0, ge=0, le=255)):
    with namespace(ns):
        node("turtlesim", "turtlesim_node", name="sim",
             params={"background_r": background_r})
        if teleop:
            node("turtlesim", "turtle_teleop_key", name="teleop")
```

## Proposed: cutting boilerplate (G1)

Each item stands alone. Ordered by lines saved per launch. Borrowing from simple_launch
(P1, P4–P7) was agreed in principle on 2026-11-05.

| # | Proposal | Saves | Notes |
|---|---|---|---|
| P1 | **Implicit collection.** `node()`, `include()` and friends register into the launch being built (a context variable, like `ros_config.current()`). A function returning `None` uses what was collected. One returning a list keeps today's behaviour. | `actions = [...]`, `.append`, `return` | Open: a node passed into a stock action (`TimerAction(actions=[node(...)])`) would also be collected. Possible fixes: context managers (`with after(5.0):`) for the common stock wrappers, plus `add(action)` for the rest. |
| P2 | **Signature as schema.** `@launch` with no `config=` builds the model from the function's parameters (`create_model` in `schema.py`). | The config class, for small launches | A model is still allowed, and better past about 6 fields or when nested. Nested models work as annotations. |
| P3 | **Auto-export.** `@launch` sets `generate_launch_description` in its module when it is the module's only target. | 1 line per file | A module with two targets still assigns it by hand. |
| P4 | **Scope context managers.** `with namespace(ns):` and `with group(when=...)`, borrowed from simple_launch. They also feed node-pattern matching, which closes the gap where `PushRosNamespace` is invisible to it (Q8). | `namespace=` on every node | Needs P1. |
| P5 | **`params=` takes a dict** as well as a `RosParams` model. `remaps=` takes a dict. | `parameters=[{...}]` wrapping | Small change in `node()`. |
| P6 | **`stock_include("pkg", "file.launch.py", **args)`.** Finds the file, converts the args with `stock_arg`, and wraps the result. | 3 imports + `get_package_share_directory` + `PythonLaunchDescriptionSource` | The most common stock boilerplate in real launches. |
| P7 | **`container()` and `composable()`** for composable nodes, with node sections applied. | The `ComposableNodeContainer` boilerplate | The current gap: there is no wrapper, so composable nodes get no config. |

Not proposed: helpers specific to one domain (`robot_state_publisher`, `rviz`, Gazebo) in the
core. If wanted, they go in a separate module.

## The runtime model

### Why does stock launch build a list and run it later?

These are reasons, not excuses. Each one is real:

1. **One intermediate form, three frontends.** XML, YAML and Python all produce the same
   `LaunchDescription` of actions. A frontend written as data cannot call `Node()` and start a
   process, so the actions are the common format. The Python API is that internal form exposed
   raw: it is the compiler's syntax tree, not a language designed for people. That is most of
   why it is verbose.
2. **Arguments are known late.** An included file's arguments are set by its includer at run
   time, scoped through groups. `ros2 launch -s` lists arguments without running anything.
   Substitutions exist because the description is built *before* argument values are known.
3. **Processes outlive the call.** A process is not a function call. It needs signal
   escalation, respawn, `OnProcessExit`/`OnProcessIO` handlers, lifecycle transitions and
   ordered shutdown. Something has to own it after `Node()` returns, and that something is the
   event loop.
4. **Introspection.** `launch_testing` and tooling build a description and inspect it, or hook
   into its events, without starting it.
5. **Heritage.** roslaunch XML was declarative, and ROS 2 kept that model.

### Where the opacity actually comes from

The opacity comes from **deferred evaluation**, not from deferred starting. Values are
`Substitution`s until execution. Conditions are expressions in strings. `OpaqueFunction` hides
logic inside the loop. Errors surface mid-launch as "Caught exception in launch". Other failures
are silent, such as typo'd arguments, a dead node with exit code 0, or an unmatched parameter
section.

tlaunch already removes the deferral. The config is resolved and validated first, then the
launch body runs eagerly as plain Python with real values. `if` and `for` work, and a mistake
raises a normal traceback at its line before anything has started. The only thing still
deferred is starting the processes. The silent failures are fixed separately: unknown-key
errors, the death report, `NO MATCH` in `--show-config`.

### Should `node()` start the node right there?

**Open, and leaning towards yes.** `working/vision.md` explores a synchronous engine built on
libtmux_easy. The arguments below hold **under the stock engine**, which is where we are
today. With an engine that tears down reliably, a failed bringup is just a teardown, and a dry
run becomes a backend instead of a reason to defer.

Under the stock engine, starting on the call would cost:

- **All-or-nothing validation (G3).** If node 10 has a mistake, nodes 1 to 9 are already
  running and have to be torn down. Building first means nothing starts until everything checks
  out.
- **Dry runs.** `--show-config` pattern matches, the planned `--dry-run` and tests all need
  the plan without the processes.
- **Stock including us (G4).** A stock parent needs a `LaunchDescription`.
- **A runtime anyway.** Because of reason 3 above, "start on call" still needs a background
  owner for the processes. That is exactly a `Session`.

So there are two modes, both built on `main`. **Branch `engine-lte` replaces both with one:**
the body is synchronous (`node()` starts now), runs on libtmux_easy, and the plan comes from
a dry-run backend instead of a build ([design.md](design.md)). The analysis below is kept as
the reasoning for `main`:

| Mode | Use when | Feels like |
|---|---|---|
| `@launch` (plan, then run) | Most launches | Synchronous to write: eager Python, real values, errors at the line |
| `@script` (`Session`) | Ordered bringup, waits, restarts, teardown | Fully synchronous: `session.start()` starts now and returns a handle |

`checks.*` sits between the two: ordering declared in a plain launch.

**Proposed (P8): `--dry-run`.** Build the plan, run every `Executable.prepare` hook, then print
each process's final command line, parameters and remaps without spawning anything. This makes
the plan as inspectable as a script is readable.

### Build on stock launch, or replace it?

Split the stack into layers and decide per layer:

| Layer | Owner | Why |
|---|---|---|
| Authoring API (`@launch`, `node`, `include`, scopes) | **Ours** | Where the verbosity lives (G1) |
| Schema, CLI, config layering, provenance | **Ours** | G2, G3, G5 |
| Observability (monitor, exit code, tmux, dashboard, checks) | **Ours, on launch's hooks** | Built: `Executable.prepare`, event handlers |
| Process engine (`LaunchService`, `ExecuteProcess`, signals, events) | **launch** | Hard to get right; already works |
| ROS specifics (`Node` command line, params files, composables, lifecycle) | **launch_ros** | Same |
| Stock interop | **launch** | Running a stock file *requires* a `LaunchService`, and being included requires a `LaunchDescription` |

**Recommendation on `main`: own the authoring layer, rent the engine.** (Reversed on branch
`engine-lte`: the engine is libtmux_easy, `launch` is kept only for stock interop. See
`working/vision.md` for why, and [parity.md](parity.md) for what that costs.) Writing our own engine would mean
re-implementing process supervision, `launch_ros`'s command building and the event system.
Interop (G4) would then still need a `LaunchService` inside it, so we would carry both. The
engine's real pain points are addressable on top of it:

| Pain | Status |
|---|---|
| Dead node, exit 0 | Fixed (monitor) |
| Opaque errors mid-launch | Mostly fixed: eager build, validation first. Remaining: Q22's one-line context for exceptions in a launch body |
| Main-thread ownership | Worked around: the script runs on a worker thread (Q26) |
| "Executed more than once" | Worked around: restart rebuilds the stage (Q26) |

Revisit only if the engine blocks a goal. G7 might: under the stock engine, lte_launch_test
sees a whole launch as one process, never node by node. `working/vision.md` weighs
libtmux_easy as the engine instead. Keeping our API free of launch types (stock actions
remain allowed, never required) leaves that exit open, the same way `schema.py` does for
Pydantic.

## Open decisions

| # | Question | Leaning |
|---|---|---|
| D1 | P1 implicit collection: how to avoid collecting a node twice when it is passed into a stock action | Context managers for the common wrappers, plus `add()` |
| D2 | P2 signature schemas: allow mixing (some parameters plus a model)? | No. Use either a signature or a model. |
| D3 | Keep returning a list as a supported style forever? | Yes. It is the migration path and matches stock. |
| D4 | P8 `--dry-run` vs stock's `--print` | `--dry-run`, showing final command lines |
| D5 | Should `node()` default `output="screen"`? | Yes. It is what people expect, and YAML users set it every time. |
| D6 | Engine: stock launch or libtmux_easy (synchronous `node()`)? | Option C, gate passed 2026-11-06; on branch `engine-lte` the stock-engine code is removed. Approaches A-D in `working/vision.md` |
