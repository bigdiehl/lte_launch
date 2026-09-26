# Design notes

Merges what used to be three separate documents (`design-questions.md`, `progress.md`,
`findings-and-questions.md`) — they had grown into a blow-by-blow development journal with the
same facts repeated across all three. This keeps only what's still useful: open questions,
decisions with just enough rationale to not re-litigate them, and reference facts worth not
re-deriving. Last consolidated: 2026-09-24.

Status tags on open questions: **OPEN** (needs a decision) · **PARTLY** (part built, part open)
· **DEFERRED** (parked by you) · **PROPOSED** (I have a default; speak up if you disagree).

---

## Principles

1. The schema is the contract: a launch is a function from a validated config object to launch
   actions; CLI, YAML, JSON Schema and help are adapters around it.
2. One spelling everywhere: field path in code = flag = YAML key (`planner.max_iterations`).
3. Validate before anything starts; name the flag or key in every error.
4. Silent acceptance of a mistake is a bug (unknown flags, unknown YAML keys, name collisions).
5. Config values are real Python values, not launch-time substitutions; substitutions stay
   available *inside* stock actions for genuinely launch-time values.
6. Stock launch actions pass through untouched; tlaunch only adds `@launch`/`include`/`load_launch`.
7. All Pydantic access lives in `schema.py` — keeps a future backend swap (Q16) cheap.
8. Prefer a plain `import` over a dynamic lookup when the target is known at write time;
   `load_launch`/`resolve()` are for genuinely dynamic or cross-package addressing.

## Decided

| Topic | Decision |
|---|---|
| Front end | Own command `tlaunch`, not a hook into `ros2 launch`; normal argparse syntax (`--a.b value`), not `name:=value` |
| Flags | Snake_case, exactly the dotted field path; `--x`/`--no-x` for bools; `--tag a --tag b` for lists; `--remap k=v` for dicts |
| Launch identity | A file's `@launch`-tagged, exported `generate_launch_description` — same handle stock `ros2 launch` uses |
| Typed-only | `tlaunch` runs typed launch files only; untyped ones use stock `ros2 launch`; a typed file run under stock launch errors with a pointer to `tlaunch` |
| Addressing | `tlaunch <package> <file>` — `<file>` the **full filename, `.py` required** (the bare stem alone is rejected with a hint), found by searching the package for a `*_launch.py` with that name (like `ros2 launch <pkg> <file>` searches `share/`); an explicit dotted (never `.py`) or on-disk-slash (`.py` required) path also works, needed only to disambiguate. `tlaunch <path>` for a standalone file (no parent package, so no relative imports). Historical: this was a share-dir walk, then a required dotted submodule path, then a bare stem with `.py` optional, before landing here — see `loader.py`'s docstring for the mechanism, not this doc, for future changes |
| Launch files | Named `*_launch.py` (not `*.launch.py` — unimportable), live in a top-level `launch/` directory (sibling to `package.xml`), installed by CMake as a real subpackage nested under the main package. Same-package include = plain `import`; `launch/__init__.py` re-exports every launch |
| Build system | Both packages use `ament_cmake` + `ament_cmake_python` (not `ament_python`) — more flexible for ROS, and the only way to get `launch/` installed as a nested subpackage while keeping it at a top-level, ROS-conventional path |
| Config layering | model defaults < `--config` files (repeatable, deep-merged, **later wins**, **lists replaced**, dict keys merged) < CLI flags. A config is a path or `<package>:<path>` |
| Pydantic | **v1** (system, apt/rosdep `python3-pydantic`), not v2 (was a pip-managed `.venv`). Both `package.xml`s declare `<exec_depend>python3-pydantic</exec_depend>`. No `PYTHONPATH` changes needed anywhere — `/usr/bin/python3` sees it by default. Trade-off accepted: v1's YAML coercion is lenient (`tolerance: 1.7` for an `int` silently truncates) and `Optional[X]` with no default is silently optional, not required. Full comparison: `#pydantic-1-vs-2` below |
| `include` | `include(child, config)` builds the child eagerly, returns a scoped `GroupAction`; parent namespace/params/remaps/env are inherited |
| Own schema interface / `InputDict` | Deferred — see `schema-interface-and-input-dict.md` |
| Overrides, tmux | Deferred — see `#deferred-designs` below |
| License | Not a concern for now |

## Open questions

### Collections on the CLI (Q5) — PARTLY

Built: `list[scalar]` → repeatable flag; `dict[str, T]` → repeatable `key=value` (split on
first `=`; untyped values parsed as YAML); list replaces / dict merges across layers.

Still open: an empty list or a dict-key removal can't be expressed on the CLI (YAML-only, for
now); a `--set path=<yaml>` escape hatch (not planned — needs a path grammar, duplicates
existing flags); fixed-size vectors (`--position 1 2 3`) vs. repeated flags.

### Schema coverage (Q23) — OPEN, small

Not handled by the walker: `Optional[Model]` (no flag), unions, fixed-size tuples, `list[Model]`/
`dict[str, Model]` (config-file only), nested collections. Leaning: add `Optional[Model]` and
fixed-size tuples next (common in robotics configs — poses, covariances); leave the rest to YAML.

### Per-field CLI hints (Q14) — OPEN, small

Support only *hide* and *custom help*, via `Field(json_schema_extra=...)` or an `Annotated`
marker, if/when a real launch needs one. No known need yet.

### tlaunch's own options (Q18) — OPEN

Reserved names: any tlaunch option added later can collide with a launch field of the same
name. Proposal: **hyphenate every new option** (`--dump-config`); a Python identifier can't
contain `-`, so it can never collide with a field. Keep the reserved list in `cli.RESERVED`.

Parity with stock `ros2 launch`: missing `--print` (dump the built actions) and
`-a/--show-all-subprocesses-output`; not planning `-s/--show-args` (superseded by `-h`).

### Config sources (Q6) — PARTLY

Still open: an env-var layer (leaning: no, in v1); resolving relative `Path` fields against
the YAML file's directory, and accepting `<package>:<path>` in `Path` fields too (leaning:
do both); `--dump-config` (effective YAML with provenance comments, leaning: build it);
detecting duplicate YAML keys (leaning: yes, needs a custom loader); resetting a value once
YAML set it (leaning: not supported).

### Include/loading mechanics (Q22) — PARTLY

Open: errors inside a launch function print a raw Python traceback (right for author bugs,
noisy for user mistakes) — leaning: keep the traceback, but print a one-line context first
("while building launch 'turtle_demo'"), full trace behind `-d`. A child's config is only
settable through whatever the parent exposes (deliberate; addressability is Q7).

Closed, no work needed: an import cycle between launch files is already a clear Python
`ImportError`, caught and wrapped by `loader.py`; no cycle-detection code needed (verified,
`test_loader.py`).

### Interop with stock launch files (Q17) — OPEN

Typed → stock (a typed launch's actions included by a stock `IncludeLaunchDescription`) is
untested but should work — actions are actions. Stock → typed is not possible (typed files
reject stock `ros2 launch` outright). Leaning: accept the one-way boundary; revisit only if a
concrete need for calling into a typed launch from an existing stock tree comes up.

### Launch-time values (Q19) — PROPOSED

No substitution helpers of our own; stock substitutions (`FindPackageShare`, ...) still work
inside stock actions. For anything knowable at tlaunch time, plain Python in the launch
function is simpler (`get_package_share_directory("pkg")`). Revisit if authors keep reaching
for `FindPackageShare` — a `package_file("pkg", "path")` helper would be cheap to add then.

### `include` scope isolation (Q7) — DEFERRED

Whether a child scope should carry its own namespace/env/remap isolation, and whether a
child's config should be directly addressable from YAML/CLI. Today: a plain scoped
`GroupAction`; the parent sets namespace etc. with stock actions.

### Overrides — params/remaps/prefixes from config (Q8) — DEFERRED

Original motivation: generic launches with no hardcoded remaps. Design sketch:
`#deferred-designs` below. What already helps: `dict` CLI flags, `SetParameter`/`SetRemap`/
`SetEnvironmentVariable` driven by config, `--launch-prefix[-filter]`.

### tmux (T) — DEFERRED

`~/projects/libtmux_easy` reviewed. Design sketch: `#deferred-designs` below.

### Packaging and invocation (Q12) — PARTLY

**Done:** `tlaunch` is a real script, on `PATH` the moment `install/setup.bash` is sourced —
nothing else needed, no wrapper script of our own. Mechanism: `ament_cmake_core` registers a
default `path.sh` environment hook for every package that prepends `<prefix>/bin` to `PATH` if
that directory exists (the same hook that puts `ros2` itself on `PATH`); installing
`scripts/tlaunch` to `bin/` (in addition to `lib/ros2_typed_launch/`, kept for `ros2 run
ros2_typed_launch tlaunch`) triggers it. Side benefit: a real script composes normally where a
shell function couldn't (`timeout ... tlaunch ...` works directly).

**Done: tab completion**, matching `ros2 launch`'s own UX (package names, then that package's
launch files) — via `argcomplete` (`python3-argcomplete`, apt, the same library `ros2cli`
uses), also wired to fire from `install/setup.bash` alone: `ros2_typed_launch/hook/
tlaunch-completion.bash`, registered via `ament_environment_hooks()` exactly like the `path.sh`
hook above, runs `eval "$(register-python-argcomplete tlaunch)"`. `cli.py` builds a small
parser used only by `argcomplete` (two positionals, `package` and `file`, each with a completer
function, plus tlaunch's own flags so completion can skip over e.g. `--config FILE`) — needed
because the real target parsing (`split_target`) is a manual `argv` scan with no static shape
for `argcomplete` to introspect. The file completer reuses `loader`'s own package-search logic
(`launch_file_names`), so it's never out of sync with what `tlaunch <package> <file>` actually
accepts. Both the hook and the `argcomplete` import in `cli.py` are optional/defensive
(matching the pattern `ros2launch` itself uses) — no completion, no crash, if
`python3-argcomplete` isn't installed. Verified in a clean shell sourcing only ROS +
`install/setup.bash`: `complete -p tlaunch` shows it registered, and a real completion trigger
returns the right package/file candidates.

Still open: a `ros2 tlaunch` entry via a new `ros2cli.command` (a new name, so no path-order
fragility, unlike replacing `launch` itself) — not started, lower priority now that plain
`tlaunch` is already on `PATH`.

### Supported distros/platforms (Q20) — OPEN

Only Jazzy / Ubuntu 24.04 / Python 3.12 has been run. Nothing depends on 3.11+-only syntax.
Leaning: declare Jazzy-only until told otherwise.

### Test strategy beyond unit tests (Q21) — PROPOSED

Have: 80 unit tests (`pytest`/`colcon test`), most needing no ROS; real launches checked by
hand (`ros2 param get`, `/tf_static`, node lists, exit codes). Proposed next step: one
subprocess-based integration test running the installed `tlaunch` on the showcase launch,
skipped unless ROS + turtlesim are present. (`launch_testing` was considered and rejected — a
pip-installed pytest once broke its plugin; see the Pydantic-venv gotcha history, now moot
since there's no project venv to pollute it.)

**Suggested order when picking this back up:** Q20 (gates how much compatibility to build) →
Q18 + Q6 polish + Q5 remainder (small, high-value) → Q23, Q14 → Q12, Q21 → Q15 units → Q16 if
`python3-darts-core` becomes installable.

---

## Reference

### Pydantic 1 vs 2

Compared directly (a probe script run under both versions) when deciding Q10.

| | Pydantic 1.10.14 (system) | Pydantic 2.13.5 (was: venv) |
|---|---|---|
| Installed by | apt/rosdep (`python3-pydantic`) | pip only |
| Field introspection | `__fields__`; `ModelField.outer_type_/required/field_info.{description,ge,le,gt,lt,min_length,max_length}` | `model_fields`; `FieldInfo.annotation/is_required()/description/metadata` |
| `Optional[int]`, no default | silently optional (defaults to `None`) | required |
| YAML coercion | lenient: `1.7`→int `1`, `5`→str `'5'` (silent) | strict: all four are errors |
| Validate | `.parse_obj(dict)` | `.model_validate(dict)` |
| Errors | `loc`, `msg`, `type` (no `input` key; missing = `"value_error.missing"`) | adds `input`, `url`; missing = `"missing"` |
| JSON Schema | `.schema()`, `definitions` | `.model_json_schema()`, `$defs` |
| Gotcha | `Field(ge=/le=/...)` synthesizes a `Constrained*Value` subclass of the plain type (e.g. `ConstrainedFloatValue < float`) — a type-equality check like `t in {float: ...}` misses it; use `issubclass` | none |

### Stock `ros2 launch` (Jazzy: `ros2launch` 0.26.12, `launch` 3.4.11)

Source: `/opt/ros/jazzy/lib/python3.12/site-packages/ros2launch/` (~500 lines; the real
machinery is in `launch`/`launch_ros`). Facts that shaped our design:

- Launch args are `name:=value` strings only — no types, no structure, no validation.
  Undeclared/typo'd args are **silently accepted**.
- `-h` is plain argparse and never loads the launch file (this is why `tlaunch` needs to own
  the entry point — dynamic, schema-derived help requires knowing the file before parsing).
- File lookup: an existing-file argument is used directly; otherwise the second argument is
  looked up by walking `<package share dir>` for a file of that exact name.
- `--launch-prefix` is a launch configuration that `ExecuteLocal` uses as its default `prefix`
  (an explicit `prefix=` wins); the prefix **replaces** the command (process shows as
  `echo-1`, not `echo` with a prefix).
- `ros2launch.option` extensions can add an argparse group and hooks (`prestart`/`prelaunch`/
  `postlaunch`), but run before the launch file is known and don't auto-apply parsed values —
  viable but hacky (this is "option A" we didn't take). Replacing the `launch` command via a
  duplicate `ros2cli.command` entry ("option B") only wins if our dist sorts after
  `ros2launch` on the path — fragile, rejected. `tlaunch` as a wholly separate command
  ("option C") is what we built: full control, no ordering hazard, but not `ros2 launch` itself.

### Schema → CLI field mapping

| Schema | CLI |
|---|---|
| nested model | no flag; recursed into as its own argument group (`path [ModelName]`, docstring as group text) |
| `str`/`int`/`float`/`Path` | `--a.b VALUE` (stays a string through argparse; Pydantic coerces) |
| `bool` | `--a.b`/`--no-a.b` (`BooleanOptionalAction`) |
| `Literal`/`Enum` | `--a.b {x\|y}` (argparse `choices`) |
| `Field(ge=/le=/gt=/lt=)` | shown in help; enforced by Pydantic, not argparse |
| default / no default | `[default: ...]` / `[required]`; not passed as an argparse default — see below |
| `list[scalar]` | repeatable `--a.b x --a.b y` |
| `dict[str, T]` | repeatable `--a.b k=v` |

The load-bearing trick: every flag is registered with `default=argparse.SUPPRESS`, so the
parsed namespace holds only flags the user actually typed. That's what lets YAML sit under
CLI without a flag the user didn't type clobbering it.

### Deferred designs

**Overrides (Q8).** Every node created through our `Node` wrapper gets a stable logical id
(default: executable/name). A reserved `overrides` section, keyed by node id, gets injected
into every launch schema and applied after the author's defaults:

```yaml
overrides:
  planner:
    remap: {/scan: /lidar/scan}
    params: {tolerance: 0.3}
    prefix: "gdb -ex run --args"
```

Launch authors still expose their own topic names as typed config; the overlay only covers
what the author didn't anticipate. Open: id namespacing across `include`, CLI syntax for keys
containing `/`/`.`, a dry-run way to enumerate a launch's override surface.

**tmux.** `~/projects/libtmux_easy` (~600 lines over `libtmux`) is a stateful, in-process
session/window/pane builder (`TmuxSession.create(name)`, `new_pane(name, cmd)`, `send_keys`).
It types commands into pane shells, so tmux — not launch — would own the processes, meaning
launch loses exit codes/events for anything routed through a pane. `TmuxSession.create()`
kills an existing same-named session by default. Two known bugs in the lib itself (unrelated
repo, not touched): `get_session_pids` calls an unimported `get_child_pids`; a stray
`breakpoint()` in `pids.get_child_pids`.

Design fork when resumed: **(i)** tmux owns the process (native to the lib; simple; launch
sees no exit codes/events for these nodes) vs. **(ii)** launch owns it, with a small per-pane
shim as the node's `--launch-prefix` that runs the real command in a pane and forwards its
exit code/signals back (keeps launch's lifecycle features; more moving parts). Neither
implemented. Lean towards (i) if the goal is "tidy panes with interactive TTYs" (the
motivating case is `turtle_teleop_key`, which needs a real TTY), (ii) if lifecycle fidelity
matters more.
