# working/ -- plans and notes

Temporary working files: plans, open questions, studies, prototypes. Documentation of what is
built lives in [`../docs/`](../docs/README.md) (start there).

## Documents

| File | What it is | Read it when |
|---|---|---|
| `vision.md` | The launch system we would build from scratch: a synchronous engine on libtmux_easy, backends, lte_launch_test compatibility, a possible path | rethinking the engine |
| `PROGRESS.md` | Done, uncommitted, next steps | picking up work |
| `design-notes.md` | Principles, **decided** table, **open questions** (with current thinking), and reference notes (stock `ros2 launch` internals, schema→CLI mapping, Pydantic 1-vs-2, deferred tmux/overrides designs) | deciding what to build next, or checking why something is the way it is |
| `schema-interface-and-input-dict.md` | Study of darts `InputDict`: pros/cons, the pluggable-backend proposal, and the **units plan** (U1–U6) | revisiting units or schema backends |
| `prototypes/` | `nav_demo_launch.py` + `nav.yaml`: a richer sample schema, run by path | trying the CLI on a non-ROS schema |

## Status

`PROGRESS.md` (done, uncommitted, next). Open questions: `design-notes.md`.
