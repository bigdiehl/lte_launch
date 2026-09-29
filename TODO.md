1. Wrap our own config class. Agnostic to backend? Simplify the interface
    - Review using input_dict. What even is that. Would like to incorporate units somehow
    - Simplify the config class creation
    - How does this work if we want to do more fancy stuff? Like validators that run afterward?

2. Support synchronous launching. 
    - E.g. we can launch a process, run some checks, and then proceed. 
    - More deterministic bringup/teardown

In general, supporting some checks would be nice. 
    - Incorporating some basic tests like:
        - Does node exist?
        - Does topic exist?
        - Is topic publishing?

3. Tmux incorporation?
    - Optionally launch all processes in different tmux panes? For debugging?
    - Support tmux layout? 

Eventually I'd like to incorporate some sort of node/launch manager. Framed as a tmux session to manage each launch/node
    - Similar to https://gitlab.iiia.csic.es/socialminds/tools/launch_tui
    - Should make tmux optional. Not everyone likes that. 


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
