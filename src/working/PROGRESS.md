# Progress

All work is on `main` (`explore/synchronous-launch` merged 2026-11-01). Last updated 2026-11-01.
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

## Synchronous launch status

Built and verified: scripts (Q26), and declared checks in a plain launch (Q30). Gap: the
dashboard's rerun is refused for every run; a script's stages *can* restart, so `r` could map
to `stage.restart()` there.

## Next (suggested order)

1. Dashboard rerun for script stages.
2. Fix the `chatter` demo's Ctrl-C exit (-2), or strip the stand-in from launch's own
   "process has died" line.

## Open questions

`design-notes.md`: Q5, Q6, Q12, Q14, Q18, Q19, Q20, Q21, Q22, Q23. Deferred: Q7, Q24, units.
