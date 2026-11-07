# lte_launch

Package `lte_launch` (was `ros2_typed_launch`; renamed 2026-11-07 to match `lte_launch_test`,
as the engine is libtmux_easy). Debian package via bloom: `ros-jazzy-lte-launch`. The command
is still `tlaunch`; the repo directory and GitHub repo still carry the old name.

Typed, schema-driven launch files for ROS 2 (Jazzy). A Pydantic model is the
public interface of a launch; the launch body is plain Python that starts what it
needs as it runs (`node()` starts the process there). Run with our own command,
**`tlaunch`**. **Branch `engine-lte`**: the engine is libtmux_easy (`Processes`), not
stock launch; the stock-engine code (monitor, tmux shim, dashboard, `@script`,
checks) is removed on this branch. `main` still has the old design.

Read first, and keep updated when state or decisions change. **`docs/`** documents what is
built (index `docs/README.md`): `docs/goals.md` (goals, principles, direction, proposals P1-P8),
`docs/design.md` (the current design), `docs/parity.md` (stock launch feature by feature),
`docs/comparison.md` (vs stock, simple_launch).
**`working/`** is for temporary plans and notes (index `working/README.md`):
`working/vision.md` (the synchronous engine on libtmux_easy: approaches A-D, demo, gate),
`working/PROGRESS.md` (done / uncommitted / next),
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
  (`examples.turtle_demo_launch`, never a `.py`) or on-disk-slash
  (`launch/turtle_demo_launch.py`) path also works, and is required if the full filename
  is ambiguous (two files sharing a name). `<path>` is for a standalone file not part of
  any package (e.g. `working/prototypes/`) — no parent package, so no relative imports
  (same limitation stock `ros2 launch` has for a bare file). A launch is identified by
  its exported `generate_launch_description`, tagged `.typed_target` by `@launch`.
  `@launch` takes the model from the only parameter's annotation
  (`def f(config: MyConfig)`), or `@launch(config=MyConfig)`; otherwise **the signature is
  the schema** (`def f(ns: str = "demo", rate: int = Field(5, ge=1))`, built by
  `schema.model_from_signature`, docstring = help, `f.config_type` = the model; see
  `turtle_teleop_launch.py`).
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
- **`node(...)`** builds the node's command line itself (`command.py`: ament lookup,
  `--ros-args`, a params file) and starts it at once, merging every matching section into that
  node's parameters/remappings. `params=` takes a **`RosParams`**
  model — a schema section that *is* a node's ROS parameters, declared once and delivered
  without restating. Typed beats untyped on a clash; a pattern matching no launched node is
  not an error (it may target a disabled node). Both are advisory and so
  **quiet unless `-d`**; `--strict` makes them errors and refuses the untyped lane.
  Wildcard *remappings* are ours: rcl does `/**` for parameters but rejects it for remap rules
  outright. The namespace comes from `with namespace()` scopes, so patterns see it.
- **`--show-config` prints the effective config as a config file** — the same YAML **every
  run writes** to `/tmp/tlaunch/<launch>-<pid>.yaml` and logs, so "what did that actually
  launch with?" is always answerable, and the answer is valid `--config` input (it
  round-trips). Provenance sits on every value, in the node sections exactly as in
  `launch_args` (`# default`, `# <file>`, `# cli`, `, shadowed by a typed field`), with
  matched nodes on the pattern (`# -> /ns/node`, or `# -> NO MATCH` — which is where you
  look for a typo'd pattern). The two lanes keep their own keys; sections sharing a pattern
  merge into one block, so there are never duplicate YAML keys. Renderer: `effective.py`.
- **The package builds with `ament_cmake`/`ament_cmake_python`**, not `ament_python` —
  needed to install `examples/launch/` as a nested subpackage. A user's own package lays
  out the same way: a top-level `launch/` installed as `<pkg>.launch`.
- **Launch files are real Python modules, named `*_launch.py`** (one dot — not
  `*.launch.py`, unimportable: a second dot before `.py` reads as package.submodule),
  in a directory CMake installs as a real **subpackage nested under the main package**
  (here `examples/launch/` → `lte_launch.examples.turtle_demo_launch`), not a copy
  to `share/launch/`.
  Consequence: **a same-package include is a plain (relative) `import`** —
  `from .other_launch import OtherConfig, other`; a child's config class is reused via
  composition (`params: OtherConfig = OtherConfig()` field, *not* subclassing — see
  `turtle_demo_launch.py`). `examples/launch/__init__.py` re-exports every launch's function and
  config, so `from lte_launch.examples import turtle_demo, TurtleDemoConfig`
  works without naming the submodule. `load_launch("pkg", "module")` (same
  `importlib.import_module` under the hood) is only for a launch chosen dynamically or
  from another package. `include(child, config)` runs the child's body right there (a
  function call), inside the parent's namespace/window/environment scopes.
- **The engine (`engine.py`) runs on a libtmux_easy `Processes` group** (`libtmux_easy.processes`,
  libtmux_easy branch `processes-core`): subprocesses by default, wrap-mode tmux panes with
  `--tmux`/`--tmux-dash` (a new session, or the current one inside tmux), none with
  `--dry-run`, or a test's own group. The body runs on the main thread; a wait blocks and
  fails on **any** death (an exit not in `exits`, default `(0,)`). After the body: the
  `[tlaunch]: up` ready line, supervise until Ctrl-C/`shutdown()`/a `required` process
  ending/a death, then **stop everything at once** (SIGINT, SIGTERM, SIGKILL; a second Ctrl-C
  kills). Exit 0 clean or interrupted, 1 a death or failed bringup, 2 a config error.
  `respawn=`, `required=`, `cwd=`, `env=`, `with environment()`, `--launch-prefix`.
  `stop_timeout=` per process (SIGINT's grace; run-wide `--stop-timeout`, libtmux_easy's
  per-process `sigint_timeout`). `on_shutdown(fn)`: LIFO, before anything is signalled,
  `fn(reason)` with `.kind`; errors/timeouts logged, exit 1; not in a dry run.
- **Composable nodes** (`composable.py`): `component(package, plugin, name=, into=)` picks
  placement from one value: None/`""` standalone (`executable=` as a node, else its own
  container), `"box"` our container (started on first use, or `container("box", kind=)`),
  `"/ns/box"` ours if started, else an existing one (`container_at`), or a `Container`.
  `load()` calls `_container/load_node` from the engine's rclpy node; a failed load is an
  error at that line (stock only logs it). Components in a container started elsewhere are
  **held** (`Run.held`): the run stays up, unloads them at teardown, and fails if the
  container leaves the graph. Dry run: each load is a `ros2 component load` command.
- **Lifecycle nodes** (`lifecycle.py`): `lifecycle_node(pkg, exe, name=, autostart=)` -> a
  handle with `transition(label)`, `set_state()`/`activate()`, `wait_for_state()`, `state`;
  `lifecycle_at(name)` for one started elsewhere. A failed transition is a `LaunchError` at its
  line. Teardown sends `shutdown` to ours before the SIGINT (`finalize=False` opts out). Dry
  run: each transition is a `ros2 lifecycle set` command.
- **lte_launch_test** runs a launch four ways (`examples/lte_test/`): `tlaunch` as one process
  gated on the ready line; `procs.build(target.build)`; `target.start(procs)` (any mode);
  `target.panes()` (dry-run plan).
- **Stock interop**: a typed launch includes a stock file with `include("pkg",
  "file.launch.py", **args)`, run as one `ros2 launch` process (`stock_arg` converts values).
  The other way (`stock.py`): a typed launch's `generate_launch_description()` returns a
  stock `LaunchDescription`, one argument per field plus `tlaunch_config`, validated at
  include time, that runs one `tlaunch` process; `lte_launch_install_stubs(...)` in
  CMake puts a stub in `share/<pkg>/launch/` so stock can find it. Design-notes Q17.
- **Parity with stock launch** is tracked feature by feature in `docs/parity.md` (next:
  scopes into a stock include, `sigkill_timeout`).
- **Pydantic 1** (system, apt/rosdep `python3-pydantic`), not 2 — launch authors import
  Pydantic directly in their own files, so a system install matters more than v2's
  stricter API. Both `package.xml`s declare `<exec_depend>python3-pydantic</exec_depend>`.
  Accepted trade-off: v1's YAML coercion is lenient (`tolerance: 1.7` for an `int`
  silently truncates to `1`) and `Optional[X]` with no default is silently optional, not
  required. Full comparison and rationale: `design-notes.md`.
- **Deferred (do not work on unless asked):** pluggable schema backends
  / darts `InputDict` (needs `python3-darts-core`, not installable here); **node-defined
  parameter schemas** (Q24 — a node shipping its schema so a launch can look it up instead
  of restating it; explicitly parked 2026-09-27, the untyped lane covers the need for now).
- **Interested, not started:** units (`--robot.radius "22 cm"`); plan in
  `schema-interface-and-input-dict.md`, decisions U1–U6 pending.
- License is not a concern for now.

## Layout

The **repo root is the `lte_launch` package** (`package.xml` here), one `ament_cmake`
package. It is built in a separate colcon workspace, **`~/ros2_ws`**, where
`src/lte_launch` is a symlink to this repo; `build/ install/ log/` live there.

- `package.xml`/`CMakeLists.txt`: `ament_python_install_package(${PROJECT_NAME})` installs
  the library; a second call installs `examples/launch/` as the subpackage
  `lte_launch.examples`. `scripts/tlaunch` goes to `bin/`
  and `lib/lte_launch/`. `cmake/lte_launch-extras.cmake` defines
  `lte_launch_install_stubs()` (exported, and used here for the examples).
  `ament_add_pytest_test` wires `test/` into `colcon test`.
- `lte_launch/`: `cli.py` (command), `loader.py` (`resolve()`/`load_launch`:
  find and import a launch, by package+bare-filename search, an explicit
  dotted/on-disk-slash path, or a standalone file path), `schema.py` (**all** Pydantic
  access), `config.py` (YAML, `<package>:<path>` refs, merge), `core.py` (`@launch`,
  `TypedLaunch` with `start`/`build`/`panes`/`plan`, `main()`), `api.py` (what a body
  calls: `node`, `process`, `include`, scopes, waits, `shutdown`, `share`), `engine.py`
  (`Run`, `Proc`, `execute`, tmux session), `command.py` (a node's argv), `composable.py`
  (`container`, `load`), `ros_config.py` (node patterns + the untyped lanes), `subst.py`
  (`$(...)` expansion), `lifecycle.py` (lifecycle nodes), `effective.py` (the effective
  config as a loadable YAML file),
  `stock.py` (a typed launch as a stock `LaunchDescription`).
- `scripts/` — `tlaunch`.
- `test/` — pytest suite (one test per behaviour, many assertions each — add assertions to
  the matching test rather than a new test per case). `test_examples.py` builds every example.
  Tests that start real processes are marked **`live`** (`test_supervision`, `test_waits`,
  `test_nodes`, `test_composable`, `test_include_live`, `test_cli_e2e`; helpers and the probe
  node in `_live.py`, fixtures `make_run`/`probe`/`spawn`/`uniq` in `conftest.py`); each skips
  without ROS, its packages or libtmux_easy. Use a unique `ROS_DOMAIN_ID` when other ROS runs share
  the machine.
- `examples/` — runnable examples. **`examples/README.md` lists every one with its
  commands** — keep it updated when an example is added or its flags change.
  - `launch/` → `lte_launch.examples`: `turtle_bringup_launch.py` (the synchronous
    style), `turtle_demo_launch.py`, `turtle_teleop_launch.py`, `localization_launch.py`,
    `turtle_params_launch.py` (typed `RosParams` + the untyped lanes), `composition_launch.py`
    (composable nodes), `lifecycle_launch.py`, `stock_include_launch.py` — ordinary submodules importing each other
    directly; `__init__.py` re-exports them all.
  - `config/*.yaml` → `share/lte_launch/examples/config/`
    (`--config lte_launch:examples/config/quiet.yaml`).
  - `stock_launch/` → `share/lte_launch/launch/`: `spawn_turtle.launch.py` (a
    traditional untyped file, included by `stock_include_launch.py`) and
    `stock_parent.launch.xml` (a stock file including a typed one).
  - `run.sh` — the tmux/dashboard demo (modes `dash`, `tmux`, `flaky`, `plain`;
    sources `$TLAUNCH_WS`, default `~/ros2_ws`).
  - `lte_test/` — the bringup under lte_launch_test (`run.sh -v [--tmux]`).
- `docs/` — documentation of what is built (`README.md`, `goals.md`, `design.md`, `parity.md`,
  `comparison.md`).
- `working/` — plans and notes (`PROGRESS.md`, `design-notes.md`, ...) + `prototypes/`
  (`nav_demo_launch.py`, `nav.yaml`: richer sample schema, run by path).

## Environment and commands

ROS 2 Jazzy at `/opt/ros/jazzy`, Python 3.12, Ubuntu 24.04. colcon,
pytest and Pydantic all come from the **system** (`python3-pydantic` v1.10, apt/rosdep) —
`ros2`/`tlaunch` run on `/usr/bin/python3`, which sees it by default. **Exception:
libtmux_easy** (the engine) is not installed for system Python here: until its `.deb` is,
`export PYTHONPATH=~/projects/libtmux_easy/src:$PYTHONPATH` (on branch `processes-core`).
Build and run from the **workspace** (`~/ros2_ws`): source ROS, then its install overlay. A
clean CMake configure picks `~/.local/bin/python3.10` (no catkin_pkg): pass
`--cmake-args -DPython3_EXECUTABLE=/usr/bin/python3`.

```bash
cd ~/ros2_ws                                                          # the workspace
source /opt/ros/jazzy/setup.bash && colcon build --symlink-install    # build
source install/setup.bash         # tlaunch on PATH + tab completion come from this alone

python3 -m pytest ~/projects/ros2_typed_launch/test -q               # tests (~45 s)
python3 -m pytest ~/projects/ros2_typed_launch/test -q -m "not live"  # quick: nothing starts (~1 s)
colcon test --packages-select lte_launch && colcon test-result

tlaunch lte_launch turtle_demo_launch.py -h              # schema-generated help
tlaunch lte_launch turtle_demo_launch.py \
    --config lte_launch:examples/config/blue_turtle.yaml \
    --config lte_launch:examples/config/quiet.yaml \
    --turtlesim.background_r 99 --show-config                        # layering + provenance, no launch
tlaunch lte_launch turtle_params_launch.py \
    --config lte_launch:examples/config/params_demo.yaml --show-config
                        # typed RosParams + untyped ros__parameters/ros__remappings,
                        # with which node pattern matched which node
tlaunch lte_launch turtle_bringup_launch.py              # start, wait, supervise
tlaunch lte_launch turtle_bringup_launch.py --dry-run    # every command line
tlaunch lte_launch turtle_bringup_launch.py --crash_after 3 ; echo "exit: $?"  # 1
tlaunch lte_launch turtle_bringup_launch.py --tmux --teleop   # a pane per process
tlaunch lte_launch composition_launch.py                 # composable nodes
tlaunch lte_launch turtle_params_launch.py --log-level debug --log-commands
examples/run.sh                   # the dashboard (opens tmux if needed)
examples/lte_test/run.sh -v       # under lte_launch_test
tlaunch <file_launch.py> --schema                                    # JSON Schema
```

`tlaunch` is a **real installed script**, on `PATH` once `install/setup.bash` is sourced —
that's the only thing needed, for this and for tab completion alike.
`CMakeLists.txt` installs it to both `bin/` — picked up by
`ament_cmake_core`'s default `path.sh` environment hook, the same mechanism that puts
`ros2` itself on `PATH` — and `lib/lte_launch/`, where `ros2 run lte_launch
tlaunch` looks. Not a shell function any more, so it composes normally (`timeout ...
tlaunch ...` works). **Tab completion** (package names, then that package's
`*_launch.py` files, like `ros2 launch`) is wired via `argcomplete`
(`python3-argcomplete`, apt): `cli.main()` calls `argcomplete.autocomplete()`
unconditionally but cheaply (a no-op unless actually invoked by a completion hook), and
`hook/tlaunch-completion.bash` — a real `ament_environment_hooks()`
hook, sourced automatically by `install/setup.bash`, exactly like the `path.sh` hook
above — runs `eval "$(register-python-argcomplete tlaunch)"`. Both degrade to nothing (no
completion, no crash) if `argcomplete` isn't installed. Python edits in this repo take
effect immediately, live, without a rebuild (`--symlink-install` symlinks each installed
`.py` file directly to its source). A **new** launch/config file or package needs a
rebuild.

Headless / scripted runs (no display, no TTY):

```bash
export QT_QPA_PLATFORM=offscreen             # turtlesim without a display
timeout --foreground -s INT 20 tlaunch ... > log 2>&1
# or let it end itself: a `required=True` process, or `shutdown()` in the body
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
- **A repeated `/` in a ROS name is fatal, not cosmetic**: a node handed `-r a:=//b` aborts at
  startup with `Couldn't parse remap rule ... Error: error not set` (rcl does not even set an
  error) and SIGABRT. Substituting an empty argument into a path produces exactly that, so
  `rc.tidy` collapses `//` and trailing `/` in remap names and patterns, and `rc.check_name`
  runs `rclpy.validate_topic_name` on both sides of every remap before launching. Parameter
  *values* are not names and are left alone.
- **Stock launch returns 0 when a process died** (`LaunchService.run()`, verified), which is
  one reason the engine is ours now. Only its `[ERROR] ... process has died` line says so —
  relevant to a stock include, which runs as one `ros2 launch` process.
- **Testing a Ctrl-C needs care**: `kill -INT <pid>` from a non-interactive shell often does
  not reach the process (and `pkill -f`/a bad `$!` can kill the calling shell — exit 144). The
  reliable way is to have the program signal itself (`os.kill(os.getpid(), SIGINT)`) or
  `timeout --foreground -s INT`.
- **`--tmux` needs libtmux_easy's `lte-run` on `PATH`** (`uv tool install --editable
  ~/projects/libtmux_easy`): it runs the command inside the pane as its real parent and reports
  the exact code/signal. Improvements found here go back into libtmux_easy, not around it.
- **A tmux pane's process is forked by the tmux server**, so it gets the *server's*
  environment; the engine creates its session with `env=os.environ` for that reason.
- `ros2 topic pub` exits **2** on SIGINT (a tool quirk); tests allow it in `stop_codes`.
- A node is only matchable by a node pattern if it is started with `node(...)` (or
  `load()`) **and given a `name`**; the namespace is the `namespace()` scope's.
- After a schema shape changes (a field moves into a nested model), update any
  `--config` YAML that used the old shape — `tlaunch` catches a stale key as "unknown
  key", but only when that file is actually used again.
- `schema.py` checks the Pydantic version at import time (`_check_pydantic_version`,
  requires v1.x) — if you ever see its `ImportError`, a stray pip-installed v2 is shadowing
  the system one somewhere on `PYTHONPATH`.

## Conventions and gotchas

- **All Pydantic access stays in `schema.py`**; `cli.py`/`config.py` see only
  `FieldSpec`/`Issue`/`SchemaError`. Keeps a future backend swap cheap.
- Keep `schema`, `config`, `cli`, `loader`, `api`, `engine` importable without ROS and
  without libtmux_easy (import `rclpy`/`launch`/`ament_index_python`/`libtmux_easy` lazily,
  inside functions) so tests run without them.
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
