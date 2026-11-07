"""Waits and process handles, live: wait_for (regex, ready=), wait_for_exit,
wait_for_node, wait_for_topic, sleep, timeouts, a wait failing on another
process's death; proc.stop(), proc.restart()."""

from __future__ import annotations

import time

import pytest

from _live import PY, need_ros
from lte_launch import engine, namespace, node, process, sleep
from lte_launch import wait_for_node, wait_for_topic

pytestmark = pytest.mark.live

# Prints a numbered line every 0.1 s.
TICK = "i=0; while true; do echo tick $i; i=$((i+1)); sleep 0.1; done"


def test_output_waits_and_handles(make_run):
    run = make_run()
    with engine.active(run):
        # wait_for: -> the matching line; a line printed already counts.
        p = process("tick", TICK, ready=r"tick [3-9]")
        assert p.wait_for(r"tick \d") == "tick 0"
        assert p.wait_for(r"tick 5$", timeout=5) == "tick 5"
        assert p.running and p.pid and p.returncode is None

        # A timeout raises WaitTimeout, naming what it waited for.
        t = time.monotonic()
        with pytest.raises(
            engine.WaitTimeout, match=r"0.3 s waiting for t:tick to print 'never'"
        ):
            p.wait_for("never", timeout=0.3)
        assert 0.25 < time.monotonic() - t < 2

        # A process that ends before printing it: ProcessDied at once.
        q = process("short", "echo hi; exit 0")
        with pytest.raises(
            engine.ProcessDied,
            match="t:short ended .exit code 0. before printing 'bye'",
        ):
            q.wait_for("bye", timeout=10)

        # wait_for_exit: -> the code; -N for a signal.
        assert process("e", "exit 4", exits=4).wait_for_exit(5) == 4
        with pytest.raises(engine.WaitTimeout):
            p.wait_for_exit(timeout=0.2)

        # stop(): not a death; -> its code. restart(): same handle, new pid.
        old = p.pid
        assert p.restart() is p and p.running and p.pid != old
        assert p.wait_for(r"tick 0") == "tick 0"  # a fresh run's output
        assert p.stop() is not None and not p.running
        assert run.deaths == []  # stopped is not died

        # Killed by a signal: -N, and a death -- even waited for, so the wait
        # for its own exit fails with it.
        k = process("k", "kill -TERM $$")
        with pytest.raises(engine.ProcessDied, match="t:k died"):
            k.wait_for_exit(5)
        assert k.returncode == -15 and [d.key for d in run.deaths] == ["t:k"]

        # sleep(): wall clock; a death meanwhile was already recorded above,
        # so every wait now fails at once naming it.
        t = time.monotonic()
        with pytest.raises(engine.ProcessDied, match="bringup stopped: t:k died"):
            sleep(5)
        assert time.monotonic() - t < 1


def test_a_wait_fails_when_another_process_dies(make_run):
    run = make_run()
    with engine.active(run):
        p = process("tick", TICK, ready="tick")
        process("crash", "sleep 0.4; exit 9")
        t = time.monotonic()
        with pytest.raises(engine.ProcessDied, match="t:crash died"):
            p.wait_for("never", timeout=20)
        assert time.monotonic() - t < 3
        assert [d.key for d in run.deaths] == ["t:crash"]

    # ready= is a wait too: a death before the line fails the start.
    run = make_run()
    with engine.active(run):
        with pytest.raises(engine.ProcessDied, match="before printing 'ready'"):
            process("x", "echo not yet; exit 0", ready="ready", timeout=10)

    # sleep() really sleeps when nothing dies.
    run = make_run()
    with engine.active(run):
        t = time.monotonic()
        sleep(0.3)
        assert 0.28 < time.monotonic() - t < 1.5


def test_graph_waits(make_run, uniq):
    need_ros("demo_nodes_cpp")
    run = make_run()
    with engine.active(run), namespace(uniq):
        talker = node("demo_nodes_cpp", "talker", name="talker")
        assert talker.wait_for_node(20) == f"/{uniq}/talker"
        assert wait_for_node("talker") == f"/{uniq}/talker"  # relative: this namespace
        assert wait_for_topic("chatter", 20) == f"/{uniq}/chatter"
        assert wait_for_topic(f"/{uniq}/chatter") == f"/{uniq}/chatter"
        assert f"/{uniq}/talker" in run.node_names()

        with pytest.raises(engine.WaitTimeout, match=f"node /{uniq}/nobody"):
            wait_for_node("nobody", timeout=0.5)
        with pytest.raises(engine.WaitTimeout, match="2 publisher"):
            wait_for_topic("chatter", timeout=0.5, publishers=2)

        anon = process("anon", [PY, "-c", "pass"])
        with pytest.raises(ValueError, match="no name given"):
            anon.wait_for_node()

        # The node leaves the graph when stopped.
        talker.stop()
        assert not talker.running
