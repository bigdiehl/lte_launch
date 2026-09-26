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
    - Can include standard launch files. Convert args to supported format
    - Standard launches can include us? Might be harder

Other
    - Method for loading params/remaps from config file
    - Multiple configs, merged together. Showing source from each
    - Cleanup --help
    - Support $(command arg) syntax in config files
    