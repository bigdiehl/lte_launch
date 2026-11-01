1. Wrap our own config class. Agnostic to backend? Simplify the interface
    - Review using input_dict. What even is that. Would like to incorporate units somehow
    - Simplify the config class creation
    - How does this work if we want to do more fancy stuff? Like validators that run afterward?

2. [DONE] Support synchronous launching.
    - E.g. we can launch a process, run some checks, and then proceed.
    - More deterministic bringup/teardown
      -- @script + Session: stages, waits, per-stage stop/restart; see design-notes Q26
         and turtle_bringup_script.py

In general, supporting some checks would be nice.
    - Incorporating some basic tests like:
        - [DONE] Does node exist?        -- session.wait_for_node / node_names()
        - [DONE] Does topic exist?       -- session.wait_for_topic
        - [DONE] Is topic publishing?    -- session.wait_for_topic(publishers=N)
    - Still missing: these are only available inside a script, not as checks a
      plain launch declares.

3. [DONE] Tmux incorporation.
    - [DONE] Optionally launch all processes in different tmux panes? For debugging?
      -- --tmux (all) / --tmux-only GLOB (some); window(name, actions) groups them,
         e.g. an include into its own window. Launch still owns every process, so
         exit codes and the death report are unchanged, and a pane is a real TTY
         (turtle_teleop_key works). See design-notes Q28, tmux_demo_launch.py.
    - [DONE] Support tmux layout?
      -- tiled, re-applied as panes appear; --tmux-session NAME to pick the session.
    - [DONE] A process in a pane still reaches launch's log, the death report and
      Process.wait_output() (lte-run --tee; Q28).
    - Still missing: nested tlaunch includes are not expanded.

Eventually I'd like to incorporate some sort of node/launch manager. Framed as a tmux session to manage each launch/node
    - [DONE] Similar to https://gitlab.iiia.csic.es/socialminds/tools/launch_tui
      -- --tmux-dash (libtmux_easy's lte-dash: per-pane status, stop, Q, home =
         this run's output) and --tmux-logs (ros2-log-view window, prefix v).
         See design-notes Q29, ./run.sh. Rerun is refused: launch will not
         execute an action twice.
    - [DONE] Should make tmux optional. Not everyone likes that.
      -- every tmux flag is opt-in; without them a run is plain launch.


4. Interface with traditional ROS2 launch
    - [DONE] Can include standard launch files. Convert args to supported format
      -- actions.stock_args + stock_include_launch.py; see design-notes.md Q17
    - Standard launches can include us? Might be harder
      -- still open, and still looks not worth it: typed files reject stock `ros2 launch`

Other
    - [DONE] Method for loading params/remaps from config file
      -- ros__parameters / ros__remappings under node patterns, via node(); Q8
    - [DONE] Multiple configs, merged together. Showing source from each
      -- --show-config prints every value with its source
    - Cleanup --help
    - [DONE] Support $(command arg) syntax in config files
      -- and find-pkg-share/find-pkg-prefix/env/eval, via the stock launch parser; Q25


Setting of log verbosity level? (e.g. to debug?)

log all final commands, remappings, parameters
    - [PARTLY] parameters/remappings and the whole effective config are written to
      /tmp/tlaunch/<launch>-<pid>.yaml on every run, and the path is logged.
      Still missing: the final process command lines (stock launch's --print).
