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
  its exported `generate_launch_description`, tagged `.typed_launch` by `@launch`.
  **Typed launch files only**; untyped ones use stock `ros2 launch`.
- Config layering: model defaults < `--config` files (repeatable, **deep-merged, later
  wins; lists are replaced, dict keys merge**) < CLI flags; validated *before* anything
  starts; unknown YAML keys are errors. A config is a path or `<package>:<path>` (looked
  up via the package's share dir via `ament_index_python`, independent of launch-file
  resolution above).
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
- Stock `launch`/`launch_ros` actions are used as-is (`PushRosNamespace`, `SetParameter`,
  `TimerAction`, event handlers, ...); see `turtle_showcase_launch.py`.
- **Pydantic 1** (system, apt/rosdep `python3-pydantic`), not 2 — launch authors import
  Pydantic directly in their own files, so a system install matters more than v2's
  stricter API. Both `package.xml`s declare `<exec_depend>python3-pydantic</exec_depend>`.
  Accepted trade-off: v1's YAML coercion is lenient (`tolerance: 1.7` for an `int`
  silently truncates to `1`) and `Optional[X]` with no default is silently optional, not
  required. Full comparison and rationale: `design-notes.md`.
- **Deferred (do not work on unless asked):** tmux (would use `~/projects/libtmux_easy`,
  a separate repo with known bugs — don't modify without asking); overrides (per-node
  params/remaps/prefixes); pluggable schema backends / darts `InputDict` (needs
  `python3-darts-core`, not installable here).
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
    `TypedLaunch`), `actions.py` (`include`). `test/` — pytest suite (80 tests).
- `ros2_typed_launch_demo/` — demo.
  - `package.xml`/`CMakeLists.txt`: installs the base package, *and* the top-level
    `launch/` directory as a subpackage nested under it (`ament_python_install_package(launch
    PACKAGE_DIR launch DESTINATION ${PYTHON_INSTALL_DIR}/${PROJECT_NAME})` — the trick
    behind the layout below).
  - `launch/`: `turtle_demo_launch.py`, `turtle_teleop_launch.py`,
    `localization_launch.py`, `turtle_showcase_launch.py` — ordinary submodules of
    `ros2_typed_launch_demo.launch`, importing each other directly; `__init__.py`
    re-exports all four.
  - `config/*.yaml` — example `--config` files, installed to the share dir (unaffected
    by where the Python launch files live).
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
