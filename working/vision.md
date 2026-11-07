# Vision: the launch system we would build from scratch

A thought experiment (2026-11-05); option C was chosen 2026-11-06 and is built on branch
`engine-lte` (`../docs/design.md`). If nothing constrained us, what would launch
be, and how much of it do we already have? Goals and today's design: `../docs/goals.md`,
`../docs/design.md`.

## The finding that changes the cost

Reinventing the engine looked like a tall order. It is a smaller one, because
**libtmux_easy already is a synchronous process engine**, and lte_launch_test already writes
ROS systems in exactly the style wanted here:

> "Minimal boilerplate; a test reads top to bottom: which processes start, in what order,
> gated on what." -- lte_launch_test `docs/design.md`

| Engine need | libtmux_easy (0.3.0) | stock launch |
|---|---|---|
| Start now, get a handle | `start(name, cmd, ready=)` -> `Process` | No: return actions, run later |
| Wait for output / a condition | `wait_for`, `wait_for_in_order`, `wait_until` | Event handlers, `checks.*` (ours) |
| Exact exit code / signal | `lte-run` | Yes |
| A death is noticed | Exit watch + `fail_on`; `check_health()` | Logged only, exit 0 (we fixed this) |
| Stop with escalation | SIGINT -> SIGTERM -> SIGKILL, in parallel | Yes |
| Restart | `Process.start()` again | Refuses ("executed more than once") |
| Logs per process | `<window>.<name>.log` | `~/.ros/log/...` |
| Subprocess or tmux, same API | `_SubprocessBackend` / `_TmuxBackend` | Subprocess only (tmux is our patch) |
| Dashboard | `lte-dash`, `lte-session` | No (ours, via libtmux_easy) |
| Real TTY per process | tmux mode | No |
| ROS command lines (`--ros-args`, params file, remaps) | No (ROS-agnostic by design) | `launch_ros.Node` |
| Composable nodes, lifecycle | No | `launch_ros` |
| Run a stock launch file | As one process: `ros2 launch ...` (+ `launch_death()`) | Natively, node by node |

Q28 rejected "tmux owns the process" because tmux lost exit codes. `lte-run` has fixed that
since, so that objection is gone.

## The ideal, sketched

```python
@launch
def bringup(ns: str = "demo", teleop: bool = True, sim: bool = True):
    if sim:
        gz = include("ros_gz_sim", "gz_sim.launch.py", gz_args="empty.sdf")  # stock: one process
        wait_for_topic("/clock")
    with namespace(ns):
        tsim = node("turtlesim", "turtlesim_node", name="sim",       # starts here
                    params={"background_r": 99})
        tsim.wait_for_node()                                         # the graph, not a sleep
        if teleop:
            node("turtlesim", "turtle_teleop_key", name="teleop", window="input")
    # returning = "up": print the ready line, then supervise until Ctrl-C or a death
```

A run has five phases, all visible:

| Phase | What happens | On error |
|---|---|---|
| 1. Configure | Schema, `--config` layering, validation (as today) | Nothing has started. Exit 2 |
| 2. Bring up | The body runs top to bottom. `node()` starts the process and returns a handle. Waits block. | Teardown in reverse order, exit 1, the traceback points at the line |
| 3. Up | Print `[tlaunch] up` (a ready line for tests and scripts) | -- |
| 4. Supervise | Until Ctrl-C, `shutdown()` or an unexpected death | A death -> teardown, exit 1 |
| 5. Teardown | Reverse start order, escalation, death summary | Exit code says what happened |

### Answering "the 10th node has a mistake"

Fair point: with an engine that tears down reliably, starting nine nodes and then hitting an
error is acceptable. That is just a failed bringup, and teardown is the same path as Ctrl-C.
Two cheap pre-checks keep the common mistakes from getting that far:

- the schema validates *config* before phase 2, as today;
- `node()` checks that the package and executable exist (ament index, about a millisecond)
  before starting anything. Then a typo'd executable fails at its line without starting it.

What eager starting really costs is the **plan**: knowing what would run without running it.
The fix is to make that a backend:

### Backends: one body, several engines

The body talks to a session object (an implicit context, like `ros_config.current()`).
Whatever implements it decides what `node()` means:

| Backend | `node()` does | Waits | Used by |
|---|---|---|---|
| subprocess (default) | Starts a process | Block | `tlaunch` |
| tmux | Starts a wrap-mode pane | Block | `tlaunch --tmux`, `--tmux-dash` |
| **dry-run** | Records `(window:name, argv, params, remaps)` | Succeed at once | `--dry-run`, `--show-config` node matching, lte_launch_test method 1 |
| **test session** | `procs.start(...)` on the test's `Processes` | Block, with `check_health()` | lte_launch_test method 2 (`procs.build`) |
| launch (compat) | Builds a stock `Node` action | Not allowed, or become `checks.*` | Being included by a stock launch file |

The dry run cannot follow a branch that depends on a runtime result (`if
wait_for_topic(...)`). That case is rare, and it is documented: the dry run takes the success
path.

## Compatibility with lte_launch_test and libtmux_easy

lte_launch_test is the preferred way to test launches. libtmux_easy offers three ways to test
a session script (`docs/testing-scripts.md`). A typed launch should support all three:

| Method | The test | What tlaunch must provide | Today |
|---|---|---|---|
| 3. Process / adopt | `procs.start("sys:up", "tlaunch pkg f_launch.py --x 1", ready=r"\[tlaunch\] up")` | A ready line; non-zero exit on a death; SIGINT exits cleanly (0, or 130/-2, which lte counts as clean); launch's `process has died` line kept for `launch_death()` | Works, except there is no ready line |
| 1. Layout | `for name, cmd in f.panes(cfg): procs.start(name, cmd)` | A dry-run plan as `[("window:name", cmd)]` | No |
| 2. Build | `procs.build(f.build, config=...)`: the body runs on the test's session, every node its own process | A body that runs on a libtmux_easy session | No |

Method 2 is the strongest: each node is its own test process, with its own health check,
`fail_on`, log, restart and dashboard entry. Under the stock launch engine that is impossible,
because launch owns the processes. That alone argues for libtmux_easy as the engine.

## What it would take

| Piece | Where | Size |
|---|---|---|
| `Processes`-like API **outside pytest** (today `Processes()` raises `TypeError` without a test owner, and the backends are private) | libtmux_easy (improvements go back there, CLAUDE.md) | Medium: mostly lifting `_SubprocessBackend`/`_TmuxBackend` out of `testing.py` |
| ROS command builder: executable from the ament index, `--ros-args -r __node:= -r __ns:=`, a params file from node sections and `RosParams`, remaps, `--log-level` | ours (replaces `launch_ros.Node`) | Small to medium |
| Implicit session context, `node()`/`include()` returning handles, `namespace()`/`window()` scopes | ours | Small |
| Stock include as one process (`ros2 launch pkg file a:=b`), with launch's death lines turned into deaths (lte's `launch_death`) | ours, or reuse lte_launch_test's check | Small |
| Composable nodes: start a container, load each component (`ros2 component load` or rclpy `LoadNode`), turn a failed load into a death (lte's `composable_failure`) | ours | Medium |
| Lifecycle: `node.transition("configure")`, `wait_for_state("active")` | ours (lte_launch_test lists the same idea) | Small |
| Respawn | Engine | Small |
| Ready line, dry-run, layout export | ours | Small |
| Compat backend for stock including us | Keep `stock.py` for bodies without waits; otherwise stock runs `tlaunch` as an `ExecuteProcess` | Exists in part |

### What we would lose

| Loss | Severity | Mitigation |
|---|---|---|
| Node-level view *inside* a stock include (separate panes, per-node deaths) | Medium | It becomes one process. `launch_death` parsing restores death detection. Run tlaunch's own `Executable.prepare` hook inside that child (`tlaunch --stock FILE`) to keep panes |
| launch event handlers, `TimerAction`, substitutions in typed files | Low | Replaced by code: `sleep`, waits, `if` |
| Stock includes a synchronous typed launch node by node | Low | One process. Needed rarely, and it still reports deaths through its exit code |
| launch's maturity (edge cases in signal handling, ROS logging setup) | Medium | libtmux_easy already handles the signal side; ROS logging is per-node environment variables |
| Familiarity for ROS users | Medium | Stock files still run and still include; only typed files change |

## Approaches compared (2026-11-06)

**A. Keep the stock engine and cut boilerplate.** Plan-then-run `@launch`, `@script` and
`checks.*` for ordering, plus P1–P7 from `../docs/goals.md`.

| Pros | Cons |
|---|---|
| Least work and least risk; it all works today | Not synchronous: ordering needs a script or checks, so two ways to write a launch |
| Stock includes us node by node | lte_launch_test sees a launch as one process (G7) |
| Familiar to ROS users | We keep code that bends launch: the tmux stand-in, `Executable.prepare` hooks, the restart rebuild, the worker thread |
| | No restarting a single node |

**B. Stock engine, `Session`-style bodies as the default.** `node()` starts at once, through
the session machinery we already have.

| Pros | Cons |
|---|---|
| Synchronous to write, with no new engine | Every workaround above stays, and becomes load-bearing |
| One decorator instead of `@launch` + `@script` | Waits cross threads through launch's events: the most fragile part of the code |
| Stock interop unchanged | Still no per-node view in lte_launch_test |
| | Panes still go through the stand-in process |

**C. libtmux_easy as the engine; stock launch kept for compatibility.**

| Pros | Cons |
|---|---|
| Synchronous by nature: start, wait, restart, reverse teardown | We build ROS command lines ourselves (executable lookup, `--ros-args`, params file, remaps) |
| Per-node lte_launch_test (`procs.build`): health, log, restart per node | Composable nodes and lifecycle are ours to write |
| tmux, dashboard and exact exit codes native, not patched in | A stock include is one process; its deaths come from parsing its output |
| Shrinks or removes `tmux.py`, `dashboard.py`, `monitor.py`, `session.py`, `checks.py` (about 2,300 lines) | Stock including us node by node only for bodies without waits |
| One stack for launching, testing and the dashboard, all under our control | libtmux_easy needs a `Processes` usable outside pytest |
| | Less battle-tested than launch's process handling |

**D. Our own engine from scratch.** Every cost of C, without reusing libtmux_easy. The only gain
is avoiding a dependency we already control. Rejected.

### Recommendation: C, staged, with a decision gate

- **Limited risk.** About half the code does not depend on the engine: `schema`, `config`,
  `cli`, `loader`, `ros_config`, `subst`, `effective`. Only execution changes.
- **Removes the most code.** The parts written to fight launch are what libtmux_easy does
  natively.
- **The only option that meets G7.** Per-node tests are impossible under the stock engine.
- **What it gives up matters least.** Stock files are usually included *by* us, and a stock
  include as one process with death parsing is what lte_launch_test already accepts.

## Path (each step useful by itself)

1. **Be a good test citizen now.** Add a `[tlaunch] up` ready line and an lte_launch_test
   example that runs `tlaunch` as a process (method 3). Useful whatever is decided.
2. **Dry run and layout.** `--dry-run` and `f.panes(config)` (method 1).
3. **Demo of C** (done 2026-11-06, results below). A ROS command builder plus a synchronous engine on libtmux_easy
   (subprocess and tmux backends), porting `turtle_demo` and `turtle_params`.
4. **Decision gate.** Judged by four things:
   - parameters, remaps and namespaces reach the nodes (`ros2 param get`, `ros2 node info`);
   - shutdown and the exit code are right on Ctrl-C and on a death;
   - `procs.build` works in a real lte_launch_test;
   - the launch file is shorter.
5. **If it passes:** lift `Processes` out of pytest in libtmux_easy, then composables,
   lifecycle, merge `@launch`/`@script`, and make it the default. The stock engine stays as
   the compatibility backend for bodies without waits.
6. **If it fails:** A plus P1–P7. Steps 1–2 still stand.

## Demo results (2026-11-06, branch `engine-lte`)

Path step 3, built: `lte_launch/lte/` (about 1,000 lines), example
`examples/launch/turtle_lte_launch.py`, tests `test/test_lte.py` and
`examples/lte_test/` (lte_launch_test).

| Piece | File |
|---|---|
| ROS command builder (ament lookup, `--ros-args`, params file, remaps, log levels) | `lte/command.py` |
| Backends: subprocess (ours), tmux (libtmux_easy `TmuxSession`, wrap mode), dry run | `lte/backends.py` |
| Engine: `Run`, `Proc` handles, waits that fail on any death, supervise, reverse teardown | `lte/engine.py` |
| API: `launch`, `node`, `process`, `include`, `namespace`, `window`, waits; `LteLaunch.build`/`.panes`/`.plan` | `lte/__init__.py` |
| CLI: an `LteLaunch` target runs on the engine; `--tmux`, `--tmux-dash`, `--dry-run`, `--show-config` | `cli.run_lte` |

Decision gate:

| Criterion | Result |
|---|---|
| Params, remaps, namespaces reach the nodes | Params: yes, live (`ros2 param get` -> 12, from `--turtlesim.background_r 12`). Node sections: matched through `namespace()`, which closes the `PushRosNamespace` gap. Remap: in the argv (dry run), not checked live |
| Shutdown and exit code | Ctrl-C -> exit 0, nothing left running. A death -> `[ERROR]` line with output tail, summary, exit 1. The same on subprocesses and tmux. tmux: the session closes when clean, kept on a failure |
| lte_launch_test | **7/7 pass, all three methods.** Process: the ready line `[tlaunch]: up`. Build: each node is a test process (stop and restart one). Layout: `panes()` |
| Shorter launch files | No action list, no `generate_launch_description` line, no `@script` vs `@launch` split. Waits are plain calls |
| Dashboard | `--tmux-dash` is one `session.dashboard()` call. Today's `tmux.py` + `dashboard.py` + `tlaunch-pane` do this through launch |

Size: the engine is about 1,000 lines; `tmux.py`, `dashboard.py`, `monitor.py`, `session.py`,
`checks.py` and `tlaunch-pane` are about 2,700. Nothing is removed yet.

Gaps found, and where they stand after path step 5 (same day):

| Gap | Now |
|---|---|
| Stock include | One `ros2 launch` process, run live. Deaths inside it are not parsed yet (lte's `launch_death`) |
| Composable nodes, lifecycle | Composable: built (`composable.py`). Lifecycle: built (`lifecycle.py`) |
| libtmux_easy not importable by `/usr/bin/python3` | Still needs `PYTHONPATH` or its `.deb` |
| Subprocess backend is ours | Gone: `libtmux_easy.processes.Processes` (branch `processes-core`) runs every mode |
| tmux: always a session of our own | Joins the current session inside tmux. `--tmux-only`, `--tmux-logs` dropped |
| Teardown is sequential | All at once; a second Ctrl-C kills |
| Effective config saved before the run | Unchanged; `--show-config` dry-runs and has the matches |
| Stock launch cannot include an lte launch | It can: one `tlaunch` process |

## Path step 5 (2026-11-06, branch `engine-lte`)

Gate passed; the stock-engine code is removed on the branch: about 2,700 lines (`monitor`,
`tmux`, `dashboard`, `session`, `checks`, `commands`, `actions`, `tlaunch-pane`) plus four
examples. One decorator (`@launch`, synchronous). Parity with stock launch is tracked in
`../docs/parity.md`.

## Other directions (from `../TODO.md`, design-notes)

| Direction | Status |
|---|---|
| Own config interface, independent of the schema backend; units (`--robot.radius "22 cm"`) | Interested; plan U1–U6 in `schema-interface-and-input-dict.md` |
| Node-defined parameter schemas (Q24) | Deferred; the untyped lane covers the need |
| Simpler config class creation (P2 signature schemas) | Proposed in `../docs/goals.md` |
| Validators that run after layering (cross-field) | Pydantic `root_validator` today; no tlaunch-specific design |

## Open questions

| # | Question |
|---|---|
| V1 | One decorator whose body may block (`@launch`), or keep `@launch` (plan) and `@script` (synchronous) apart? The sketch above merges them |
| V2 | Is supervision after the body returns the right default, or should a body end with an explicit `spin()`/`wait()`? |
| V3 | Names: the tests address `window:name` (libtmux_easy), ROS addresses `/ns/name`. Derive the window from the namespace by default? |
| V4 | Should a stock include under the new engine run through `tlaunch --stock` (our hooks, panes, monitor) or a plain `ros2 launch`? |
| V5 | Should the ROS command builder reuse `launch_ros`'s pieces (params-file writing, name validation) without the engine? |
