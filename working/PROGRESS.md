# Progress

All work is on `main`. Last updated 2026-11-01.
Design: `../docs/design.md`; goals: `../docs/goals.md`. Decisions and open questions: `design-notes.md`.

## Done

| Area | What | Ref |
|---|---|---|
| Command | `tlaunch <pkg> <file>`, schema-generated flags/help, `--schema`, tab completion | Q12 |
| Config | `--config` layering, `<pkg>:<path>`, `launch_args` + node sections, `--show-config`, saved effective config | Q6, Q8 |
| Substitutions | `$(env/find-pkg-share/command/eval)`, `$(var NAME)` from the launch's own args | Q25 |
| Node params | `node()`, typed `RosParams`, untyped `ros__parameters`/`ros__remappings`, `--strict` | Q8 |
| Interop | Typed includes stock (`stock_args`); stock includes typed (`stock.py`, CMake stubs) | Q17 |
| Scripts | `@script`, `Session`, stage handles, waits (process/output/graph), stop/restart, Ctrl-C | Q26 |
| Monitoring | Death report, non-zero exit, `--diagnostics` | Q27 |
| tmux | `--tmux`, `--tmux-only`, `window()`, real TTY, exact exit codes via `lte-run` | Q28 |
| Dashboard | `--tmux-dash`, `--tmux-logs`, borrowed keys, `examples/run.sh` demo | Q29 |
| Checks | `checks.node/topic/output` with `then`, in a plain launch | Q30 |
| Command lines | `--log-level`, `--log-commands`, regrouped `-h` | Q31 |

## Latest (merged to `main` 2026-11-01)

- `b7583f1` tmux panes and dashboard (Q28, Q29), refactor, tests consolidated (189 → 59).
- `e342c8b` stock launch includes typed (Q17); DESIGN/PROGRESS.
- `faff2fa` declared checks (Q30), `--log-level`/`--log-commands`, regrouped `-h` (Q31).
- libtmux_easy compatibility checked against `d37e15a`: all interfaces match. Fixed on our
  side: a pane shown in the dashboard closed when its process ended (`remain-on-exit` now
  set per pane too).
- `load_config.py` removed: everything used from it is in tlaunch, except loading a
  directory of YAML files (not needed for now).

## Repo layout (2026-11-01)

- The repo root is the package: `~/projects/ros2_typed_launch` (was `~/projects/ros2_launch`,
  a colcon workspace with two packages under `src/`).
- Workspace: `~/ros2_ws`, `src/ros2_typed_launch` → the repo.
- The demo package is folded in as `examples/`, installed as `ros2_typed_launch.examples`
  (`tlaunch ros2_typed_launch turtle_demo_launch.py`); `test_examples.py` builds each one.
- Merged branches deleted; only `main`.

## Synchronous launch status

Built and verified: scripts (Q26), and declared checks in a plain launch (Q30). Gap: the
dashboard's rerun is refused for every run; a script's stages *can* restart, so `r` could map
to `stage.restart()` there.

## Branch `engine-lte` (2026-11-06, uncommitted)

The engine is libtmux_easy; the stock-engine code is removed on this branch (`main` keeps it).

| Area | What |
|---|---|
| Docs | `docs/` (`goals.md`, `design.md`, `parity.md`, `comparison.md`); `working/vision.md` (A-D, C chosen) |
| libtmux_easy | Branch `processes-core`: `libtmux_easy.processes.Processes` without pytest (`on_exit`, `env`, `kill_all`, bare windows); `testing.Processes` is a subclass. 5 new tests; plugin 33/33; lte_launch_test 123 pass (2 collection tests fail on `main` too) |
| Engine | `engine.py` on `Processes`: subprocess/tmux/dry/test group; parallel teardown, second Ctrl-C kills; `respawn`, `required`, `shutdown()`, `cwd`, `environment()`, `--launch-prefix`; joins the current tmux session |
| Composable | `component(into=)`: standalone, our container, or one started elsewhere (`container_at`, held: unloaded at teardown, fails if it leaves the graph); `load()` via LoadNode; failed load is an error; dry run as `ros2 component load` |
| Interop | Stock file: one `ros2 launch` process. Stock includes us: one `tlaunch` process (validated at include time) |
| Removed | `actions`, `checks`, `commands`, `dashboard`, `monitor`, `session`, `tmux`, `tlaunch-pane`, `@script`, `--diagnostics`, `--tmux-only`, `--tmux-logs`, `-n`; examples showcase, bringup script, checks, flaky, tmux demo; their tests |
| Tests | 47 unit; `examples/lte_test` 8/8 (process, build, start, layout) |
| Verified live | params reach nodes; Ctrl-C exit 0; a death exit 1; `--tmux` (teleop TTY); composition in all three placements, external unload and loss, failed load; stock include both ways |

Next: `docs/parity.md` "Next, by value" (lifecycle, stock include deaths, `on_shutdown`);
libtmux_easy `.deb` for system Python.

## Open questions

`design-notes.md`: Q5, Q6, Q12, Q14, Q18, Q19, Q20, Q21, Q22, Q23. Deferred: Q7, Q24, units.
