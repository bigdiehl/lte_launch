from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ros2_typed_launch import launch


class RobotConfig(BaseModel):
    """Identity of the robot."""

    name: str = Field("robot", description="Robot name")
    namespace: str = Field("", description="ROS namespace")
    use_sim_time: bool = Field(False, description="Use simulated clock")


class PlannerConfig(BaseModel):
    """Global path planner."""

    algorithm: Literal["navfn", "smac"] = Field("smac", description="Planner plugin")
    tolerance: float = Field(0.5, gt=0, description="Goal tolerance (m)")
    max_iterations: int = Field(1000, ge=1, le=100000, description="Search budget")
    costmap: Optional[Path] = Field(None, description="Costmap YAML override")


class NavConfig(BaseModel):
    """Navigation stack."""

    map: Path = Field(description="Map YAML file")
    robot: RobotConfig = RobotConfig()
    planner: PlannerConfig = PlannerConfig()


@launch(config=NavConfig)
def nav(config):
    return []


generate_launch_description = nav.generate_launch_description
