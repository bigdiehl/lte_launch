"""Supervision, live, on plain processes (no ROS): deaths, exits=, required,
shutdown(), respawn, Ctrl-C, and how a process is started (cwd=, env=,
environment(), --launch-prefix, --log-commands). Through engine.execute,
the whole of a run: bringup, the ready line, supervise, teardown."""

from __future__ import annotations

import os
import signal
import time

import pytest
from pydantic import BaseModel

from _live import DEAF, PY, QUIET, later, until
from ros2_typed_launch import environment, launch, process, shutdown, sleep, window
from ros2_typed_launch import engine

pytestmark = pytest.mark.live



class Mode(BaseModel):
    mode: str = ""


def execute(make_run, body, mode="", **run_args):
    """Run ``body`` as a launch; -> (exit code, seconds, run)."""
    target = launch(config=Mode)(body)
    run = make_run(**run_args)
    t = time.monotonic()
    code = engine.execute(target, Mode(mode=mode), run)
    return code, time.monotonic() - t, run


def test_exit_codes_and_deaths(make_run, capsys):
    reached = []

    # A death during bringup fails the wait the body is in; the rest of the
    # body never runs, and the run exits 1 at once (not after the sleep).
    def bringup(config):
        process("ok", QUIET, ready="up")
        process("bad", "echo oops; exit 5")
        sleep(20)
        reached.append("after")

    code, took, run = execute(make_run, bringup)
    out = capsys.readouterr().out
    assert code == 1 and took < 5 and reached == []
    assert "t:bad died unexpectedly: exit code 5" in out and "| oops" in out
    assert "1 process(es) died unexpectedly" in out and "  t:bad: exit code 5" in out
    assert "up:" not in out  # never got there
    assert not any(p.running for p in run.started)

    # A death after the body returned: up first, then the death, exit 1.
    def after_up(config):
        process("ok", QUIET, ready="up")
        process("late", "sleep 0.5; exit 4")

    code, _, run = execute(make_run, after_up)
    out = capsys.readouterr().out
    assert code == 1
    assert out.index("up: 2 process(es) running") < out.index(
        "t:late died unexpectedly"
    )
    assert [p.key for p in run.deaths] == ["t:late"]

    # exits=: codes a process may end with; finishing is not a death. With
    # every process done the run ends on its own, exit 0.
    def finishing(config):
        process("three", "exit 3", exits=(0, 3))
        process("zero", "exit 0")
        process("int", "exit 7", exits=7)

    code, _, run = execute(make_run, finishing)
    out = capsys.readouterr().out
    assert code == 0 and run.deaths == []
    assert (
        "t:three finished (exit code 3)" in out and "every process has finished" in out
    )

    # exits=None: any end at all is a death, even 0.
    code, _, run = execute(make_run, lambda c: process("any", "exit 0", exits=None))
    assert (
        code == 1 and "t:any died unexpectedly: exit code 0" in capsys.readouterr().out
    )

    # Killed by a signal: reported as such.
    code, _, run = execute(make_run, lambda c: process("sig", "kill -9 $$"))
    assert code == 1 and "killed by SIGKILL" in capsys.readouterr().out
    assert run.started[0].returncode == -9


def test_required_and_shutdown(make_run, capsys):
    # required: its clean end ends the run, exit 0; the others are stopped.
    def clean(config):
        process("other", QUIET, ready="up")
        process("main", "sleep 0.3", required=True)

    code, took, run = execute(make_run, clean)
    out = capsys.readouterr().out
    assert code == 0 and took < 5
    assert "shutdown: t:main (required) ended" in out
    assert not run.started[0].running

    # required and a bad code: a death, exit 1.
    code, _, run = execute(
        make_run, lambda c: process("main", "sleep 0.2; exit 2", required=True)
    )
    assert code == 1 and "t:main died unexpectedly" in capsys.readouterr().out

    # shutdown(reason) in the body: the rest of the body is skipped, exit 0.
    reached = []

    def body(config):
        process("a", QUIET, ready="up")
        shutdown("enough")
        reached.append(1)

    code, _, run = execute(make_run, body)
    out = capsys.readouterr().out
    assert code == 0 and reached == [] and "shutdown: enough" in out
    assert not run.started[0].running


def test_respawn(make_run, tmp_path, capsys):
    # respawn=True: at once; respawn_max: that many times; a clean end after
    # the last one is just a finish (no death), so the run ends exit 0.
    def again(config):
        process("again", "echo run; exit 0", respawn=True, respawn_max=2)

    code, _, run = execute(make_run, again)
    out = capsys.readouterr().out
    proc = run.started[0]
    assert code == 0 and proc.respawns == 2 and run.deaths == []
    assert (
        out.count("respawning in 0 s") == 2 and "t:again finished (exit code 0)" in out
    )

    # respawn_max exhausted on a bad code: that end is a death, exit 1.
    def flaky(config):
        process("flaky", "exit 1", respawn=0.05, respawn_max=1)

    code, _, run = execute(make_run, flaky)
    out = capsys.readouterr().out
    assert code == 1 and run.started[0].respawns == 1
    assert "t:flaky died unexpectedly: exit code 1" in out

    # The delay is honoured: two starts at least 0.5 s apart. (output() is
    # the latest run's only, so the starts go to a file.)
    stamps = tmp_path / "stamps"

    def delayed(config):
        p = process("d", f"date +%s.%N >> {stamps}", respawn=0.5, respawn_max=1)
        until(lambda: p.respawns == 1 and not p.pending, 5, "the respawn")

    code, _, run = execute(make_run, delayed)
    first, second = map(float, stamps.read_text().split())
    assert code == 0 and second - first >= 0.45, (first, second)
    capsys.readouterr()

    # No respawn while tearing down: stopped by teardown is not an end to
    # respawn after.
    def forever(config):
        process("svc", QUIET, ready="up", respawn=0.0)
        shutdown("done")

    code, _, run = execute(make_run, forever)
    time.sleep(0.3)
    proc = run.started[0]
    assert code == 0 and proc.respawns == 0 and not proc.running and not proc.pending
    assert "respawning" not in capsys.readouterr().out


def test_ctrl_c(make_run, capsys):
    pid = os.getpid()

    # One Ctrl-C: a clean stop, exit 0, everything stopped.
    def body(config):
        process("a", QUIET, ready="up")
        later(0.3, os.kill, pid, signal.SIGINT)

    code, took, run = execute(make_run, body)
    out = capsys.readouterr().out
    assert code == 0 and took < 5
    assert "up: 1 process(es) running" in out and "interrupted; stopping" in out
    assert not run.started[0].running

    # Ctrl-C during the body (a wait) is the same.
    def in_wait(config):
        later(0.3, os.kill, pid, signal.SIGINT)
        sleep(20)

    code, took, _ = execute(make_run, in_wait)
    assert code == 0 and took < 5 and "interrupted" in capsys.readouterr().out

    # A second Ctrl-C while stopping kills: a process ignoring SIGINT would
    # otherwise hold teardown for the 10 s SIGINT timeout.
    def stubborn(config):
        process("stubborn", DEAF, ready="up")
        later(0.3, os.kill, pid, signal.SIGINT)
        later(1.5, os.kill, pid, signal.SIGINT)

    code, took, run = execute(make_run, stubborn)
    out = capsys.readouterr().out
    assert "second interrupt: killing everything" in out
    assert code == 0 and took < 6, took
    assert not run.started[0].running and run.started[0].returncode == -9

    # SIGTERM (`kill`, a supervisor) is a Ctrl-C too.
    def term(config):
        process("a", QUIET, ready="up")
        later(0.3, os.kill, pid, signal.SIGTERM)

    code, _, _ = execute(make_run, term)
    assert code == 0 and "interrupted" in capsys.readouterr().out
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_how_a_process_is_started(make_run, tmp_path, capsys):
    (tmp_path / "here").mkdir()

    run = make_run(
        launch_prefix="env TL_PREFIXED=1",
        launch_prefix_filter="^sh$",
        log_commands=True,
    )
    seen = {}

    def capture(config):
        procs = {}
        procs["cwd"] = process("cwd", "pwd", cwd=str(tmp_path / "here"))
        with environment(A="outer", B=1):
            procs["env1"] = process("env1", "echo A=$A B=$B C=$C", env={"C": "own"})
            with environment(A="inner"), window("w"):
                procs["env2"] = process("env2", "echo A=$A B=$B")
        procs["env3"] = process("env3", "echo A=${A:-unset} PYU=$PYTHONUNBUFFERED")
        procs["pre"] = process("pre", ["sh", "-c", "echo P=$TL_PREFIXED"])
        procs["nopre"] = process(
            "nopre",
            [PY, "-c", "import os; print('P=' + os.environ.get('TL_PREFIXED', ''))"],
        )
        for p in procs.values():
            p.wait_for_exit(10)
        seen.update({k: (p.output().strip(), p.window) for k, p in procs.items()})

    code = engine.execute(launch(config=Mode)(capture), Mode(), run)
    out = capsys.readouterr().out
    assert code == 0, out
    assert seen["cwd"][0] == str(tmp_path / "here")
    assert seen["env1"][0] == "A=outer B=1 C=own"
    assert seen["env2"] == ("A=inner B=1", "w")  # inner scope wins; window("w")
    assert seen["env3"][0] == "A=unset PYU=1"  # the scope ended; BASE_ENV always
    assert seen["pre"][0] == "P=1" and seen["nopre"][0] == "P="
    assert seen["env1"][1] == "t"  # no namespace, no window(): the label
    # --log-commands: each command line as it starts, the prefix included
    assert f"t:cwd: cd {tmp_path / 'here'} && pwd" in out
    assert "t:pre: env TL_PREFIXED=1 sh -c 'echo P=$TL_PREFIXED'" in out


class Two(BaseModel):
    b_code: int = 0


@launch
def two(config: Two):
    process("a", QUIET, ready="up")
    process("b", f"exit {config.b_code}", exits=config.b_code)


def test_start_on_a_test_group(tmp_path):
    """target.start(procs): lte_launch_test's way. The body's processes are
    the group's; once the body returns the group's owner watches them."""
    processes = pytest.importorskip("libtmux_easy.processes")
    heard = []
    procs = processes.Processes("g", log_dir=str(tmp_path), prefix="",
                                on_exit=lambda name, status, died: heard.append(name))  # fmt: skip
    try:
        run = two.start(procs, b_code=4)
        assert [p.key for p in run.started] == ["two:a", "two:b"]
        assert procs["two:a"].status().running and run.closing
        until(lambda: "two:b" in heard, 5, "b's exit, heard by the group's own on_exit")
        assert procs.on_exit is not run._exited  # detached: the test's again
        assert run.deaths == []  # exit 4 was allowed, and the run no longer judges
    finally:
        procs.close(check=False)
    assert not procs["two:a"].status().running
