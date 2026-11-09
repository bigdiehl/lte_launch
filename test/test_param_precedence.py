"""Which value a node ends up with when param_files=, the generated params
file (params= and config sections) and ros_arguments=["-p", ...] set the
same parameter, on a real turtlesim, read back with `ros2 param get`.

rcl's rule (rcl_yaml_param_parser, the same for rclcpp and rclpy): every
--params-file and -p goes, in command-line order, into one table keyed by
node name ("/**" for -p and for our generated file); a later value replaces
an earlier one under the same key, and the node then applies each key that
matches it in the order the key *first appeared*. node_command emits
param_files, then the generated file, then ros_arguments, so for /** keys:
param_files < params=/config < -p."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from _live import need_ros, until
from lte_launch import engine, namespace, node

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _ros():
    need_ros("turtlesim")
    if not shutil.which("ros2"):
        pytest.skip("ros2 not on PATH")


def param_get(fqn: str, name: str) -> int:
    """`ros2 param get --no-daemon`, retried until the node answers."""

    def get():
        p = subprocess.run(["ros2", "param", "get", "--no-daemon", fqn, name],
                           capture_output=True, text=True, timeout=20)  # fmt: skip
        m = re.search(r"Integer value is: (-?\d+)", p.stdout)
        return m and int(m.group(1)) + 1  # truthy even for 0

    return until(get, 30, f"{fqn} {name}") - 1


def files(tmp_path, **texts) -> dict[str, str]:
    for name, text in texts.items():
        (tmp_path / f"{name}.yaml").write_text(text)
    return {name: str(tmp_path / f"{name}.yaml") for name in texts}


def test_param_files_then_generated_then_ros_arguments(make_run, probe, uniq, tmp_path):
    ns = f"/{uniq}"
    f = files(
        tmp_path,
        wild="/**:\n  ros__parameters:\n"
        "    {background_r: 1, background_g: 1, background_b: 1}\n",
        # Only this node's own name, and before any /**: still under ours.
        exact=f"{ns}/b:\n  ros__parameters: {{background_g: 5, background_b: 5}}\n",
    )
    run = make_run()
    with engine.active(run), namespace(uniq):
        a = node(
            "turtlesim", "turtlesim_node", name="a",
            param_files=[f["wild"]],
            params={"background_r": 2, "background_g": 2},
            ros_arguments=["-p", "background_r:=3"],
        )  # fmt: skip
        b = node("turtlesim", "turtlesim_node", name="b",
                 param_files=[f["exact"]], params={"background_g": 2})  # fmt: skip
        # The order on the command line is what decides.
        argv = a.argv
        files_at = [i for i, x in enumerate(argv) if x == "--params-file"]
        assert argv[files_at[0] + 1] == f["wild"]
        assert argv[files_at[1] + 1].endswith(".params.yaml")  # generated
        assert files_at[1] < argv.index("-p") == len(argv) - 2

        assert param_get(a.fqn, "background_r") == 3  # -p, last
        assert param_get(a.fqn, "background_g") == 2  # params=, over param_files
        assert param_get(a.fqn, "background_b") == 1  # param_files alone
        b.wait_for_node(20)  # the probe's GetParameters: quicker than the CLI
        assert probe.param(b.fqn, "background_g") == 2  # params= (/**, applied later)
        assert probe.param(b.fqn, "background_b") == 5
    run.teardown()
    assert run.deaths == []


@pytest.mark.xfail(
    reason="rcl applies node keys in first-appearance order: a param_files "
    "entry under the node's own name, after a /** section in the same (or an "
    "earlier) file, is applied after our generated /** file, so it beats "
    "params=/config values (and -p). Not fixable by the key we write alone; "
    "see docs/design.md, parameter layers",
    strict=True,
)
def test_params_beat_a_param_files_entry_under_the_nodes_own_name(
    make_run, probe, uniq, tmp_path
):
    ns = f"/{uniq}"
    f = files(
        tmp_path,
        both="/**:\n  ros__parameters: {background_b: 1}\n"
        f"{ns}/c:\n  ros__parameters: {{background_g: 5}}\n",
    )
    run = make_run()
    try:
        with engine.active(run), namespace(uniq):
            c = node("turtlesim", "turtlesim_node", name="c",
                     param_files=[f["both"]], params={"background_g": 2})  # fmt: skip
            c.wait_for_node(20)
            assert probe.param(c.fqn, "background_g") == 2  # it is 5
    finally:
        run.teardown()
