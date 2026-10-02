# Design notes

Merges what used to be three separate documents (`design-questions.md`, `progress.md`,
`findings-and-questions.md`) — they had grown into a blow-by-blow development journal with the
same facts repeated across all three. This keeps only what's still useful: open questions,
decisions with just enough rationale to not re-litigate them, and reference facts worth not
re-deriving. Last consolidated: 2026-09-24; Q8/Q24/Q25 (config-driven node params, remappings,
substitutions, node schemas) explored and written up 2026-09-27.

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
| Launch identity | A file's exported `generate_launch_description`, tagged `.typed_target` by `@launch`/`@script` — same handle stock `ros2 launch` uses |
| Typed-only | `tlaunch` runs typed launch files only; untyped ones use stock `ros2 launch`; a typed file run under stock launch errors with a pointer to `tlaunch` |
| Addressing | `tlaunch <package> <file>` — `<file>` the **full filename, `.py` required** (the bare stem alone is rejected with a hint), found by searching the package for a `*_launch.py` with that name (like `ros2 launch <pkg> <file>` searches `share/`); an explicit dotted (never `.py`) or on-disk-slash (`.py` required) path also works, needed only to disambiguate. `tlaunch <path>` for a standalone file (no parent package, so no relative imports). Historical: this was a share-dir walk, then a required dotted submodule path, then a bare stem with `.py` optional, before landing here — see `loader.py`'s docstring for the mechanism, not this doc, for future changes |
| Launch files | Named `*_launch.py` (not `*.launch.py` — unimportable), live in a top-level `launch/` directory (sibling to `package.xml`), installed by CMake as a real subpackage nested under the main package. Same-package include = plain `import`; `launch/__init__.py` re-exports every launch |
| Build system | Both packages use `ament_cmake` + `ament_cmake_python` (not `ament_python`) — more flexible for ROS, and the only way to get `launch/` installed as a nested subpackage while keeping it at a top-level, ROS-conventional path |
| Config layering | model defaults < `--config` files (repeatable, deep-merged, **later wins**, **lists replaced**, dict keys merged) < CLI flags. A config is a path or `<package>:<path>` |
| Pydantic | **v1** (system, apt/rosdep `python3-pydantic`), not v2 (was a pip-managed `.venv`). Both `package.xml`s declare `<exec_depend>python3-pydantic</exec_depend>`. No `PYTHONPATH` changes needed anywhere — `/usr/bin/python3` sees it by default. Trade-off accepted: v1's YAML coercion is lenient (`tolerance: 1.7` for an `int` silently truncates) and `Optional[X]` with no default is silently optional, not required. Full comparison: `#pydantic-1-vs-2` below |
| `include` | `include(child, config)` builds the child eagerly, returns a scoped `GroupAction`; parent namespace/params/remaps/env are inherited |
| Own schema interface / `InputDict` | Deferred — see `schema-interface-and-input-dict.md` |
| Node params/remaps from config | ROS-native node patterns (`/**`, `/**/node`, `/ns/*`), **not** invented per-node ids; wildcards **interpreted by us** for both lanes, because rcl implements them for params but refuses them for remappings. Keys `ros__parameters` / `ros__remappings`, at the file's top level; launch schema under a **required** `launch_args:`. Reaches nodes through **our `Node` wrapper**. Untyped by default; typable via a `RosParams` Pydantic type; `--strict` refuses the untyped lane. Fully decided, not built — Q8 |
| `$(...)` in config files | Reuse `launch.frontend.parse_substitution` (the stock lark grammar), expanded **eagerly per string leaf** at load time. Not our own parser, not `ParameterFile`. Built — Q25 |
| Node-defined schemas | **Optional**: when a node ships a schema, `tlaunch -h` shows its parameters; when it doesn't, the untyped lane carries them. MoveIt's `generate_parameter_library` spec is *not installed* by its CMake macro, so discovery needs our own resource — Q24 |
| Process monitoring | Every run records the processes it starts and **reports what died**: an error line naming the node, an end-of-run summary, and a **non-zero exit code** (launch itself returns 0 for a run whose node died). `/diagnostics` publishing is opt-in via `--diagnostics`, since it needs an rclpy node in the launch. Direct children only. Built — Q27 |
| Imperative launching | `@script(config=...)` + a `Session`: the service owns the main thread, the script runs on a worker. `session.start()` returns a **stage handle** you drill into (`stage["planner"]`); Ctrl-C makes the current wait raise so the script's `finally` runs; the session always stops what it started; runnable as `tlaunch <pkg> <file>` **and** `python3 <file>`. Built — Q26 |
| tmux | Deferred — see `#deferred-designs` below |
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
do both — and `$(dirname)` from Q25 covers the first if we push the config file's directory
into the substitution context); `--dump-config` (effective YAML with provenance comments,
leaning: build it); detecting duplicate YAML keys (leaning: yes, needs a custom loader);
resetting a value once YAML set it (leaning: not supported).

Worth stealing from `load_config.py` (the reference implementation from your other project,
`ros2_launch/load_config.py`): **directory loading** (point `--config` at a directory, load
every `*.yaml` in it, optionally recursive) and **colon-delimited path lists** in one
argument. Both are additive; our layering semantics already match its `merge_dicts` (and we
additionally track provenance and reject unknown keys — it only logs a debug line on
override). Two bugs in that file, if it is still in use elsewhere: `raise ValueError(
"Override conflict at " ".".join(...))` is missing a `+` (implicit string concatenation, so
the path ends up joined *by* the message), and `merge_dicts(a, b, path=[])` has a mutable
default argument.

### Include/loading mechanics (Q22) — PARTLY

Open: errors inside a launch function print a raw Python traceback (right for author bugs,
noisy for user mistakes) — leaning: keep the traceback, but print a one-line context first
("while building launch 'turtle_demo'"), full trace behind `-d`. A child's config is only
settable through whatever the parent exposes (deliberate; addressability is Q7).

Closed, no work needed: an import cycle between launch files is already a clear Python
`ImportError`, caught and wrapped by `loader.py`; no cycle-detection code needed (verified,
`test_loader.py`).

### Interop with stock launch files (Q17) — PARTLY

**Built:** typed → stock, the common case (a typed launch includes a third-party package's
plain launch file). `actions.stock_arg`/`stock_args` convert typed config values to the flat
strings stock launch args are always made of (bool → `"true"`/`"false"`, `Enum` → its value,
everything else `str()`'d; collections/models rejected with a clear error — no agreed string
form for those). The author still calls stock `IncludeLaunchDescription` +
`PythonLaunchDescriptionSource` themselves (principle 6: stock actions pass through
untouched); `stock_args` only replaces manual `str(config.x)` calls at each site. Demo:
`stock_include_launch.py` includes `stock_launch/spawn_turtle.launch.py` (a genuine untyped
file, installed to `share/` like any stock package would, found via
`get_package_share_directory` — not part of the Python package, so tlaunch's own search never
sees it). Verified with a real launch: typed config → `stock_args` → stock
`LaunchConfiguration` → node parameter, end to end.

Still open: stock (a stock launch including one of ours, or a stock file being run under
`tlaunch`) — not possible today; typed files reject stock `ros2 launch` outright, and nothing
generates stock `DeclareLaunchArgument`s from a schema. Leaning: accept the boundary; revisit
only if a concrete need for calling into a typed launch from an existing stock tree comes up.

### Launch-time values (Q19) — PROPOSED, revisited by Q25

Unchanged for launch *files*: no substitution helpers of our own; stock substitutions
(`FindPackageShare`, ...) still work inside stock actions, and for anything knowable at
tlaunch time plain Python in the launch function is simpler
(`get_package_share_directory("pkg")`).

New position for config *files*: `$(...)` in a YAML value is expanded **eagerly, at config
load time, to a real Python string** — see Q25. That is not a retreat from principle 5; it is
the same rule (config values are real values, not deferred `Substitution` objects). The
distinction that matters: a launch file has Python available and needs no substitution syntax;
a YAML file has no Python, so it needs *some* escape hatch for package paths and env vars, and
reusing ROS's own spelling beats inventing one.

### `include` scope isolation (Q7) — DEFERRED

Whether a child scope should carry its own namespace/env/remap isolation, and whether a
child's config should be directly addressable from YAML/CLI. Today: a plain scoped
`GroupAction`; the parent sets namespace etc. with stock actions.

### Node params and remappings from config (Q8) — BUILT (2026-09-27/28)

Replaces the old "overrides" sketch, which keyed off per-node ids we would have invented. ROS's
own node-pattern spelling means an existing ROS parameter file is nearly a tlaunch config already.

```yaml
launch_args:                      # the typed schema, validated
  turtlesim: {background_r: 99}
/**:                              # node patterns: * is one token, ** is zero or more
  ros__parameters: {use_sim_time: true}
/my_ns/planner:
  ros__parameters: {tolerance: 0.3}
  ros__remappings: {turtle1/cmd_vel: /joy_vel}
```

Params and remappings are **untyped, schema-free lanes** to the matching nodes — the deliberate
answer to Q24's duplication problem, not a gap. They *can* be typed when wanted, via a `RosParams`
model handed to `node(params=...)`, which makes Q24's tiers 1 and 2 one mechanism seen from two
ends. Delivery is our `node()` wrapper; plain stock `Node` still works, it just collects nothing.
`--ros-param '[PATTERN:]name=value'` and `--ros-remap '[PATTERN:]from=to'` are the CLI equivalents
(hyphenated per Q18, so neither can collide with a field name); `--strict` refuses the untyped
lane outright.

**Why we match the wildcards ourselves** — verified here, both directions:

| | `/**`, `/**/node` |
|---|---|
| Parameters, via `--params-file` | rcl matches them itself |
| Remappings, via `-r /**:from:=to` | **rcl refuses**: `Wildcard '**' is not implemented`; only an exact node-name prefix works |

Wildcard remapping therefore does not exist in ROS 2 at all, so doing it ourselves is a
capability rather than a reimplementation. And because we know every node we launch, a pattern
matching none of them can be reported at all — stock ROS ignores a typo'd section in silence
(verified: the node starts, zero warnings).

Settled decisions:

| # | Decision |
|---|---|
| 1 | `launch_args:` is **required**; a bare top-level schema key is an error naming the fix. Breaking change, migration done |
| 2 | Node patterns sit at the **top level**, so a plain ROS parameter file is valid as-is. A top-level key is a pattern iff it holds `/` or `*`, or its value has `ros__parameters`/`ros__remappings`; anything else stays an unknown-key error |
| 3 | A pattern matching no launched node **does not fail** — it may target a node behind a disabled toggle, or a stock `Node`. Quiet unless `-d`; `--show-config` shows `NO MATCH` instead. Whether the warning is wanted at all is still open |
| 4 | Delivery is a **`Node` wrapper**, not a post-pass over the built tree — that would reach stock `Node`s too, but `Node` keeps its parameters private and it would apply config nobody opted into |
| 5 | A typed field and an untyped section setting the same parameter: **typed wins**; the advisory is `-d`-only, and the value is marked in `--show-config` |

3 and 5 proceed quietly, softer than principle 4's usual line, because both have legitimate uses
and neither changes the effective values. Principle 4 is served instead by `--show-config` being
fully inspectable. `--strict` is the single switch that turns both into errors.

**Effective config output.** `--show-config` renders the effective config **as a config file** —
the same YAML every run writes to `/tmp/tlaunch/<launch>-<pid>.yaml` and logs, so "what did that
run actually use?" is always answerable, and the answer is valid `--config` input (round-trip
verified). The two lanes keep their own keys; sections sharing a pattern merge into one block,
which per-value provenance makes lossless and which keeps duplicate keys out of a document Q6's
future duplicate-key check would otherwise reject. Provenance, matched nodes and shadowed values
ride along as YAML comments. Renderer: `effective.py`. Supersedes Q6's `--dump-config` item.

**Not covered yet**, roughly in priority order: an enclosing `PushRosNamespace` is invisible to
matching (nodes match on their *declared* `name`/`namespace`; the launch-time namespace stack
would need tracking in our own group/include helpers), so `--show-config` under-reports for a
launch that names nodes dynamically; a node with no `name=` can only be matched by `/**`, since
rcl picks the executable's default at startup; nodes inside an *included stock* launch file are
unreachable for remappings, though params can still be handed through as a file for rcl to match;
`ComposableNode` has no wrapper.

**Gotcha, learned the hard way:** a remapping's "from" side is the name **as the node uses it**,
so a node in a namespace needs the relative spelling (`turtle1/cmd_vel`, not `/turtle1/cmd_vel`).
ROS's rule, not ours — but the demo config got it wrong first and silently remapped nothing.

### `$(...)` substitutions in config files (Q25) — BUILT (2026-09-27)

`$(find-pkg-share pkg)`, `$(find-pkg-prefix pkg)`, `$(env VAR [default])`, `$(command ...)`,
`$(eval ...)` and the rest of the vocabulary in [the roslaunch XML
article](https://design.ros2.org/articles/roslaunch_xml.html) work in any config value.

**No parser of our own, and not `ParameterFile`.** `launch.frontend.parse_substitution` is the
real lark grammar behind the XML/YAML launch frontends, and it works standalone against a bare
`LaunchContext()` (evidence in the Reference section below) — so the vocabulary is ROS's with no
grammar of ours to maintain, and `lark` is already a hard dependency of `launch`. Two gotchas: the
keyword is **`env`**, not `env-var`; and `find-pkg-*` only register once
**`launch_ros.substitutions` has been imported**.

Applied **per string leaf of the parsed YAML**, not to the file text the way
`ParameterFile(allow_substs=True)` does (it runs the parser over the entire raw file into a temp
file — which is why `load_config.py` needs `atexit` cleanup). Per-leaf means no temp files, keys
and structure untouched, and an error naming the offending key rather than `"The substituted
parameter file is not a valid yaml file"`.

Open: expanding in keys too (leaning no); `$(dirname)` meaning the config file's directory (cheap
— push it into the context as a local, and it settles half of Q6). `$(command ...)`/`$(eval ...)`
run arbitrary code from a config file and are deliberately allowed: same trust level as the launch
file itself.

### Node parameter schemas and introspection (Q24) — DEFERRED (parked 2026-09-27)

The complaint (2026-09-27): a launch schema that must restate every parameter of every node is
unwieldy for param-heavy nodes, and with stock ROS you often have to **read a node's source to
find out what its parameters even are**. Ideally a node publishes its own configuration schema,
queryably.

**Key finding: that schema already exists and is already queryable — just only at runtime, from
a started node.** `ros2 param describe /turtlesim background_r` returns type `integer`,
description "Red channel of the background color", min 0, max 255, step 1. `ParameterDescriptor`
carries `name, type, description, additional_constraints, read_only, dynamic_typing,
floating_point_range, integer_range` — near 1:1 with our `FieldSpec`. The information is not
missing from ROS; it is trapped behind a running node and never surfaced statically.

Proposed three-tier split, rather than one schema trying to be everything:

| Tier | For | Mechanism | Cost |
|---|---|---|---|
| 1 | any node, today | untyped `ros__parameters` lane (Q8) — node params never enter the typed schema | no validation, no `-h` for them |
| 2 | nodes you own | the node's package ships the Pydantic model as its parameter contract; the **node declares its params from the model** (`declare_from_model(node, Model)`, `FieldSpec` → `ParameterDescriptor`), and launch files *compose* the same model instead of restating it | needs a node-side library, i.e. scope beyond launch |
| 3 | third-party nodes | `tlaunch params-scaffold <pkg> <exe>`: start it, call `describe_parameters`, emit a model or commented param YAML to check in; plus a **static ament index resource** (`share/ament_index/resource_index/tlaunch_schemas/<pkg>`) so `tlaunch params <pkg>` answers without running anything | scaffolding is runtime-dependent by nature; the index resource needs adoption |

Tier 2 is what actually answers "a node should define its own schema": generate the runtime
`ParameterDescriptor`s *from* the model, and `ros2 param describe` and `tlaunch -h` agree by
construction. The ament index is a fair mechanism for tier 3 — 21 resource types already exist
on this system (`rclcpp_components`, the pluginlib ones), so defining one is conventional, not
exotic.

**Decided 2026-09-27:** a node defining its own schema is the direction, but **optional** — when
a schema is available, `tlaunch -h` shows that node's parameters (looked up, not retyped); when
it isn't, the untyped `ros__parameters` lane carries them as before.

#### How MoveIt does it — `generate_parameter_library`

Probed from the real 0.7.6 debs (downloaded and extracted, not installed — nothing on this
machine uses it; MoveIt itself is not installed here).

- A **per-node YAML spec checked into the source tree** → codegen at build time, via three
  entry points: `generate_parameter_library_cpp` (a header), `generate_parameter_library_python`
  (an importable module), `generate_parameter_library_markdown` (docs).
- Spec shape: `node_name: { param: { type, default_value, description, read_only, validation:
  {...} } }`, with `__map_<key>` for nested maps.
- Validator vocabulary: `lt, gt, lt_eq, gt_eq, bounds, not_empty, one_of, unique, subset_of,
  fixed_size, size_gt, size_lt, element_bounds, lower_element_bounds, upper_element_bounds` —
  maps cleanly onto Pydantic (`gt/ge/lt/le`, `min_length/max_length`, `Literal`, ...). This is
  why `ros2 param describe` on a MoveIt node is informative: the descriptors are generated.
- **The spec YAML is not installed.** The CMake macro installs only generated artifacts
  (`install(DIRECTORY ${LIB_INCLUDE_DIR} DESTINATION include)` for C++;
  `install(FILES ${PARAM_HEADER_FILE} DESTINATION .../${PROJECT_NAME})` for Python). So a launch
  tool **cannot read a third-party package's spec from an install tree** — only from a source
  checkout. This is the decisive constraint on "reuse the node's schema if available".

Consequence for discovery, best to worst: a package that ships a **Pydantic model** (tier 2) is
importable with no new machinery; an **ament index resource** we define could point at a spec file
the package installs (one `install()` line for its author, and a `generate_parameter_library` spec
is an acceptable format — convert on read); a **Python** node's generated module *is* installed and
importable, so statically introspectable; a **C++** node leaves only an installed header (text,
parseable but brittle) and its runtime services, which is tier 3's territory. Worth interoperating
with rather than competing against: if a package already has a spec YAML, the only ask is that it
install it.

**Framing to adopt:** our schema describes the **launch's** interface — structure, toggles,
cross-node coupling — not a mirror of every node's parameters. Those are two different
contracts. The launch composes a node's schema where one exists and delegates to the untyped
lane where it doesn't. That dissolves the "the schema cannot really be complete" tension: it is
complete *for the launch*.

Open: whether tier 2 belongs in this project at all (it makes `ros2_typed_launch` a node-side
library too, not just a launch front end) or in a sibling package; whether tier 3 scaffolding
is worth building before tier 1 is in use.

### Imperative / synchronous launching (Q26) — BUILT (2026-10-01)

The complaint (2026-09-28): stock launch is fire-and-forget. You declare a `LaunchDescription`,
hand it over, and have no control afterwards. Wanted instead: a **script** that launches
something, does something else (checks, waiting for a bringup signal), then does more — while
keeping what launch gives you (live output, a node failure not taking the parent down).

```python
@script(config=BringupConfig)
def bringup(session, config):
    sim = session.start(turtle_params, ParamsDemoConfig(namespace="demo"))
    try:
        sim.wait_started(timeout=30)                 # the process exists
        session.wait_for_node("/demo/turtlesim")     # the node is really up (rclpy graph)
        driver = session.start(lambda: driver_process("demo"), name="driver")
        driver["driver"].wait_output("publishing")   # wait for what it says, not a sleep
        driver.restart()                             # stop, rebuild, start -- same handle
        driver.stop()                                # one stage down, session lives on
        return 0
    finally:
        sim.stop()                                   # runs on Ctrl-C too

if __name__ == "__main__":
    bringup.main()
```

**The constraint that shapes it:** `run()`/`run_async()` raise `RuntimeError('LaunchService can
only be run in the main thread.')`. So the service owns the main thread and **the script runs on a
worker**, which is legal because `emit_event`/`include_launch_description` are documented
thread-safe. `run(shutdown_when_idle=False)` keeps an idle session alive between steps.

**The four decisions (asked one at a time, 2026-09-30) and how they came out:**

1. **Ctrl-C** → the wait raises `SessionShutdown` at once, the script's `finally` runs, then
   shutdown. Every wait is shutdown-aware; none returns a comforting lie. A script that lets the
   exception through exits **130** with `[INFO] [tlaunch]: interrupted`, not a traceback.
2. **How you run it** → **both**, from one `main()`: `tlaunch <pkg> <file>` and `python3 <file>`.
   The loader searches for `*_launch.py` **and `*_script.py`** (`loader.TARGET_SUFFIXES`), and
   both decorators tag their `generate_launch_description` with `.typed_target`, so there is one
   discovery path and one dispatch point (`cli._main`). Cost, as predicted: under `python3` the
   module is `__main__`, so a **script must use absolute imports** (the demo says so in a comment).
3. **Handles** → a **stage handle you can drill into**: `session.start()` returns a `Stage`;
   `stage["planner"]` is a `Process`. Keys come from the declared `name` at build time
   (`_declared_name` reads through launch's substitution normalization, so `ExecuteProcess(name=)`
   and `Node(name=)` both work); a runtime-named node has no key. Waits hang off handles, never off
   a name string, because launch's `process_name` carries an unpredictable `-N` suffix.
4. **On error** → **always stop them.** The script returning or raising shuts the session down; no
   `--keep-running`. Exit code is the script's int if it returns one, else the service's.

**What is in the code:** `session.py` — `Session` (start/sleep/log, `wait_for_node`,
`wait_for_topic`, `node_names`, `stop_all`), `Stage` (`wait_started`/`wait_exited`/`wait_output`/
`stop`/`restart`, `[key]`, `pids`), `Process` (`pid`, `returncode`, `running`, `output()`, the same
waits, `stop`), and `run_session` (the thread split); `core.py` — `@script`, `TypedScript`, and
`TypedTarget.main()` shared with `@launch`; `cli.run_script`. 22 unit tests, two of which drive a
real `LaunchService`.

**Verified by running it, not by reading** (`turtle_bringup_script.py`, plus throwaway probes):

| Claim | Evidence |
|---|---|
| ordered bringup | turtlesim up → graph check passes → driver starts → output matched → stop → teardown, in order, exit 0 |
| Ctrl-C mid-wait | `[WARNING] user interrupted`; the wait raised, `finally` ran with `shutting_down=True`, processes stopped, rc **130** |
| failure isolation | a process dying rc=3 left the script running and a later stage started fine |
| per-stage stop | driver stopped while turtlesim stayed up and the script continued |
| restart | `driver-2` → `driver-3` with a new pid, same `stage["driver"]` key |
| stock includes | a node started *inside* an included stock launch file was attributed to its stage and stopped with it |
| `python3` route | same script, same flags, same behaviour as under `tlaunch` |

**Two findings worth keeping:**

* **Launch refuses to execute an action twice** (`ExecuteLocal action 'failer-3': executed more
  than once`) — and that exception **takes the whole service down**. So `restart()` must *rebuild*:
  a stage is restartable only if it came from a typed launch or from a **function** that builds the
  actions (`session.start(lambda: driver_process(ns))`). From bare action objects it refuses, with
  that fix in the message. `Process.restart()` is deliberately not implemented: rebuilding one node
  out of a list would drop the scope its siblings put it in (an enclosing `PushRosNamespace`).
* **Stage attribution survives includes** by marking each stage's scope with
  `SetEnvironmentVariable("TLAUNCH_STAGE", id)` inside its scoped `GroupAction`: a process we never
  built still reports that env in its start event, so it lands in the right stage (keyed by
  launch's runtime process name, since it has no declared one).

Remaining edges: `--show-config` for a script is rendered *before* the run (stages build as the
script goes), so its node-pattern matches are unknown there, unlike for a launch; the health
checks are only available inside a script, not as something a plain launch declares.

### Process tracking + diagnostics on death (Q27) — BUILT (2026-10-01)

Wanted (2026-09-30): the launch keeps track of the PIDs it spawns; when a process dies it
**logs an error** and **publishes a diagnostics message**.

**The finding that set the priorities:** a dying process does **not** change launch's exit code.
`LaunchService.run()` returned **0** for a run whose process exited 3 — so a failed bringup is
indistinguishable from a good one to anything reading `$?`. Fixing that turned out to matter more
than the topic.

**Decided 2026-10-01** (asked one question at a time):

1. **Tracking, the error line and an end-of-run summary are always on** — they need no ROS node and
   cost nothing. **Publishing is `--diagnostics`**, because it needs an rclpy node inside the launch
   process and nothing listens by default (`diagnostic_updater`/`diagnostic_aggregator` are not even
   installed here).
2. **An unexpected death makes `tlaunch` exit non-zero** (1 when launch itself returned 0), with a
   summary naming what died. *Unexpected* = we did not ask it to stop, it was not going down with
   the launch, and it exited non-zero or was killed by a signal. A oneshot exiting 0 is not a death;
   neither is a process we stopped, nor anything killed during shutdown.
3. **A diagnostic is named by the node's fully qualified name** (`/demo/keeper`), with the process
   name alongside pid, returncode and command in its key/value list. `Node.node_name` resolves once
   the action is executing, which is exactly when the start event arrives.
4. **Direct children only.** A wrapper script's children are invisible — launch only learns the
   wrapper's pid — and the right fix there is for the wrapper to pass its child's exit code up. A
   composable node has no pid at all (a thread in a container); that case is still open.

**What is in the code:** `monitor.py` — `ProcessInfo` (pid, cmd, node name, exit status, recent
output, `died_unexpectedly`, `why()`), `Monitor` (four event handlers via `handlers()`, `note_stop`,
`deaths()`, `summary()`), `report()` (the summary plus the exit code, used by both run paths) and
`Diagnostics` (a hidden rclpy node publishing `DiagnosticArray` on `/diagnostics` at 1 Hz from its
own thread). `session.py` now **uses** that monitor rather than tracking separately — `Process` is a
handle over a `ProcessInfo` — so there is one implementation for plain runs and sessions.
`cli.run_launch` prepends `monitor.handlers()` to every launch. Demo: `flaky_demo_launch.py`.
10 unit tests in `test_monitor.py`.

**Verified by running it:**

| Claim | Evidence |
|---|---|
| a death is named and logged | `[ERROR] [tlaunch]: /demo/keeper (turtlesim_node-1) died unexpectedly: killed by SIGKILL [pid 735979]` |
| the run says so at the end | `tlaunch: 1 process(es) died unexpectedly:` + the node, why, pid and cmd |
| the exit code says so | `exit: 1` for the flaky demo; `0` with `--rc 0`, and `0` for a run where only a oneshot exited and the rest were stopped at shutdown |
| `/diagnostics` is real | an external `ros2 topic echo /diagnostics` showed `name: /demo/keeper`, `level: "\0"` → `"\x02"`, `message: 'died unexpectedly: killed by SIGKILL'`, with process/pid/returncode/cmd in `values` |
| signals read as words | `returncode` −9/−15/−2 → "killed by SIGKILL/SIGTERM/SIGINT" |

**Gotcha, found the hard way:** `DiagnosticStatus.level` must be the **bytes** constant
(`DiagnosticStatus.ERROR` == `b'\x02'`). Assigning the int `2` passes Python and then **aborts the
whole process** inside the C conversion (`Assertion 'PyBytes_Check(field)' failed`) — not an
exception. A unit test pins the bytes form.

**Probed and rejected:** the ROS graph as a death signal. A `SIGKILL`ed node left the graph in 0.2 s
in one run and was still listed 10 s later in another, so graph checks are a bringup aid (Q26's
`wait_for_node`), never the death detector. Process events, by contrast, arrived immediately.

Still open: composable nodes (no pid — probably needs the container's own node list); a
`required=True` notion per node (ROS 1's `required`, ROS 2's `on_exit=Shutdown()`); whether
`--diagnostics` should also publish a single aggregate status for the launch as a whole.

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

Have: 73 unit tests (`pytest`/`colcon test`), most needing no ROS; real launches checked by
hand (`ros2 param get`, `/tf_static`, node lists, exit codes). Proposed next step: one
subprocess-based integration test running the installed `tlaunch` on the showcase launch,
skipped unless ROS + turtlesim are present. (`launch_testing` was considered and rejected — a
pip-installed pytest once broke its plugin; see the Pydantic-venv gotcha history, now moot
since there's no project venv to pollute it.)

**Suggested order when picking this back up:** the active front is **Q25 → Q8 → Q24** (config
substitutions are self-contained and unblock nothing else; the node-pattern lanes are the real
feature; node schemas are the long game and can wait for the untyped lane to be in use). Then:
Q20 (gates how much compatibility to build) → Q18 + Q6 polish + Q5 remainder (small,
high-value) → Q23, Q14 → Q12, Q21 → Q15 units → Q16 if `python3-darts-core` becomes
installable.

---

## Reference

### ROS params, remaps and substitutions (probed on this machine, 2026-09-26/27)

For Q8/Q25/Q24. All of this was run, not read off documentation.

| Fact | Evidence |
|---|---|
| Param wildcards work | `--params-file` with `/**:` and `/**/turtlesim:` sections both applied (`ros2 param get` returned each) |
| A param section matching no node is **silently ignored** | a `/typo_node_name:` section alongside them: node started fine, zero warnings — the failure mode principle 4 forbids |
| Remap wildcards are **not implemented in rcl** | `-r /**:chatter:=x` → `Couldn't parse remap rule ... Wildcard '**' is not implemented, at ./src/rcl/arguments.c:1219`; process aborts |
| Exact node-name prefix remaps do work | `-r talker:chatter:=named_chatter` → topic appeared as `/named_chatter` |
| `parse_substitution` works standalone | `launch.frontend.parse_substitution` + `perform_substitutions(LaunchContext(), ...)`: `$(find-pkg-share turtlesim)`, `$(find-pkg-prefix turtlesim)`, `$(env HOME)`, `$(env UNSET default)`, `$(eval '3 * 14')` → `42`, `$(command 'echo hi')` all resolved |
| Substitution keyword is `env` | not `env-var`; registry: `find-pkg-share`, `find-pkg-prefix`, `find-exec`, `exec-in-pkg`, `param`, `env`, `command`, `eval`, `var`, `dirname`, `filename`, `file-content`, `anon`, `log_dir`, `if`, `equals`, `not-equals`, `not`, `and`, `or`, `any`, `all` |
| `launch_ros` substitutions need an import first | bare `launch` → `Unknown substitution: find-pkg-share`; fixed by importing `launch_ros.substitutions` (the `@expose_substitution` decorators run at import) |
| `ParameterFile(allow_substs=True)` substitutes **whole file text** | source: `perform_substitutions(context, parse_substitution(f.read()))` → temp file → `yaml.safe_load` to validate; failure message is only `"The substituted parameter file is not a valid yaml file"` |
| A node already publishes its own param schema at runtime | `ros2 param describe /turtlesim background_r` → type integer, description "Red channel of the background color", min 0, max 255, step 1 |
| `ParameterDescriptor` ≅ our `FieldSpec` | fields: `name, type, description, additional_constraints, read_only, dynamic_typing, floating_point_range, integer_range` |
| Custom ament index resource types are conventional | 21 already installed (`rclcpp_components`, `*__pluginlib__plugin`, ...); API: `ament_index_python.resources.{get_resource,get_resources,has_resource,get_resource_types}` |
| `lark` is a system package | `python3-lark` 1.1.9-1, already a hard dependency of `launch` — reusing the stock parser adds no new dependency |

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

**Overrides (Q8).** The old sketch here — a reserved `overrides:` section keyed by per-node
logical ids we would have invented — was **dropped on 2026-09-26** in favour of ROS's own node
patterns (`/**`, `/**/node`, `/ns/*`) and the standard `ros__parameters` spelling. See Q8 above
for the live design. Ideas kept from it: launch authors still expose the topic names that matter
as typed config, the overlay only covers what the author didn't anticipate, and a dry-run way to
enumerate the surface is worth having (now: list the nodes a launch builds, with the patterns
that matched each). Dropped with it: `prefix:` per node id (`--launch-prefix[-filter]` already
covers the debugging case).

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
