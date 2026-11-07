# lte_launch docs

(Formerly `ros2_typed_launch`, command `tlaunch`. Debian package: `ros-jazzy-lte-launch`.)

Typed, schema-driven launch files for ROS 2 Jazzy. A Pydantic model is a launch's interface.
The launch body is plain Python, run with `lte-launch`.

These docs describe what is built and the direction it is going. Plans in progress and
scratch notes live in [`../working/`](../working/README.md).

## Documents

| File | Contents |
|---|---|
| [goals.md](goals.md) | Goals, principles, the YAML argument, the runtime model (why stock launch is declarative, plan vs script), build vs reuse, proposals P1–P8 |
| [design.md](design.md) | How it works: decisions, a run end to end and its end sequence, modules, processes, `on_shutdown`, composable and lifecycle nodes, parameter layers, stock boundaries, testing |
| [parity.md](parity.md) | Every stock `launch`/`launch_ros` feature and its equivalent here; what is next |
| [releasing.md](releasing.md) | CI jobs, the `ros-jazzy-lte-launch` .deb, rosdep, making a release |
| [comparison.md](comparison.md) | Against stock Python, stock XML/YAML and simple_launch |
| [`../examples/README.md`](../examples/README.md) | Every example with its commands |

## Quick start

```bash
cd ~/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install       # once, and after adding a launch/config file
source install/setup.bash            # lte-launch on PATH, with tab completion

lte-launch lte_launch turtle_demo_launch.py -h       # options from the schema
lte-launch lte_launch turtle_demo_launch.py \
    --config lte_launch:examples/config/blue_turtle.yaml \
    --turtlesim.background_r 99 --show-config           # layering + provenance, no launch
lte-launch lte_launch turtle_bringup_launch.py --dry-run   # the plan, nothing started
lte-launch lte_launch turtle_bringup_launch.py --tmux      # a pane per process
```

## Minimal launch

```python
from pydantic import BaseModel, Field
from lte_launch import launch, namespace, node

class Config(BaseModel):
    namespace: str = "demo"
    background_r: int = Field(0, ge=0, le=255)

@launch
def turtles(config: Config):          # the annotation is the schema
    with namespace(config.namespace):
        sim = node("turtlesim", "turtlesim_node", name="sim",
                   params={"background_r": config.background_r})   # starts now
        sim.wait_for_node()
```

Save it as `<pkg>/launch/turtles_launch.py` (one dot, so it is importable), then run
`lte-launch <pkg> turtles_launch.py --background_r 99`.
