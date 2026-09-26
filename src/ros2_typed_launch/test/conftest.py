import sys
import textwrap

import pytest

SAMPLE = '''
from typing import Any, Literal
from pydantic import BaseModel, Field
from ros2_typed_launch import launch


class Robot(BaseModel):
    """Robot identity."""
    name: str = Field("robot", description="Robot name")
    use_sim_time: bool = False


class Planner(BaseModel):
    algorithm: Literal["navfn", "smac"] = "smac"
    tolerance: float = Field(0.5, gt=0)
    max_iterations: int = Field(1000, ge=1, le=100000)


class Cfg(BaseModel):
    """Sample config."""
    map: str
    robot: Robot = Robot()
    planner: Planner = Planner(tolerance=0.9)
    tags: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    remap: dict[str, str] = Field(default_factory=dict)
    robots: list[Robot] = Field(default_factory=list)


@launch(config=Cfg)
def sample(config):
    return []


generate_launch_description = sample.generate_launch_description
'''


@pytest.fixture
def sample_launch(tmp_path):
    path = tmp_path / "sample_launch.py"
    path.write_text(SAMPLE)
    return str(path)


@pytest.fixture
def write(tmp_path):
    """write("name.yaml", "text") -> path"""

    def _write(name, text):
        path = tmp_path / name
        path.write_text(textwrap.dedent(text))
        return str(path)

    return _write


# A launch that pulls in another module of the same fake package with a
# relative import -- the pattern `turtle_demo_launch.py` uses for real.
CHILD_MODULE = '''
from pydantic import BaseModel
from ros2_typed_launch import launch


class ChildConfig(BaseModel):
    n: int = 1


@launch(config=ChildConfig)
def child(config):
    return ["child-%d" % config.n]

generate_launch_description = child.generate_launch_description
'''

MAIN_MODULE = '''
from pydantic import BaseModel
from ros2_typed_launch import include, launch
from .child_launch import ChildConfig, child


class MainConfig(BaseModel):
    n: int = 5


@launch(config=MainConfig)
def main(config):
    return [include(child, ChildConfig(n=config.n))]

generate_launch_description = main.generate_launch_description
'''

# Has generate_launch_description, like a stock (untyped) launch file -- but
# no .typed_launch tag, so it is the "not a typed launch" case, not the
# "doesn't look like a launch at all" case.
NOT_A_LAUNCH_MODULE = "def generate_launch_description():\n    return None\n"


@pytest.fixture
def fake_package(tmp_path, monkeypatch):
    """An importable package "tpkg" on sys.path, standing in for what an
    installed package looks like once install/setup.bash has put it on
    PYTHONPATH: `tpkg.main_launch` imports `tpkg.child_launch` with a relative
    import. Also has a nested `tpkg.launch` subpackage (mirroring the real
    demo's top-level launch/ directory, built with ament_cmake -- see
    working/design-notes.md), so both dotted (`launch.nested_launch`)
    and on-disk-style (`launch/nested_launch.py`) addressing can be tested."""
    pkg = tmp_path / "tpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "child_launch.py").write_text(CHILD_MODULE)
    (pkg / "main_launch.py").write_text(MAIN_MODULE)
    (pkg / "not_a_launch.py").write_text(NOT_A_LAUNCH_MODULE)
    sub = pkg / "launch"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (sub / "nested_launch.py").write_text(CHILD_MODULE)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield "tpkg"
    for name in list(sys.modules):
        if name == "tpkg" or name.startswith("tpkg."):
            del sys.modules[name]
