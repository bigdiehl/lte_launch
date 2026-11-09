"""lte-launch end to end, as a subprocess: exit codes 0/1/2, the ready line,
the effective config file every run writes, --dry-run, Ctrl-C and SIGTERM,
--tmux, and the bringup example against a real turtlesim."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import textwrap

import pytest
import yaml

from _live import Reader, need_ros, until

pytestmark = pytest.mark.live

LAUNCH = """
from pydantic import BaseModel, Field
from lte_launch import launch, node, process


class E2EConfig(BaseModel):
    mode: str = "finish"
    count: int = Field(1, ge=1)


@launch
def e2e(config: E2EConfig):
    if config.mode == "finish":
        process("main", "echo hello from main; sleep 0.2", required=True)
    elif config.mode == "die":
        process("main", "sleep 0.2; exit 3")
    elif config.mode == "serve":
        process("serve", "echo serving; sleep 60")
    elif config.mode == "raise":
        raise RuntimeError("boom in the body")
    elif config.mode == "typo":
        node("turtlesim", "no_such_node")
"""


@pytest.fixture
def e2e(tmp_path, monkeypatch):
    monkeypatch.setenv("ROS_LOG_DIR", str(tmp_path / "log"))  # run_cli inherits it
    if not shutil.which("lte-launch"):
        pytest.skip("lte-launch not on PATH (source the workspace's install/setup.bash)")
    pytest.importorskip("libtmux_easy.processes")
    path = tmp_path / "e2e_launch.py"
    path.write_text(textwrap.dedent(LAUNCH))
    return str(path)


def run_cli(*args, timeout=60):
    """-> (exit code, stdout, stderr). Inherits the environment
    (ROS_DOMAIN_ID, PYTHONPATH)."""
    p = subprocess.run(["lte-launch", *args], capture_output=True, text=True,
                       timeout=timeout, stdin=subprocess.DEVNULL)  # fmt: skip
    return p.returncode, p.stdout, p.stderr


def test_exit_codes_and_what_a_run_leaves(e2e, tmp_path):
    # 0: the required process ended; the ready line, its output, the files.
    code, out, err = run_cli(e2e, "--mode", "finish")
    assert code == 0, out + err
    assert "[INFO] [lte-launch]: up: 1 process(es) running" in out
    assert "hello from main" in out
    assert "shutdown: e2e:main (required) ended" in out

    saved = re.search(r"effective config written to (\S+)", out).group(1)
    assert yaml.safe_load(open(saved))["launch_args"] == {"mode": "finish", "count": 1}
    logs = re.search(r"\]: logs: (\S+)", out).group(1)
    root = tmp_path / "log" / "lte-launch"
    assert os.path.dirname(logs) == str(root) and saved == f"{logs}/config.yaml"
    assert os.path.realpath(root / "latest") == logs  # as stock's latest
    files = sorted(os.listdir(logs))
    assert "launch.log" in files
    # launch.log is the whole transcript: our lines and the echoed output
    launch_log = open(os.path.join(logs, "launch.log")).read()
    assert "[lte-launch]: up: 1 process(es)" in launch_log
    assert "hello from main" in launch_log and "logs: " in launch_log
    # ... and each process has its own
    own = [f for f in files if f.endswith(".log") and f != "launch.log"]
    assert "hello from main" in "".join(open(os.path.join(logs, f)).read() for f in own)
    # ... and the effective config is a valid --config: it round-trips.
    code, shown, err = run_cli(e2e, "--config", saved, "--show-config")
    assert code == 0 and re.search(r'mode: "?finish"? +# \S+/config\.yaml', shown), (
        shown + err
    )
    # a dry run leaves no log dir behind
    before = set(os.listdir(root))
    assert run_cli(e2e, "--dry-run")[0] == 0 and set(os.listdir(root)) == before

    # 1: a death after up; a body that raises; a typo'd executable.
    code, out, _ = run_cli(e2e, "--mode", "die")
    assert code == 1 and "e2e:main died unexpectedly: exit code 3" in out
    code, out, err = run_cli(e2e, "--mode", "raise")
    assert code == 1 and "RuntimeError: boom in the body" in out + err
    if shutil.which("ros2"):
        code, out, _ = run_cli(e2e, "--mode", "typo")
        assert (
            code == 1 and "no executable 'no_such_node' in package 'turtlesim'" in out
        )

    # 2: bad config, before anything starts (no up line, no config written).
    code, out, err = run_cli(e2e, "--count", "0")
    assert code == 2 and "configuration error" in err and "--count" in err
    assert "up:" not in out and "effective config" not in out
    bad = tmp_path / "bad.yaml"
    bad.write_text("launch_args:\n  mood: x\n")
    code, _, err = run_cli(e2e, "--config", str(bad))
    assert code == 2 and "mood" in err
    code, _, err = run_cli(e2e, "--log-level", "loud")
    assert code == 2 and "LOGGER:=LEVEL" in err

    # --dry-run: the plan, nothing started, nothing written.
    code, out, _ = run_cli(e2e, "--mode", "serve", "--dry-run")
    assert code == 0 and "dry run, 1 process(es):" in out and "e2e:serve" in out
    assert "up:" not in out and "effective config" not in out


def test_ctrl_c_and_sigterm(e2e, spawn):
    for sig in (signal.SIGINT, signal.SIGTERM):
        out = Reader(
            spawn(["lte-launch", e2e, "--mode", "serve"], stdin=subprocess.DEVNULL)
        )
        out.wait_for(r"\[lte-launch\]: up: 1 process")
        out.wait_for("serving")
        out.proc.send_signal(sig)
        assert out.finish(20) == 0, out.text
        assert "interrupted; stopping" in out.text


def test_tmux(e2e):
    if not (shutil.which("tmux") and shutil.which("lte-run")):
        pytest.skip("--tmux needs tmux and libtmux_easy's lte-run on PATH")
    name = f"tl-e2e-{os.getpid()}"

    def exists(session):
        return subprocess.run(["tmux", "has-session", "-t", session],
                              capture_output=True).returncode == 0  # fmt: skip

    try:
        # A clean run: its own session, killed at the end.
        code, out, err = run_cli(e2e, "--mode", "finish", "--tmux-session", name)
        assert code == 0, out + err
        assert f"tmux session: tmux attach -t {name}" in out
        assert "shutdown: e2e:main (required) ended" in out and not exists(name)

        # A failed one: kept to look at.
        code, out, _ = run_cli(e2e, "--mode", "die", "--tmux-session", name + "-k")
        assert code == 1 and "died unexpectedly: exit code 3" in out
        assert f"tmux session kept: tmux attach -t {name}-k" in out and exists(
            name + "-k"
        )
    finally:
        for session in (name, name + "-k"):
            subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)


def test_the_bringup_example(spawn, probe, uniq):
    need_ros("turtlesim", "lte_launch")
    pytest.importorskip("libtmux_easy.processes")  # lte-launch needs it
    if not shutil.which("lte-launch"):
        pytest.skip("lte-launch not on PATH")
    target = ["lte_launch", "turtle_bringup_launch.py", "--namespace", uniq]
    out = Reader(spawn(["lte-launch", *target, "--turtlesim.background_r", "33"],
                       stdin=subprocess.DEVNULL))  # fmt: skip
    out.wait_for(r"\[lte-launch\]: up: 2 process", 30)
    assert probe.param(f"/{uniq}/turtlesim", "background_r") == 33
    # the driver: graph discovery can trail its "publishing" line
    until(lambda: probe.publishers(f"/{uniq}/turtle1/cmd_vel") >= 1, 10, "the driver")
    out.proc.send_signal(signal.SIGINT)
    assert out.finish(20) == 0, out.text

    # --crash_after: the helper's death fails the run.
    code, text, _ = run_cli(*target, "--no-drive", "--crash_after", "0.5")
    assert code == 1 and "flaky died unexpectedly: exit code 3" in text
