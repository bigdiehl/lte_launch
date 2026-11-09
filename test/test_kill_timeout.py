"""kill_timeout= and --kill-timeout: SIGTERM's grace before SIGKILL (stock's
sigkill_timeout), live on plain processes (libtmux_easy 0.5.0; no ROS)."""

from __future__ import annotations

import time

import pytest
from pydantic import BaseModel

from lte_launch import engine, launch, process, shutdown
from _live import PY

#: ignores SIGINT and SIGTERM: only SIGKILL ends it
DEAFER = (f"{PY} -c \"import signal, time; print('up', flush=True); "
          "signal.signal(signal.SIGINT, signal.SIG_IGN); "
          "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)\"")  # fmt: skip


def _processes():
    """libtmux_easy's Processes, or a skip if it has no SIGTERM grace."""
    processes = pytest.importorskip("libtmux_easy.processes")
    if not engine.supports_kill_timeout(processes.Processes):
        pytest.skip("libtmux_easy < 0.5.0: no Processes(sigterm_timeout=)")
    return processes.Processes


class C(BaseModel):
    x: int = 1


@launch
def deafer(config: C):
    process("own", DEAFER, ready="up", stop_timeout=0.2, kill_timeout=0.3)
    process("group", DEAFER, ready="up", stop_timeout=0.2)  # the run's
    shutdown()


@pytest.mark.live
def test_kill_timeout(tmp_path):
    Processes = _processes()
    procs = Processes("kt", log_dir=str(tmp_path), prefix="", sigterm_timeout=0.5)
    run = engine.Run(procs, label="kt", params_dir=str(tmp_path))
    t = time.monotonic()
    assert engine.execute(deafer, C(), run) == 0
    took = time.monotonic() - t
    # all at once, each on its own deadlines: not 5 s of SIGTERM grace
    assert took < 3, took
    own, group = run.started
    assert own.kill_timeout == 0.3 and group.kill_timeout is None
    assert procs.status("kt:own").stopped == "SIGKILL"
    assert procs.status("kt:group").stopped == "SIGKILL"


LAUNCH_FILE = f"""
from pydantic import BaseModel
from lte_launch import launch, process, shutdown

class C(BaseModel):
    x: int = 1

@launch
def deafer(config: C):
    process("deafer", {DEAFER!r}, ready="up")
    shutdown("bye")
"""


@pytest.mark.live
def test_kill_timeout_flag(tmp_path, capsys):
    _processes()
    from lte_launch import cli

    assert "--kill-timeout" in cli.RESERVED
    path = tmp_path / "deafer_launch.py"
    path.write_text(LAUNCH_FILE)
    t = time.monotonic()
    argv = [str(path), "--stop-timeout", "0.2", "--kill-timeout", "0.3"]
    assert cli.main(argv) == 0
    assert time.monotonic() - t < 4
    assert "shutdown: bye" in capsys.readouterr().out


class OldProcesses:
    """libtmux_easy 0.4.0's start(): no sigterm_timeout."""

    on_exit = None

    def start(self, name, command, ready=None, timeout=30.0, fail_on=(),
              exits=None, stop_codes=None, env=None, sigint_timeout=None):
        raise AssertionError("not reached")  # fmt: skip


def test_kill_timeout_old_libtmux_easy(tmp_path, capsys, monkeypatch):
    """An old libtmux_easy: a clear error, only when kill_timeout is asked for."""
    assert not engine.supports_kill_timeout(OldProcesses)
    assert not engine.supports_kill_timeout(None)
    run = engine.Run(OldProcesses(), label="old", params_dir=str(tmp_path))
    with pytest.raises(engine.LaunchError, match=r"own: kill_timeout= needs "
                       r"libtmux_easy 0\.5\.0"):  # fmt: skip
        run.start("own", "true", kill_timeout=1.0)
    assert run.started == []

    pytest.importorskip("libtmux_easy.processes")
    from lte_launch import cli

    monkeypatch.setattr(engine, "supports_kill_timeout", lambda procs: False)
    path = tmp_path / "deafer_launch.py"
    path.write_text(LAUNCH_FILE)
    assert cli.main([str(path), "--kill-timeout", "1"]) == 2
    err = capsys.readouterr().err
    assert "--kill-timeout needs libtmux_easy 0.5.0" in err
    # a dry run needs no libtmux_easy at all
    assert cli.main([str(path), "--kill-timeout", "1", "--dry-run"]) == 0
