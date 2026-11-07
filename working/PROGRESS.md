# Progress

All work is on `main`. Last updated 2026-11-01.
Design: `../docs/design.md`; goals: `../docs/goals.md`. Decisions and open questions: `design-notes.md`.

## Done

| Area | What | Ref |
|---|---|---|
| Command | `lte-launch <pkg> <file>`, schema-generated flags/help, `--schema`, tab completion | Q12 |
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
- `load_config.py` removed: everything used from it is in lte-launch, except loading a
  directory of YAML files (not needed for now).

## Repo layout (2026-11-01)

- The repo root is the package: `~/projects/lte_launch` (was `~/projects/ros2_launch`,
  a colcon workspace with two packages under `src/`).
- Workspace: `~/ros2_ws`, `src/lte_launch` → the repo.
- The demo package is folded in as `examples/`, installed as `lte_launch.examples`
  (`lte-launch lte_launch turtle_demo_launch.py`); `test_examples.py` builds each one.
- Merged branches deleted; only `main`.

## Synchronous launch status

Built and verified: scripts (Q26), and declared checks in a plain launch (Q30). Gap: the
dashboard's rerun is refused for every run; a script's stages *can* restart, so `r` could map
to `stage.restart()` there.

## Branch `engine-lte` (2026-11-07, pushed; not merged to `main`)

The engine is libtmux_easy; the stock-engine code is removed on this branch (`main` keeps it).
Design: `docs/design.md`; stock parity: `docs/parity.md`.

| Area | What |
|---|---|
| libtmux_easy | Released as 0.4.0: `processes.Processes` without pytest (`on_exit`, `env`, `kill_all`, bare windows, per-process `sigint_timeout`) |
| Engine | Parallel teardown and the end sequence; `respawn`, `required`, `shutdown()`, `on_shutdown`, `stop_timeout`, `prefix`, `cwd`, scopes |
| Schema | `@launch` from the annotation, `config=`, or the signature (P2) |
| ROS | `component(into=)` (standalone, ours, elsewhere), lifecycle nodes, `ros_params`/`ros_remaps` scopes |
| Interop | Stock include: one process, its deaths are ours. Stock includes us: one `lte-launch` process |
| Tests | 90 (36 live, `-m "not live"` < 1 s); `colcon test` 91; `examples/lte_test` 8/8 |

Next: rename to `lte_launch` (package `ros-jazzy-lte-launch`); CI; Q25 (re-type substituted
parameters); `docs/parity.md` "Next, by value"; libtmux_easy `.deb` for system Python.

## Open questions

`design-notes.md`: Q5, Q6, Q12, Q14, Q18, Q19, Q20, Q21, Q22, Q23. Deferred: Q7, Q24, units.
