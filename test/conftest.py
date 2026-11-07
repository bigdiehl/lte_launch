import contextlib
import os
import re
import signal
import subprocess
import sys
import textwrap

import pytest

from _live import Probe, counter, need_ros


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: starts real processes (plain, ROS nodes, tlaunch itself); "
        'deselect with -m "not live"',
    )


# --------------------------------------------------------------------------
# live tests: real processes (and ROS nodes); helpers in _live.py
# --------------------------------------------------------------------------
@pytest.fixture
def uniq(request):
    """A name no other test (or concurrent run) uses: a ROS name token."""
    base = re.sub(r"[^A-Za-z0-9]", "_", request.node.name)[-16:].strip("_")
    return f"t{os.getpid()}_{next(counter)}_{base}".lower()


@pytest.fixture
def make_run(tmp_path):
    """make_run(label="t", **Run kwargs) -> an engine.Run on a fresh
    libtmux_easy Processes group (subprocesses); torn down at the end even
    if the test fails."""
    processes = pytest.importorskip("libtmux_easy.processes")
    from ros2_typed_launch import engine

    runs = []

    def make(label="t", **kwargs):
        d = tmp_path / f"run{len(runs)}"
        procs = processes.Processes(label, log_dir=str(d), prefix="")
        kwargs.setdefault("params_dir", str(d / "params"))
        run = engine.Run(procs, label=label, **kwargs)
        runs.append(run)
        return run

    yield make
    for run in runs:
        run.teardown()


@pytest.fixture
def probe():
    need_ros()
    p = Probe()
    yield p
    p.destroy()


@pytest.fixture
def spawn():
    """spawn(argv, **Popen kwargs) -> a Popen in a session of its own, text
    output piped; stopped (SIGINT, then SIGKILL) at the end of the test."""
    started = []

    def _spawn(argv, **kwargs):
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.STDOUT)
        kwargs.setdefault("text", True)
        proc = subprocess.Popen(argv, start_new_session=True, **kwargs)
        started.append(proc)
        return proc

    yield _spawn
    for proc in started:
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGINT)
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(5)


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
    pass


generate_launch_description = sample.generate_launch_description
'''


@pytest.fixture
def sample_launch(tmp_path):
    path = tmp_path / "sample_launch.py"
    path.write_text(SAMPLE)
    return str(path)


@pytest.fixture
def write(tmp_path):
    """write("name.yaml", "map: m\\n") -> path

    The text is the launch's own settings; it gets nested under ``launch_args:``
    for you, since that is where every schema value lives now. Pass
    ``raw=True`` to write the file exactly as given -- for node-pattern
    sections, or to test the file shape itself.
    """

    def _write(name, text, raw=False):
        path = tmp_path / name
        text = textwrap.dedent(text)
        if not raw:
            text = "launch_args:\n" + textwrap.indent(text, "  ")
        path.write_text(text)
        return str(path)

    return _write


# A launch that pulls in another module of the same fake package with a
# relative import -- the pattern `turtle_demo_launch.py` uses for real.
CHILD_MODULE = """
from pydantic import BaseModel
from ros2_typed_launch import launch, process


class ChildConfig(BaseModel):
    n: int = 1


@launch(config=ChildConfig)
def child(config):
    process("child-%d" % config.n, "true")

generate_launch_description = child.generate_launch_description
"""

MAIN_MODULE = """
from pydantic import BaseModel
from ros2_typed_launch import include, launch
from .child_launch import ChildConfig, child


class MainConfig(BaseModel):
    n: int = 5


@launch(config=MainConfig)
def main(config):
    include(child, ChildConfig(n=config.n))

generate_launch_description = main.generate_launch_description
"""

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
