# Progress

Branch `explore/synchronous-launch`, 5 commits ahead of `main`. Last updated 2026-10-11.
Design: `DESIGN.md`. Decisions and open questions: `design-notes.md`.

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
| Dashboard | `--tmux-dash`, `--tmux-logs`, borrowed keys, `./run.sh` demo | Q29 |

## Uncommitted (2026-10-11)

- All tmux/dashboard work (Q28, Q29): `tmux.py`, `dashboard.py`, `tlaunch-pane`, tests, demo.
- **Pane output reaches launch again**: `lte-run --tee` into a FIFO the shim relays; log files, death
  report and `wait_output()` work for a process in a pane. Verified with
  `turtle_bringup_script.py --tmux`.
- Refactor pass: `pane_rows`, `need`, `log`, `env_args`; `TMUX_FLAGS`; one error path in
  `panes_for`.
- Tests consolidated: 189 → 59 (60 with stock interop), every assertion kept (`test_subst.py` merged into
  `test_config.py`).
- **Stock includes typed** (Q17): `stock.py`, `cli.configure`, CMake
  `ros2_typed_launch_install_stubs`, demo stubs + `stock_parent.launch.xml`. Verified with Python
  and XML parents and `ros2 launch` direct.
- Not for commit: `load_config.py`; the user's edits to `params_demo.yaml` and
  `localization_launch.py`.

## Synchronous launch status

Built and verified (Q26, `turtle_bringup_script.py`). Gaps:

- Checks (`wait_for_node`/`wait_for_topic`) exist only inside a script; a plain launch cannot
  declare them.
- The dashboard's rerun is refused for every run. A script's stages *can* restart, so `r`
  could map to `stage.restart()` there.

## Next (suggested order)

1. Commit the tmux/dashboard and stock-interop work.
2. Declarative checks in a plain launch (TODO 2).
3. Dashboard rerun for script stages.
4. Small: `--help` cleanup, log verbosity flag, log final command lines (`--print`).
5. Fix the `chatter` demo's Ctrl-C exit (-2), or strip the stand-in from launch's own
   "process has died" line.

## Open questions

`design-notes.md`: Q5, Q6, Q12, Q14, Q18, Q19, Q20, Q21, Q22, Q23. Deferred: Q7, Q24, units.
