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

Moved to `../docs/goals.md` (goals G1-G7, principles 1-9). Direction and approaches:
`vision.md`.

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
| `$(...)` in config files | Reuse `launch.frontend.parse_substitution` (the stock lark grammar), expanded **eagerly per string leaf**. Not our own parser, not `ParameterFile`. **`$(var NAME)` reads this launch's own arguments** by dotted path, which splits expansion in two: `launch_args` when a file is read (no `$(var ...)` there), node sections — values *and* pattern — once the args are merged and validated. Built — Q25 |
| Node-defined schemas | **Optional**: when a node ships a schema, `tlaunch -h` shows its parameters; when it doesn't, the untyped lane carries them. MoveIt's `generate_parameter_library` spec is *not installed* by its CMake macro, so discovery needs our own resource — Q24 |
| Process monitoring | Every run records the processes it starts and **reports what died**: an error line naming the node, an end-of-run summary, and a **non-zero exit code** (launch itself returns 0 for a run whose node died). `/diagnostics` publishing is opt-in via `--diagnostics`, since it needs an rclpy node in the launch. Direct children only. Built — Q27 |
| Imperative launching | `@script(config=...)` + a `Session`: the service owns the main thread, the script runs on a worker. `session.start()` returns a **stage handle** you drill into (`stage["planner"]`); Ctrl-C makes the current wait raise so the script's `finally` runs; the session always stops what it started; runnable as `tlaunch <pkg> <file>` **and** `python3 <file>`. Built — Q26 |
| tmux panes | `--tmux` runs each process in its own pane; `--tmux-only GLOB` picks some. **Launch keeps owning the process**: it runs `tlaunch-pane`, which runs the real command in a pane and ends exactly as it did, so pids, signals and exit codes are unchanged. Grouping (e.g. an include into one window) is by environment variable, via `window(name, actions)`. A pane gives a **real TTY**, which stock launch cannot. The pane's output is copied back to the shim's stdout (`lte-run --tee`), so launch's log files, the death report and `wait_output()` still see it; only the terminal echo is dropped (the pane is where it is read). Built — Q28 |
| tmux dashboard | `--tmux-dash` opens libtmux_easy's `lte-dash` over the run: home = the run's own output, sidebar status from pane options, stop/shutdown through launch. `--tmux-logs` adds a `/rosout` viewer window, toggled with `prefix v`. Keys are borrowed for the run (`lte-key`), never put in `~/.tmux.conf`. Built — Q29 |
| declared checks | `checks.node/topic/output(..., then=[...])`: a launch action that waits in launch's loop and starts `then` once it passes. A timeout is an error, a summary line, a non-zero exit, and by default a shutdown. Built — Q30 |
| command lines | `--log-level` gives every ROS node `--ros-args --log-level`; `--log-commands` logs each final command. Same `Executable.prepare` hook as tmux, installed first. Built — Q31 |
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
removed 2026-11-01): **directory loading** (not needed for now) (point `--config` at a directory, load
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

### Interop with stock launch files (Q17) — DONE (both directions)

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

**Stock → typed: built 2026-10-11** (`stock.py`). Without it a typed launch could only sit
at the top of a launch tree.

How: a typed launch's `generate_launch_description()` returns a real `LaunchDescription` —
one `DeclareLaunchArgument` per field with a flag (dotted name; schema default, none if
required so stock reports it; description + constraints), plus `tlaunch_config`
(`;`-separated config files), plus an `OpaqueFunction` that runs `cli.configure` (layering,
validation, `$(var)`) and `cli.build_actions`. tlaunch itself reads `.typed_target` and never
calls it. Scripts still raise "run it with tlaunch".

Finding the file: stock includes by path; ours live in the Python package and break their
relative imports when loaded by path. `lte_launch_install_stubs(<py pkg> <module>...)`
(CMake, `cmake/lte_launch-extras.cmake`) installs a one-line stub per module into
`share/<pkg>/launch/` that imports it by package path. So `ros2 launch <pkg> <module>.py` and
`$(find-pkg-share pkg)/launch/<module>.py` both work.

| Question | Answer |
|---|---|
| Python and XML parents, `ros2 launch` direct | Work; verified with turtlesim (`stock_parent.launch.xml`) |
| Layering | defaults < `tlaunch_config` files < arguments. "Given" = set before our declarations run (a snapshot `OpaqueFunction` first), so a declared default never beats a file |
| Types | Arguments are text: scalars/bools go to the schema as text (as flags do); lists/dicts are YAML |
| Errors | tlaunch's own lines, named by argument, under stock's "Caught exception in launch" |
| Typos | Stock ignores undeclared arguments. One under a group of ours (`turtlesim.nmespace`) is reported with a suggestion |
| Node sections, `RosParams`, `$(var)` remaps | Same as tlaunch (verified: `turtle_params_launch.py` with a config file) |

Limits: stock passes a parent's launch configurations down unscoped, so a parent argument
named like one of our fields feeds it silently (wrap the include in a `<group>`). tlaunch's own
options do not apply (monitoring/exit code, `--tmux*`, `--show-config`, saved effective
config). `@script` targets cannot be included.

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

**`$(var NAME)` -- added 2026-10-02.** It reads a *launch configuration*, which a bare
`LaunchContext` has none of, so it needed a meaning of ours: **the launch's own arguments, by
dotted path** (`$(var turtlesim.namespace)`). That forced the expansion into **two stages**, since
one substitution now depends on the config being resolved:

1. `launch_args` are expanded as each file is read (after the schema check, so an unknown key is
   reported first). `$(var ...)` is **rejected** here -- an argument built from another argument
   means cycles -- with a message saying to write the value out or use it in a node section.
2. **node sections are expanded after the args are merged and validated**, with
   `context.launch_configurations` seeded from them (`config.variables`). That covers CLI overrides
   too, so `--namespace alpha` reaches a remap written as `/$(var namespace)/joy_vel`.

The **pattern key** is expanded as well (`/$(var namespace)/turtlesim:`, via `Section.retarget`),
because a section keyed on a hard-coded namespace would silently stop matching the moment the
namespace was overridden -- the worst kind of failure, since a non-matching section is only a
warning.

Names are checked **against the schema, in the same pass as the unknown-key check**
(`config.unknown_vars`), because both answer the same question -- does this config belong to this
launch? So a config aimed at another launch reports everything at once, before anything is expanded:

```
tlaunch: error: in config file 'params_demo.yaml':
  namespace: unknown key
  /$(var namespace)/turtlesim: $(var namespace) is not an argument of this launch (did you mean 'turtlesim.namespace'?)
```

`--ros-param`/`--ros-remap` are checked there too. What is left for expansion time is the case that
needs values: an argument with no string form says why (`'tags' is a list`). Bools read as launch
spells them (`true`/`false`).

Verified live: `--namespace alpha` with `params_demo.yaml` produced a node actually subscribed to
`/alpha/joy_vel` (`ros2 node info`), with the section pattern retargeted to `/alpha/turtlesim`.

**Names are repaired and checked (2026-10-02).** An empty argument substituted into a path leaves
a repeated slash -- `/$(var namespace)/joy_vel` with `namespace: ""` reads `//joy_vel` -- and ROS
rejects that outright. Checked, not assumed: a node handed it **aborts at startup** with
`Couldn't parse remap rule: '-r turtle1/cmd_vel:=//joy_vel'. Error: error not set` and SIGABRT, i.e.
rcl does not even report a reason. So after expansion:

* `rc.tidy` collapses repeated slashes and drops a trailing one, on **remap names and the pattern**.
  Both repairs preserve meaning -- an empty namespace *is* the root, so `/joy_vel` is what was meant,
  and our pattern matcher already read `//turtlesim` as `/turtlesim`;
* `rc.check_name` then runs ROS's **own** validator (`rclpy.validate_topic_name`, which accepts every
  legal remap form: relative, `~/topic`, `{substitution}/topic`) on both sides of every remap, so a
  bad name is a config error naming the key instead of a dead node. 64 ms to import, 0.3 µs a call.

Parameter *values* are left alone -- they are not names, and a value may legitimately contain `//`.

**Open, and a real foot-gun:** a substitution always produces **text**, so
`ros__parameters: {port: $(env PORT)}` sets the *string* `"8080"`, and a node expecting an integer
rejects it at runtime. Stock `ParameterFile(allow_substs=True)` does not have this problem because
it substitutes into the raw file and re-parses the whole thing as YAML. The fix would be to re-parse
a value that is **entirely** one substitution (`"69"` -> `69`), leaving mixed strings
(`/$(var ns)/joy_vel`) alone. Not done: it changes types of existing configs, so it wants a decision.

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

Open: whether tier 2 belongs in this project at all (it makes `lte_launch` a node-side
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

### Processes in tmux panes (Q28) — BUILT (2026-10-02)

Wanted: a launch merges every process's output into one pane, so reading one node means
grepping. Optionally split any or all launched elements — a node, an included launch — into
tmux panes.

Of the fork recorded in `#deferred-designs` (tmux owns the process vs. launch owns it with a
per-pane shim), **(ii) was built**: launch still owns every process. The deciding argument is
Q27's — a run whose node dies has to say so, and a process tmux owns has no exit code launch
can see, so (i) would have given back the very thing Q27 just fixed.

**Decisions**

* **opt-in per run and per process.** `--tmux`, `--tmux-only GLOB` (repeatable; matched
  against the process name, that name without launch's `-N` counter, and the node's FQN),
  `--tmux-session NAME`. A pane is a window-manager decision, so launch *files* say nothing
  about it — one exception, grouping, below.
* **`tlaunch-pane`, a stand-in process.** Launch runs it in place of the real command; it
  splits a pane, runs the command there and then ends the same way the command did (re-raising
  a signal rather than reporting 128+N), so launch records the true returncode. Stdlib only —
  one runs per launched process.
* **inside the pane, the command runs under libtmux_easy's `lte-run`** (revised 2026-10-10;
  was a `sh -c "cmd; rc=$?; printf $rc > file"` line). `lte-run` is the command's real parent:
  `waitpid` gives the exact code or signal, sent to the shim over a unix socket (`LTE_DIR`,
  passed with `-e`); signals go back the same way as `{"cmd": "signal"}`. No tmux polling, no
  status file, no 128+N guessing. A hard dependency: `uv tool install --editable
  ~/projects/libtmux_easy` puts `lte-run` on `PATH`; `--tmux` refuses to start without it.
  Improvements found here are fed back to libtmux_easy rather than worked around.
* **hooked at `Executable.prepare`,** where launch turns an action's substitutions into the
  argv it will run. One seam covers nodes, plain `ExecuteProcess`, and anything inside an
  *included* launch file, typed or stock — there is no launch description to walk for a
  process launch creates from a timer, a condition or an include.
* **grouping by environment variable** (`TLAUNCH_TMUX_WINDOW`), set by
  `window(name, actions)` — a scoped `GroupAction` + `SetEnvironmentVariable`. Processes
  carrying the same value share a window, however deep they were started, which is what makes
  "an included launch in its own window" work with no new API. Without `--tmux` it does
  nothing at all. (Same trick as the session's stage markers in Q26.)
* **nothing is ever killed to make room.** An existing session is joined (so running inside
  tmux puts the windows where the user already is), window names are made unique, and only
  panes whose process has finished are closed.
* **the session is tidied up unless something failed.** A session of our own that nobody is
  attached to and nothing failed in is closed at the end of the run — otherwise one piles up
  per run. If a pane's process failed, its pane *is* the last of that output, so it stays, and
  tlaunch says how to reach it.
* **a pane is a convenience, never a dependency.** If no pane can be had (no room, no window,
  no tmux), the shim `exec`s the real command instead, so the process still runs with the
  shim's pid and its output back in launch's log.

**Evidence**

| Question | Answer |
|---|---|
| Does launch still see the real exit code? | Yes — `exit 0/7/137` → 0/7/137 (137 is no longer mistaken for SIGKILL), `kill -15` → `-15`, SIGSEGV → `-11`. `flaky_demo_launch.py --tmux` reports `exit code 3` and exits 1, same as without `--tmux` |
| Does a node that catches the shutdown signal report its own code? | Only since `lte-run`. With a shell in the pane, the forwarded `killpg` hit the shell too: a node trapping SIGTERM and exiting 3 was reported as killed by SIGTERM (4/4). Now 3 (and a test pins it). A command ignoring signals is sent SIGKILL by the shim after 8 s — before launch's own SIGKILL, which would land on the shim and orphan the command |
| Is a pane killed by hand a failure? | Yes: `lte-run` gets SIGHUP and passes it on, so the shim dies by SIGHUP. If `lte-run` never reports, the shim also dies by SIGHUP |
| Does a pane give a real terminal? | Yes — `tty` in a pane answers `/dev/pts/N`, `[ -t 0 ]` passes. **`turtle_teleop_key` works**, where under plain launch it dies with "Failed to get old console mode" |
| Does the pane's process get launch's environment? | Only if passed explicitly. The tmux **server** forks a pane's process, so it uses the *server's* environment — which in a joined session predates the workspace being sourced. `split-window -e K=V` per variable fixes it (66 variables in a typical run); verified `ROS_DISTRO` and `TLAUNCH_TMUX_WINDOW` in `/proc/<pane pid>/environ` |
| Is `#{pane_dead_status}` trustworthy? | **No.** With `respawn-pane` it was empty roughly 1 run in 8 (tmux 3.4); a SIGKILLed pane read `1` instead of the signal. So nothing reads it for the exit code (that comes from `lte-run`), and the shim records the status as the pane option `@tlaunch_status` for the end-of-run tidy-up — `stopped` if it was asked to stop, so a Ctrl-C does not keep the session as "failed" |
| How is "killed by signal N" passed on? | The shim **re-raises the signal on itself**, so launch records `-15`/`-9` exactly as if it had started the command |
| Does a dead pane survive to be read? | Yes, with `remain-on-exit on` — set on the **window** before anything is split, since setting it per pane afterwards is a race a fast-failing process wins |
| Do dead panes pile up? | Yes, until "no space for new pane". Finished panes are closed before a new split, and the layout is re-tiled each time (without re-tiling a window fits only ~6) |
| Is `libtmux_easy` used? | **Its `lte-run` wrapper, yes** (since 2026-10-10). `TmuxSession` still no: each process needs its own stand-in process, which cannot share a `TmuxSession` object; its shell pane mode types commands into bash and so loses launch's environment; and importing `libtmux`/`psutil` per stand-in costs startup. `wrap.py` is stdlib-only, so none of that applies to it |
| What does `lte-run` cost per pane? | 0.02 s and 12.5 MB, after a fix fed back to libtmux_easy: its console script imported the whole package (`libtmux`, `psutil`) through `__init__`, 0.09 s and 19.8 MB. `__init__` now loads its names lazily |

**Gotchas found**

* `tmux display-message -p -t %999` for a pane that does not exist **succeeds** and prints an
  empty format. Waiting for an error is an endless wait — a missing pane is recognised by the
  empty answer.
* a shell between launch's signal and the command takes the signal too (`killpg` reaches the
  whole pane group). Superseded by `lte-run`, which signals the target and its descendants only.
* the shim's tmux error message once printed the whole `split-window` argv — every `-e NAME=VALUE`,
  tokens included — into launch's log. It now names only the subcommand.
* `signal.signal(SIGKILL, SIG_DFL)` raises `OSError` — so re-raising SIGKILL on yourself has to
  skip the reset and go straight to `os.kill`.
* a window cannot be created without a pane, and that pane runs a shell no process will use.
  It cannot be closed from tlaunch either, because a window with no panes is gone — so the
  first process into a window is told to retire it (`--retire`) once its own pane exists.

**Output back to launch (2026-10-11, reworked 2026-10-15).** A process in a pane writes to the
pane, which used to cut it off from launch's log files, the death report's last lines and
`Process.wait_output()`. Now the shim relays it:

* the pane runs `lte-run --tee <scratch>/out -- command`. lte-run attaches the copy (tmux
  `pipe-pane`, escapes stripped) before it starts the command, and has written the last of it
  before it reports the exit. The shim reads the FIFO and writes each line to its own stdout,
  which is where launch reads a process's output. stdout and stderr arrive merged, as stdout.
* first version (2026-10-11) did this in the shim: its own `pipe-pane`, a FIFO gate before
  `exec`, and a quiet-based drain because tmux refuses `pipe-pane` on an exited pane, so the
  copy never reaches EOF. Moved into libtmux_easy (`33f57da`), where `wait_for_output()` needed
  the same thing.
* the shim opens the FIFO (and holds a write end) before the pane exists: lte-run refuses a
  FIFO with no reader, and a FIFO with no writer reads as EOF.
* **the screen**: `tmux.off_screen` drops `screen` from the action's `output` before launch
  builds its loggers (`execute()` calls `prepare()` first), keeping `log`/`own_log`. The pane
  is where the output is read; echoing it in the terminal would undo the point of panes.

Verified 2026-11-01 against libtmux_easy `d37e15a`: `turtle_bringup_script.py --tmux` (its
`wait_output("publishing")` targets a process in a pane) runs to the end; the flaky demo's
paned output is in `launch.log`, its failed pane kept; dashboard `x`, `r` (refused), `Q`.

**Dashboard: a pane on show (2026-11-01).** `remain-on-exit` was a window option only. Showing a
pane swaps it into the dashboard's window, which lacks it, so a process that ended while on
show closed its pane and the sidebar said "gone closed". tlaunch-pane now sets it on the pane
too (pane options travel with the pane).

**Open**

* expanding an *included* `tlaunch` launch's own `--tmux` settings (nested tlaunch) — not
  attempted.
* `--tmux-attach`, to attach from tlaunch itself rather than printing the command.

### Dashboard and log window (Q29) — BUILT (2026-10-10)

Wanted: plug a run into libtmux_easy's dashboard, with launch's output (and every process not
in a pane) on its home page; optionally a window running ros2-term's `log_app.py`, with a key
to switch to it and back.

**Decisions**

* **libtmux_easy opens the window, tlaunch feeds it.** `TmuxSession.dashboard()` cannot be
  used (launch owns the processes), so libtmux_easy gained `dash.open_window()` and
  `lte-dash --open SESSION --status F --ctl C --home-log L`, stdlib-only, which
  `TmuxSession.dashboard()` now uses too. tlaunch needs only `lte-dash` on `PATH`, as with
  `lte-run`.
* **status from pane options.** Each `tlaunch-pane` records `@tlaunch_name/window/command/shim/
  target/status/stopped` on its pane; one `list-panes -s` per 0.5 s becomes lte-dash's status
  file (PaneStatus shape, `mode: "tlaunch"`). `window` is recorded because a pane on show sits
  in the dashboard's window.
* **commands go through launch.** `x` = `ShutdownProcess` for the action whose pid is the
  pane's stand-in, after `monitor.note_stop`, so it is a stop and not a death. `X` the same for
  every pane; `Q` = launch `Shutdown`, i.e. a Ctrl-C. `r` is refused (launch will not execute an
  action twice). `LaunchService.emit_event` is thread-safe, so the socket thread calls it
  directly; `tm.attach_run(service, monitor)` from `run_launch`/`run_session` hands it over.
* **home = fd 1 and 2, teed.** At the descriptor (`os.pipe` + `dup2`), not `sys.stdout`:
  launch's handlers hold their own stream reference. The terminal still gets everything.
* **stopped is not failed.** `@tlaunch_stopped` (last forwarded signal) replaced the
  `status=stopped` marker, so the real exit code survives for the sidebar *and* a stopped pane
  does not keep the session.
* **log window**: `--tmux-logs` runs `ros2-log-view` (`TLAUNCH_LOG_VIEWER` overrides) with
  launch's environment, and sets session option `@tlaunch_logs`.
* **keys are borrowed, not configured** (revised 2026-10-10; was a `~/.tmux.conf` block). A
  binding is server-global, so libtmux_easy's `lte-key borrow KEY OPTION -- CMD` wraps the key:
  `if-shell -F '#{OPTION}' { CMD } { <the key's previous binding> }`. Outside our sessions the
  key does what it always did; the original is saved in global options and restored by the last
  `lte-key return`, with a count so two runs can share a key. tlaunch borrows `v`
  (`@tlaunch_logs`: select it, or `last-window` if already there); the lte-dash sidebar borrows
  `g` itself and returns it in a `finally` (SIGHUP from kill-window/kill-session included).
  `TLAUNCH_LOGS_KEY`/`TLAUNCH_DASH_KEY` pick other keys, empty = none.

**Evidence** (tmux 3.4, Textual 8.2.8)

| Question | Answer |
|---|---|
| Sidebar lists the run? | Yes, grouped by window; `j` swaps the pane into the viewport |
| `x` on teleop | `sending signal 'SIGINT'`, `finished cleanly`, no death report; entry `exit 0, stopped SIGINT` |
| `Q` `Q` | `shutdown requested from the dashboard`, run exits 0, session closed, temp dir gone |
| Inside tmux | dashboard opens in the user's session and is selected; at the end only our windows close and the `@lte_dash_*`/`@tlaunch_logs` options are unset |
| A failure | session, dashboard, status file and home log kept; sidebar shows keeper stopped / oneshot exit 0 / flaky exit 3 |
| `prefix v` | dash -> logs -> dash, via a pty client |
| Borrowed keys | user's own `v` saved and wrapped; in another session `prefix v` ran it (renamed the window). Two runs at once: counts 2 -> 1 -> restored; `g` unbound again, no globals left |
| Non-pane output in home | yes (`--tmux-only 'turtle*'`: chatter's ticks in `home.log`) |

**Gotchas found**

* tmux 3.4 `set-option -t =name` says "no such session" (`has-session -t =name` is fine).
* `tmux()` strips stdout, eating the empty fields that end the last `-F` row: teleop, listed
  last, silently vanished from the status file. `tmux.pane_rows` pads; the status file and the
  end-of-run failure count both read through it.
* The installed ros2-term (0.1.3, from the index) has no `log_app`; `ros2-log-view` needs the
  local checkout installed (`pipx install --force --editable ~/projects/ros2-term`). `examples/run.sh`
  instead sets `TLAUNCH_LOG_VIEWER` to run the checkout with the pipx venv's Python.

**Open**

* rerun from the sidebar for session scripts (a stage *can* restart; a launch action cannot).
* non-pane processes are not listed in the sidebar (their output is in home).
* `--tmux-attach`, to attach from tlaunch rather than printing the command.

### Declared checks in a plain launch (Q30) — BUILT (2026-11-01)

A script can wait for a node, a topic or a line of output; a plain launch could not. Now it can,
declaratively (`checks.py`, demo `turtle_checks_launch.py`):

```python
checks.node("/demo/turtlesim", timeout=10, then=[driver,
    checks.output(driver, "publishing", timeout=10)])
```

* a check is an ordinary `launch.Action`: `execute()` starts an asyncio task in launch's loop
  (nothing blocks) and returns it from `get_asyncio_future()`, so launch waits for it.
* `then` actions run on a `CheckDone(passed=True)` event (`EventHandler`, `handle_once`).
  Stock `OpaqueCoroutine` cannot return actions, hence our own action.
* graph checks use launch_ros's per-launch node (`get_ros_node(context)`), filtering it out
  of the node list. No second rclpy context.
* failure: `[ERROR] [tlaunch]: check failed: ...`, recorded on the monitor (summary, exit 1),
  then `Shutdown` unless `on_fail="continue"`. The monitor puts itself in the context's
  globals (`lte_launch_monitor`); that is also how an output check sees lines printed
  before it started.
* under stock `ros2 launch` checks run the same; only the summary and exit code are missing.
* not a package-level export (`from lte_launch import checks` imports the submodule):
  `checks.py` needs `launch` at import time, and the package must import without ROS.

### Node log levels and command lines (Q31) — BUILT (2026-11-01)

`--log-level LEVEL|LOGGER:=LEVEL` (repeatable) appends `--ros-args --log-level ...` to every
`launch_ros` `Node`'s final command; plain processes are untouched. `--log-commands` logs each
final command line (`[INFO] [tlaunch]: turtlesim_node-1: ...`). Both hook `Executable.prepare`
(`commands.py`), installed before `tmux.patch` so the logged command is the real one, not the
pane stand-in. This is the "log final command lines" TODO; stock `--print` (print the
description and exit) is a different thing and not copied.

`-h` reorganised: the launch's arguments first, then tlaunch's own in three groups (config,
running, tmux) with one-line help. `tlaunch -h` with no launch lists every option and examples.

### Packaging and invocation (Q12) — PARTLY

**Done:** `tlaunch` is a real script, on `PATH` the moment `install/setup.bash` is sourced —
nothing else needed, no wrapper script of our own. Mechanism: `ament_cmake_core` registers a
default `path.sh` environment hook for every package that prepends `<prefix>/bin` to `PATH` if
that directory exists (the same hook that puts `ros2` itself on `PATH`); installing
`scripts/tlaunch` to `bin/` (in addition to `lib/lte_launch/`, kept for `ros2 run
lte_launch tlaunch`) triggers it. Side benefit: a real script composes normally where a
shell function couldn't (`timeout ... tlaunch ...` works directly).

**Done: tab completion**, matching `ros2 launch`'s own UX (package names, then that package's
launch files) — via `argcomplete` (`python3-argcomplete`, apt, the same library `ros2cli`
uses), also wired to fire from `install/setup.bash` alone: `lte_launch/hook/
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

**tmux.** Superseded — built as Q28 above; option (ii) was chosen. Kept for the reasoning.
`~/projects/libtmux_easy` (~600 lines over `libtmux`) is a stateful, in-process
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
