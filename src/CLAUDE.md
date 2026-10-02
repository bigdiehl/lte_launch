# ros2_typed_launch

Typed, schema-driven launch files for ROS 2 (Jazzy). A Pydantic model is the
public interface of a launch; the launch body is plain Python from validated
config -> list of launch actions. Run with our own command, **`tlaunch`**.

Read first, and keep updated when state or decisions change (index: `working/README.md`):
`working/design-notes.md` (principles, decided/open questions, reference notes),
`working/schema-interface-and-input-dict.md` (darts `InputDict` study, **units plan U1–U6**).

## Direction (decided)

- `tlaunch` is our own command (not a hook into `ros2 launch`), normal argparse syntax:
  flags are **snake_case, exactly the dotted field path** (`--planner.max_iterations`),
  `--x/--no-x` for bools, repeatable `--tags a --tags b` for lists, repeatable
  `--remap key=value` for dicts. No `name:=value`.
- **A launch is addressed as `tlaunch <package> <file>` or `tlaunch <path>`.** `<file>`
  is the **full filename, `.py` required** (`turtle_demo_launch.py`; the bare stem alone
  is rejected with a hint), found by **searching the package for a `*_launch.py` file
  with that name**, however deep (e.g. inside a nested `launch/` subpackage) — the way
  `ros2 launch <package> <file>` searches `share/`, but over real Python files. The match
  is loaded with a real `importlib.import_module`. An explicit dotted
  (`launch.turtle_demo_launch`, never a `.py`) or on-disk-slash
  (`launch/turtle_demo_launch.py`) path also works, and is required if the full filename
  is ambiguous (two files sharing a name). `<path>` is for a standalone file not part of
  any package (e.g. `working/prototypes/`) — no parent package, so no relative imports
  (same limitation stock `ros2 launch` has for a bare file). A launch is identified by
  its exported `generate_launch_description`, tagged `.typed_target` by `@launch`/`@script`.
  **Typed launch files only**; untyped ones use stock `ros2 launch`.
- Config layering: model defaults < `--config` files (repeatable, **deep-merged, later
  wins; lists are replaced, dict keys merge**) < CLI flags; validated *before* anything
  starts; unknown YAML keys are errors. A config is a path or `<package>:<path>` (looked
  up via the package's share dir via `ament_index_python`, independent of launch-file
  resolution above).
- **A config file has two parts.** The launch's own settings go under **`launch_args:`**
  (required; a bare schema key at the top level errors with the fix). Every other top-level
  key is a **node pattern** (`/**`, `/**/planner`, `/ns/*` — `*` is one token, `**` zero or
  more, as in a ROS parameter file) holding **`ros__parameters:`** and/or
  **`ros__remappings:`** — untyped ROS data passed straight to the matching nodes, needing
  no schema at all. That is the answer to param-heavy nodes: you never restate 100 fields.
  `--ros-param '[PATTERN:]name=value'` / `--ros-remap '[PATTERN:]from=to'` are the CLI
  equivalents (pattern defaults to `/**`, values parsed as YAML). `$(...)` in any value is
  expanded by the **stock** launch parser — `$(find-pkg-share p)`,
  `$(env VAR [default])`, `$(command ...)`, `$(eval ...)`; see `subst.py`.
  **`$(var NAME)` reads this launch's own arguments** by dotted path
  (`/$(var turtlesim.namespace)/joy_vel`), so expansion happens in two stages:
  `launch_args` as each file is read (and `$(var ...)` is refused there — no argument
  built from another), then the node sections, **values and pattern key**, once the args
  are merged and validated, which is what lets a CLI override reach them. A `$(var ...)`
  naming no argument of this launch is caught **against the schema, together with the
  unknown-key check** (so a config meant for another launch reports every mismatch at
  once, before expanding anything). A substitution always yields **text**, so a parameter
  built from one is a string (Q25 has the open question about re-parsing).
- **`node(...)`** (our wrapper over `launch_ros`'s `Node`) is what collects it, merging every
  matching section into that node's parameters/remappings. `params=` takes a **`RosParams`**
  model — a schema section that *is* a node's ROS parameters, declared once and delivered
  without restating. Typed beats untyped on a clash; a pattern matching no launched node is
  not an error (it may target a disabled node, or a stock `Node`). Both are advisory and so
  **quiet unless `-d`**; `--strict` makes them errors and refuses the untyped lane. Plain
  stock `Node` still works, it just collects nothing. Wildcard *remappings* are ours: rcl
  does `/**` for parameters but rejects it for remap rules outright.
- **`--show-config` prints the effective config as a config file** — the same YAML **every
  run writes** to `/tmp/tlaunch/<launch>-<pid>.yaml` and logs, so "what did that actually
  launch with?" is always answerable, and the answer is valid `--config` input (it
  round-trips). Provenance sits on every value, in the node sections exactly as in
  `launch_args` (`# default`, `# <file>`, `# cli`, `, shadowed by a typed field`), with
  matched nodes on the pattern (`# -> /ns/node`, or `# -> NO MATCH` — which is where you
  look for a typo'd pattern). The two lanes keep their own keys; sections sharing a pattern
  merge into one block, so there are never duplicate YAML keys. Renderer: `effective.py`.
- **Both packages build with `ament_cmake`/`ament_cmake_python`**, not `ament_python` —
  needed to install `launch/` as a nested subpackage while keeping it at a top-level,
  ROS-conventional path.
- **Launch files are real Python modules, named `*_launch.py`** (one dot — not
  `*.launch.py`, unimportable: a second dot before `.py` reads as package.submodule),
  living in a **top-level `launch/` directory** (sibling to `package.xml`). CMake
  installs it as a real **subpackage nested under the main package**
  (`ros2_typed_launch_demo.launch.turtle_demo_launch`), not a copy to `share/launch/`.
  Consequence: **a same-package include is a plain (relative) `import`** —
  `from .other_launch import OtherConfig, other`; a child's config class is reused via
  composition (`params: OtherConfig = OtherConfig()` field, *not* subclassing — see
  `turtle_demo_launch.py`). `launch/__init__.py` re-exports every launch's function and
  config, so `from ros2_typed_launch_demo.launch import turtle_demo, TurtleDemoConfig`
  works without naming the submodule. `load_launch("pkg", "module")` (same
  `importlib.import_module` under the hood) is only for a launch chosen dynamically or
  from another package. `include(child, config)` builds the child eagerly and returns a
  scoped `GroupAction`; the parent's namespace/params/remaps/env are inherited.
- **Every run watches the processes it starts** (`monitor.py`): pid, command, node name and exit
  status per process. A death nobody asked for (not stopped by us, not during shutdown, non-zero
  or signalled) gets an `[ERROR] [tlaunch]` line naming the **node** (`/demo/keeper
  (turtlesim_node-1)`), an end-of-run summary, and a **non-zero exit code** — stock launch
  returns 0 for a run whose node died, which is the hazard this removes. A oneshot exiting 0 is
  not a death. `--diagnostics` additionally publishes `DiagnosticArray` on `/diagnostics` at
  1 Hz (named by node FQN, process name in `values`); it is opt-in because it needs an rclpy
  node inside the launch. **Direct children only** — a wrapper script's children and composable
  nodes (threads, no pid) are out of reach. Sessions use the same monitor, so there is one
  implementation. See design-notes Q27, `flaky_demo_launch.py`.
- **A launch can instead be an imperative script**: `@script(config=Model)` over a
  `(session, config)` function, run with `tlaunch <package> <file>` **or** `python3 <file>`
  (`target.main()`). `session.start(target)` queues a **stage** and returns a handle —
  `stage.wait_started()`, `stage["planner"].wait_output("ready")`, `stage.stop()`,
  `stage.restart()`; `session.wait_for_node`/`wait_for_topic` are real `rclpy` graph checks;
  `session.sleep` is interruptible. **The service owns the main thread** (launch's own rule), so
  the script body runs on a worker; Ctrl-C makes the current wait raise `SessionShutdown` so the
  script's `finally` runs, and the session always stops what it started. Exit code: the script's
  int if it returns one, else the service's (130 on Ctrl-C). Scripts are named `*_script.py` and
  live beside launch files; `loader.TARGET_SUFFIXES` is what makes both findable. See
  `session.py`, `turtle_bringup_script.py`, design-notes Q26.
- Stock `launch`/`launch_ros` actions are used as-is (`PushRosNamespace`, `SetParameter`,
  `TimerAction`, event handlers, ...); see `turtle_showcase_launch.py`. A typed launch can
  also **include a traditional (untyped) stock launch file** via stock
  `IncludeLaunchDescription`; `actions.stock_args({name: config.x, ...})` converts typed
  config values to the flat strings stock launch args are always made of (bool ->
  `"true"`/`"false"`, everything else `str()`'d). One-way only — see `stock_include_launch.py`
  and design-notes.md, Q17.
- **Pydantic 1** (system, apt/rosdep `python3-pydantic`), not 2 — launch authors import
  Pydantic directly in their own files, so a system install matters more than v2's
  stricter API. Both `package.xml`s declare `<exec_depend>python3-pydantic</exec_depend>`.
  Accepted trade-off: v1's YAML coercion is lenient (`tolerance: 1.7` for an `int`
  silently truncates to `1`) and `Optional[X]` with no default is silently optional, not
  required. Full comparison and rationale: `design-notes.md`.
- **Deferred (do not work on unless asked):** tmux (would use `~/projects/libtmux_easy`,
  a separate repo with known bugs — don't modify without asking); pluggable schema backends
  / darts `InputDict` (needs `python3-darts-core`, not installable here); **node-defined
  parameter schemas** (Q24 — a node shipping its schema so a launch can look it up instead
  of restating it; explicitly parked 2026-09-27, the untyped lane covers the need for now).
- **Interested, not started:** units (`--robot.radius "22 cm"`); plan in
  `schema-interface-and-input-dict.md`, decisions U1–U6 pending.
- License is not a concern for now.

## Layout

The **repo root is one level above this file** (`src/CLAUDE.md`). It is a **colcon
workspace**: `src/` (packages + docs, below), `build/ install/ log/` (colcon output,
regenerated), `.venv/` (unused leftover — see Environment) — all at the repo root, not
under `src/`.

Two **`ament_cmake`** packages under `src/`:

- `ros2_typed_launch/` — library + `tlaunch` command.
  - `package.xml`/`CMakeLists.txt`: `ament_python_install_package(${PROJECT_NAME})`
    installs the Python package; `install(PROGRAMS scripts/tlaunch DESTINATION
    lib/${PROJECT_NAME})` installs the entry-point script (hand-written, no setuptools
    `console_scripts` indirection); `ament_add_pytest_test` wires `test/` into `colcon test`.
  - `ros2_typed_launch/`: `cli.py` (command), `loader.py` (`resolve()`/`load_launch`:
    find and import a launch, by package+bare-filename search, an explicit
    dotted/on-disk-slash path, or a standalone file path), `schema.py` (**all** Pydantic
    access), `config.py` (YAML, `<package>:<path>` refs, merge), `core.py` (`@launch`,
    `@script`, and the `TypedTarget`/`TypedLaunch`/`TypedScript` trio with their shared
    `main()`), `actions.py` (`include`, `node`, `stock_args`), `ros_config.py`
    (node patterns + the untyped `ros__parameters`/`ros__remappings` lanes), `subst.py`
    (`$(...)` expansion), `effective.py` (the effective config as a loadable YAML file),
    `session.py` (imperative launching: `Session`/`Stage`/`Process`/`run_session`),
    `monitor.py` (process tracking, the death report and `/diagnostics`).
    `test/` — pytest suite (143 tests).
- `ros2_typed_launch_demo/` — demo. **`README.md` lists every demo with its commands** —
  keep it updated when a demo is added or its flags change.
  - `package.xml`/`CMakeLists.txt`: installs the base package, *and* the top-level
    `launch/` directory as a subpackage nested under it (`ament_python_install_package(launch
    PACKAGE_DIR launch DESTINATION ${PYTHON_INSTALL_DIR}/${PROJECT_NAME})` — the trick
    behind the layout below).
  - `launch/`: `turtle_demo_launch.py`, `turtle_teleop_launch.py`,
    `localization_launch.py`, `turtle_showcase_launch.py`, `stock_include_launch.py`,
    `turtle_params_launch.py` (typed `RosParams` + the untyped lanes via `node()`), and
    `turtle_bringup_script.py` (**imperative**: `@script`, stages, graph checks, restart),
    `flaky_demo_launch.py` (a process that dies, to show the death report) —
    ordinary submodules of `ros2_typed_launch_demo.launch`, importing each other directly;
    `__init__.py` re-exports all eight.
  - `config/*.yaml` — example `--config` files, installed to the share dir (unaffected
    by where the Python launch files live).
  - `stock_launch/spawn_turtle.launch.py` — a traditional (untyped) launch file, installed
    to `share/ros2_typed_launch_demo/launch/` like any stock package would (not part of the
    Python package); included by `stock_include_launch.py` via stock
    `IncludeLaunchDescription` + `actions.stock_args`.
- `working/` — docs + `prototypes/` (`nav_demo_launch.py`, `nav.yaml`: richer sample
  schema, not part of a package — run by path). `COLCON_IGNORE`d, as is `.venv/`.

## Environment and commands

ROS 2 Jazzy at `/opt/ros/jazzy`, Python 3.12, Ubuntu 24.04. Not a git repository. colcon,
pytest and Pydantic all come from the **system** (`python3-pydantic` v1.10, apt/rosdep) —
`ros2`/`tlaunch` run on `/usr/bin/python3`, which sees it by default, so **no
`PYTHONPATH` changes are needed anywhere**. Run everything from the **repo root**. No
wrapper script: just source ROS, then this workspace's install overlay.

```bash
cd ..                                                                 # repo root
source /opt/ros/jazzy/setup.bash && colcon build --symlink-install    # build
source install/setup.bash         # tlaunch on PATH + tab completion come from this alone

cd src/ros2_typed_launch && python3 -m pytest test -q                # tests
cd ../..                          # back to repo root
colcon test --packages-select ros2_typed_launch ros2_typed_launch_demo && colcon test-result

tlaunch ros2_typed_launch_demo turtle_demo_launch.py -h              # schema-generated help
tlaunch ros2_typed_launch_demo turtle_demo_launch.py \
    --config ros2_typed_launch_demo:config/blue_turtle.yaml \
    --config ros2_typed_launch_demo:config/quiet.yaml \
    --turtlesim.background_r 99 --show-config                        # layering + provenance, no launch
tlaunch ros2_typed_launch_demo turtle_showcase_launch.py -h          # namespaces, params, timers, ...
tlaunch ros2_typed_launch_demo turtle_params_launch.py \
    --config ros2_typed_launch_demo:config/params_demo.yaml --show-config
                        # typed RosParams + untyped ros__parameters/ros__remappings,
                        # with which node pattern matched which node
tlaunch ros2_typed_launch_demo turtle_bringup_script.py --settle 2   # imperative script
python3 src/ros2_typed_launch_demo/launch/turtle_bringup_script.py --no-drive   # same file, direct
tlaunch ros2_typed_launch_demo flaky_demo_launch.py ; echo "exit: $?"   # death report, exit 1
tlaunch ros2_typed_launch_demo flaky_demo_launch.py --diagnostics    # + /diagnostics at 1 Hz
tlaunch <file_launch.py> --schema                                    # JSON Schema
```

`tlaunch` is a **real installed script**, on `PATH` once `install/setup.bash` is sourced —
that's the only thing needed, for this and for tab completion alike.
`ros2_typed_launch/CMakeLists.txt` installs it to both `bin/` — picked up by
`ament_cmake_core`'s default `path.sh` environment hook, the same mechanism that puts
`ros2` itself on `PATH` — and `lib/ros2_typed_launch/`, where `ros2 run ros2_typed_launch
tlaunch` looks. Not a shell function any more, so it composes normally (`timeout ...
tlaunch ...` works). **Tab completion** (package names, then that package's
`*_launch.py` files, like `ros2 launch`) is wired via `argcomplete`
(`python3-argcomplete`, apt): `cli.main()` calls `argcomplete.autocomplete()`
unconditionally but cheaply (a no-op unless actually invoked by a completion hook), and
`ros2_typed_launch/hook/tlaunch-completion.bash` — a real `ament_environment_hooks()`
hook, sourced automatically by `install/setup.bash`, exactly like the `path.sh` hook
above — runs `eval "$(register-python-argcomplete tlaunch)"`. Both degrade to nothing (no
completion, no crash) if `argcomplete` isn't installed. Python edits under `src/` take
effect immediately, live, without a rebuild (`--symlink-install` symlinks each installed
`.py` file directly to its source). A **new** launch/config file or package needs a
rebuild.

Headless / scripted runs (no display, no TTY):

```bash
export QT_QPA_PLATFORM=offscreen             # turtlesim without a display
timeout --foreground -s INT 20 tlaunch ... > log 2>&1
# or let the launch stop itself:  turtle_showcase_launch.py --timers.stop_after 9
```

- Use `--foreground` with `timeout`, or the SIGINT kills the calling shell. Put variable
  assignments on their own lines — `A && B && ( ... ) &` backgrounds the whole chain.
- A backgrounded launch ignores SIGINT; stop it with `kill -TERM`. `pgrep -f`/`pkill -f`
  match their own shell — use `[t]urtlesim`-style patterns.
- `turtle_teleop_key` dies with "Failed to get old console mode" without a TTY;
  expected in scripted runs, not a bug.
- Verify values reached nodes: `ros2 param get /<ns>/turtlesim background_r`;
  `ros2 topic echo /tf_static --once`; `ros2 node list`; `ros2 node info /<ns>/turtlesim`.
- `package.xml` comments must never contain `--` anywhere (not just as the closing
  delimiter) — invalid XML, fails with an unhelpful "not well-formed (invalid token)".
- A `ros__remappings` "from" side is the name **as the node uses it**, so a node in a
  namespace needs the relative spelling (`turtle1/cmd_vel`, not `/turtle1/cmd_vel`). ROS's
  rule, not ours; the wrong form silently remaps nothing.
- **A dying process does not change launch's exit code** — `LaunchService.run()` returns 0 even
  when a process died with rc=3 (verified). Only the `[ERROR] ... process has died` log line
  says so. See design-notes Q27.
- **`DiagnosticStatus.level` must be the bytes constant** (`DiagnosticStatus.ERROR` ==
  `b'\x02'`), never the int `2`: the int passes Python and then **aborts the process** in the C
  conversion (`Assertion 'PyBytes_Check(field)' failed`). `diagnostic_updater` and
  `diagnostic_aggregator` are not installed here; `diagnostic_msgs` is.
- **Testing a Ctrl-C needs care**: `kill -INT <pid>` from a non-interactive shell often does
  not reach the process (and `pkill -f`/a bad `$!` can kill the calling shell — exit 144). The
  reliable way is to have the program signal itself (`os.kill(os.getpid(), SIGINT)`) or
  `timeout --foreground -s INT`.
- **Launch will not execute an action twice** — `ExecuteLocal action '...': executed more than
  once`, and that exception takes the whole service down. So a session restart *rebuilds*: a stage
  is restartable only from a typed launch or a factory function, never from action objects.
- A script run with plain `python3` is `__main__` with no parent package, so it must use
  **absolute** imports (`from ros2_typed_launch_demo.launch.x_launch import ...`).
- `ExecuteProcess(name="x").name` is a list of `TextSubstitution`s, not a string; read a declared
  name through `session._plain_text`/`_declared_name`, not `str()`.
- A node is only matchable by a node pattern if it is built with `node(...)` **and given a
  `name`** — matching uses the declared `name`/`namespace`, so an enclosing
  `PushRosNamespace` or a runtime-chosen name is invisible to it.
- After a schema shape changes (a field moves into a nested model), update any
  `--config` YAML that used the old shape — `tlaunch` catches a stale key as "unknown
  key", but only when that file is actually used again.
- **`.venv/` is a leftover** from when Pydantic was pip-installed (v2); nothing reads it
  now. `schema.py` checks the Pydantic version at import time
  (`_check_pydantic_version`, requires v1.x) — if you ever see its `ImportError`, a stray
  pip-installed v2 is shadowing the system one somewhere on `PYTHONPATH`.

## Conventions and gotchas

- **All Pydantic access stays in `schema.py`**; `cli.py`/`config.py` see only
  `FieldSpec`/`Issue`/`SchemaError`. Keeps a future backend swap cheap.
- Keep `schema`, `config`, `cli`, `loader` importable without ROS (import `launch`/
  `ament_index_python` lazily, inside functions) so tests run without it.
- Values reach Pydantic as **strings** from the CLI and rely on lax coercion. Lists/dicts
  of models and other complex types have no flag — set them via `--config`.
- Pydantic silently drops unknown keys unless `extra="forbid"`; tlaunch checks YAML keys
  itself (`schema.unknown_keys`). A schema field named like a tlaunch option (`config`,
  `schema`, `debug`, ...) is a clean error.
- The launch target must come **before** the first schema flag.
- Two files sharing a `*_launch.py` stem raise an "ambiguous" error naming both dotted
  paths — use an explicit dotted or slash form to pick one.
- A file loaded by **path** (no package given) has no parent package, so it can't use
  relative imports — a launch that does `from .other_launch import ...` must be
  addressed by `<package> <file>`, not by path, even from inside its own `launch/` dir.
- An import cycle between launch files is caught by Python itself with a clear
  `ImportError` — no extra handling needed on our side.
- Prefer **composition over inheritance** when a parent config wants to reuse a child's
  config type (`params: ChildConfig = ChildConfig()` field), not subclassing — see
  `turtle_demo_launch.py`'s `LocalizationToggle`.
- Style: `from __future__ import annotations`, black-like formatting wrapped at roughly
  60–80 columns, type hints, short docstrings.
- Reference: stock launch CLI source is
  `/opt/ros/jazzy/lib/python3.12/site-packages/ros2launch/` (~500 lines; machinery is in
  `launch/`/`launch_ros/` beside it — `tlaunch` no longer depends on `ros2launch.api`
  itself). `ament_cmake_python`'s macros: `/opt/ros/jazzy/share/ament_cmake_python/cmake/`;
  `ament_cmake_pytest`'s: `/opt/ros/jazzy/share/ament_cmake_pytest/cmake/`.
