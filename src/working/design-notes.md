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
| Launch identity | A file's `@launch`-tagged, exported `generate_launch_description` — same handle stock `ros2 launch` uses |
| Typed-only | `tlaunch` runs typed launch files only; untyped ones use stock `ros2 launch`; a typed file run under stock launch errors with a pointer to `tlaunch` |
| Addressing | `tlaunch <package> <file>` — `<file>` the **full filename, `.py` required** (the bare stem alone is rejected with a hint), found by searching the package for a `*_launch.py` with that name (like `ros2 launch <pkg> <file>` searches `share/`); an explicit dotted (never `.py`) or on-disk-slash (`.py` required) path also works, needed only to disambiguate. `tlaunch <path>` for a standalone file (no parent package, so no relative imports). Historical: this was a share-dir walk, then a required dotted submodule path, then a bare stem with `.py` optional, before landing here — see `loader.py`'s docstring for the mechanism, not this doc, for future changes |
| Launch files | Named `*_launch.py` (not `*.launch.py` — unimportable), live in a top-level `launch/` directory (sibling to `package.xml`), installed by CMake as a real subpackage nested under the main package. Same-package include = plain `import`; `launch/__init__.py` re-exports every launch |
| Build system | Both packages use `ament_cmake` + `ament_cmake_python` (not `ament_python`) — more flexible for ROS, and the only way to get `launch/` installed as a nested subpackage while keeping it at a top-level, ROS-conventional path |
| Config layering | model defaults < `--config` files (repeatable, deep-merged, **later wins**, **lists replaced**, dict keys merged) < CLI flags. A config is a path or `<package>:<path>` |
| Pydantic | **v1** (system, apt/rosdep `python3-pydantic`), not v2 (was a pip-managed `.venv`). Both `package.xml`s declare `<exec_depend>python3-pydantic</exec_depend>`. No `PYTHONPATH` changes needed anywhere — `/usr/bin/python3` sees it by default. Trade-off accepted: v1's YAML coercion is lenient (`tolerance: 1.7` for an `int` silently truncates) and `Optional[X]` with no default is silently optional, not required. Full comparison: `#pydantic-1-vs-2` below |
| `include` | `include(child, config)` builds the child eagerly, returns a scoped `GroupAction`; parent namespace/params/remaps/env are inherited |
| Own schema interface / `InputDict` | Deferred — see `schema-interface-and-input-dict.md` |
| Node params/remaps from config | ROS-native node patterns (`/**`, `/**/node`, `/ns/*`), **not** invented per-node ids; wildcards **interpreted by us** for both lanes, because rcl implements them for params but refuses them for remappings. Keys `ros__parameters` / `ros__remappings`, at the file's top level; launch schema under a **required** `launch_args:`. Reaches nodes through **our `Node` wrapper**. Untyped by default; typable via a `RosParams` Pydantic type; `--strict` refuses the untyped lane. Fully decided, not built — Q8 |
| `$(...)` in config files | Reuse `launch.frontend.parse_substitution` (the stock lark grammar), expanded **eagerly per string leaf** at load time. Not our own parser, not `ParameterFile`. Designed, not built — Q25 |
| Node-defined schemas | **Optional**: when a node ships a schema, `tlaunch -h` shows its parameters; when it doesn't, the untyped lane carries them. MoveIt's `generate_parameter_library` spec is *not installed* by its CMake macro, so discovery needs our own resource — Q24 |
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

### Node params and remappings from config (Q8) — DECIDED, not built

**Supersedes the old "overrides" sketch** (which keyed off custom per-node logical ids we
would have invented). Decided instead to use **ROS's own node-pattern spelling**, so an
existing ROS parameter file is already most of the way to being a tlaunch config. Raised
2026-09-26 with `load_config.py` as the reference implementation.

Target file shape — three sections, one file:

```yaml
launch_args:                              # (1) our typed schema
  turtlesim: {background_r: 99}
/**:                                      # (2)+(3) ROS-native node patterns
  ros__parameters: {use_sim_time: true}
  ros__remappings: {/scan: /lidar/scan}
/my_ns/planner:
  ros__parameters: {tolerance: 0.3}
```

Params and remappings are **untyped, schema-free lanes** straight to the matching nodes — this
is the deliberate answer to Q24's duplication problem, not a gap.

**Decided 2026-09-27:**

- The remapping key is **`ros__remappings`**, not `remappings` — symmetric with
  `ros__parameters`, and the double underscore marks both as ROS's reserved namespace rather
  than schema fields of ours.
- **A `Node` wrapper** is how the data reaches nodes (settles open decision 4 below): our wrapper
  collects the matched params/remaps for that node and passes them to stock
  `launch_ros.actions.Node`. Plain stock `Node` keeps working (principle 6) — it just doesn't
  receive the collected config, which is the honest trade and needs to be documented, not hidden.
- **A Pydantic type for ROS parameters**, so params *can* be typed in the schema when the author
  wants them to be: a section declared with that type is understood as "these are ROS parameters",
  validated like any other schema field, and handed to the wrapper automatically — no separate
  restating. Sketch: `class TurtlesimParams(RosParams): background_r: int = Field(69, ge=0, le=255)`,
  then `TypedNode(package="turtlesim", executable="turtlesim_node", params=config.turtlesim)`.
  This makes tiers 1 and 2 of Q24 the same mechanism seen from two ends: typed when a schema
  exists, untyped lane when it doesn't.
- **Strictness flags** to refuse untyped params/remaps entirely, for interfaces that want to be
  fully specified. Naming open (`--strict-params`, or per-launch `@launch(strict_params=True)`,
  or both — a launch author's default that the CLI can tighten but not loosen).
- **CLI targeting** for arbitrary params/remaps, patterns included. Proposal:
  `--ros-param '/**:use_sim_time=true'` and `--ros-remap '/my_ns/planner:/scan=/lidar/scan'`,
  pattern optional and defaulting to `/**`, repeatable, layered above config files like every
  other CLI value. Hyphenated per Q18, so neither can ever collide with a schema field name.

**Why we must do the matching ourselves** (verified on this machine, both directions):

| | `/**`, `/**/node` wildcards |
|---|---|
| Parameters, via `--params-file` | rcl matches them itself — works |
| Remappings, via `-r /**:from:=to` | **rcl refuses**: `Couldn't parse remap rule ... Wildcard '**' is not implemented` |

Only an exact node-name prefix remap works (`-r talker:chatter:=named_chatter`, verified). So
wildcard remapping **does not exist in ROS 2**; interpreting the patterns ourselves and
emitting concrete per-node rules is a real capability, not a reimplementation. Decided
2026-09-27: we interpret them ourselves, for params and remappings alike, so both lanes behave
identically.

**The value on top**: we know every node we launch, so a pattern matching *none* of them can be
reported. Verified that stock ROS silently ignores a typo'd section (`/typo_node_name:` in a
params file → node starts fine, zero warnings) — the most common ROS param-file frustration.

All five open decisions were settled 2026-09-27:

| # | Decision | Chosen |
|---|---|---|
| 1 | Is `launch_args:` required? | **Required, always.** One rule, no ambiguity, better errors. A bare top-level schema key is now an error — **breaking**, see migration below |
| 2 | Where do node patterns live? | **Top level**, beside `launch_args:` — so a plain ROS parameter file is a valid tlaunch config with no edits. Needs the sniffing rule: a top-level key is a node pattern iff it contains `/` or `*`, or its value has `ros__parameters`/`ros__remappings`; anything else is an unknown-key error |
| 3 | Pattern matching no launched node | **Warn, don't fail.** A section may legitimately target a node behind a disabled toggle. Implication: the warning has to be *visible*, not buried in launch output — and `--show-config` should show which patterns matched which nodes, so the quiet case is still inspectable |
| 4 | How the data reaches nodes | **A `Node` wrapper.** Rejected: a post-pass over the built tree — it would reach plain stock `Node`s, but `Node` keeps `parameters`/`remappings` private and it would apply config to nodes the author never opted into |
| 5 | Typed field *and* untyped `ros__parameters` set the same param | **Typed wins, with a warning** naming both, so the effective value is never a surprise |

Note 3 and 5 are both "warn, proceed" — deliberately softer than principle 4's usual line,
because both cases have legitimate uses (conditional nodes; a params file shared across
launches). A warning is not silence, so principle 4 still holds. **The strictness flag above is
what makes them errors** for interfaces that want to be airtight: `--strict` (or the launch
author's own default) promotes both warnings to errors and refuses the untyped lane. That keeps
one mechanism for "I want this fully specified" rather than three separate switches.

**Migration** (consequence of decision 1): `config/blue_turtle.yaml`, `config/quiet.yaml` and
`working/prototypes/nav.yaml` all need their contents nested under `launch_args:`, and the error
for a bare root should say exactly that ("top-level key 'turtlesim' — launch settings now go
under `launch_args:`"), since every existing file and every doc example hits it once.

**Known boundary, to document rather than discover later**: matching needs each node's FQN at
build time. For nodes in *our* typed launches that is available (we own the namespace stack,
and our config values are real Python values, never substitutions). For nodes inside an
**included stock** launch file, the namespace may come from `PushRosNamespace`/substitutions
resolved only at launch time — so wildcard *remappings* cannot reliably reach in there. Params
still can: hand the merged param file through and let rcl match.

### `$(...)` substitutions in config files (Q25) — OPEN, designed, not built

Wanted: `$(find-pkg-share pkg)`, `$(find-pkg-prefix pkg)`, `$(env VAR [default])` inside YAML
values, per the substitution list in
[the roslaunch XML article](https://design.ros2.org/articles/roslaunch_xml.html).

**Do not write a parser, and do not use `ParameterFile`.** `launch.frontend.parse_substitution`
is the real lark-grammar parser the XML/YAML launch frontends use, and it works standalone
against a bare `LaunchContext()`. Verified: `$(find-pkg-share turtlesim)` →
`/opt/ros/jazzy/share/turtlesim`, `$(find-pkg-prefix turtlesim)` → `/opt/ros/jazzy`,
`$(env HOME)/maps/x.yaml`, `$(env NOT_SET default_val)` → `default_val`, `$(eval '3 * 14')` →
`42`, `$(command 'echo hi')` → `hi\n`. Zero parser code, the whole documented vocabulary for
free, and `lark` is already a hard dependency of `launch` itself.

Two gotchas: the keyword is **`env`**, not `env-var`; and `find-pkg-share`/`find-pkg-prefix`
live in `launch_ros`, so they only register once **`launch_ros.substitutions` has been
imported** (bare `launch` gives `Unknown substitution: find-pkg-share`).

**Apply it per string leaf of the already-parsed YAML, not to the file text.** `ParameterFile
(allow_substs=True)` runs the parser over the *entire raw file* and writes a temp file (read
its source: `perform_substitutions(context, parse_substitution(f.read()))`) — which is why
`load_config.py` needs `atexit` temp-file cleanup. Per-leaf is strictly better for us: no temp
files, keys and structure untouched (a stray `$` in a comment can't break the parse), errors
can name the exact key path, and it composes with our existing loader/provenance. Compare
`ParameterFile`'s failure mode: `"The substituted parameter file is not a valid yaml file"`.

Open: whether to expand in keys too (leaning no); whether `$(dirname)` should mean the config
file's directory (cheap — push it into the context as a local; would also settle half of Q6);
whether to allow `$(command ...)`/`$(eval ...)` at all, given they run arbitrary code from a
config file (leaning: allow, same trust level as the launch file itself, but document it).

### Node parameter schemas and introspection (Q24) — OPEN

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

Consequence: a **Python** node's generated module *is* installed and importable, so it is
statically introspectable. A **C++** node leaves an installed header (text — parseable, but
brittle) and its runtime parameter services. So the discovery ladder, best to worst:

1. the node's package ships a **Pydantic model** (tier 2) — importable, zero new machinery;
2. an **ament index resource** we define, pointing at a schema file the package installs — one
   `install()` line for the package author, and a `generate_parameter_library` spec YAML is an
   acceptable format for it (convert on read);
3. import a `generate_parameter_library`-generated **Python module**, if present;
4. **runtime `describe_parameters`** — needs the node running, so: `params-scaffold` codegen and
   a `--verify` mode, not a load-time path.

Worth interoping with rather than competing: if a package already has a spec YAML, the only ask
is that it install it.

**Framing to adopt:** our schema describes the **launch's** interface — structure, toggles,
cross-node coupling — not a mirror of every node's parameters. Those are two different
contracts. The launch composes a node's schema where one exists and delegates to the untyped
lane where it doesn't. That dissolves the "the schema cannot really be complete" tension: it is
complete *for the launch*.

Open: whether tier 2 belongs in this project at all (it makes `ros2_typed_launch` a node-side
library too, not just a launch front end) or in a sibling package; whether tier 3 scaffolding
is worth building before tier 1 is in use.

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
