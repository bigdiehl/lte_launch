"""Lifecycle nodes: dry-run plans, and one live run of lifecycle's talker."""

from __future__ import annotations

import pytest
from _live import need_ros
from pydantic import BaseModel

from ros2_typed_launch import (
    LaunchError, LifecycleNode, launch, lifecycle_at, lifecycle_node, namespace, shutdown,
)  # fmt: skip
from ros2_typed_launch import engine

TALKER = ("lifecycle", "lifecycle_talker")


def _sets(run) -> list[str]:
    return [p.command for p in run.plan if p.command.startswith("ros2 lifecycle")]


def _need_talker():
    need_ros("lifecycle", "lifecycle_msgs")


def test_lifecycle_dry_run():
    _need_talker()
    run = engine.Run(None, label="x")
    with engine.active(run), namespace("lc"):
        cam = lifecycle_node(*TALKER, name="cam")
        assert isinstance(cam, LifecycleNode) and cam.fqn == "/lc/cam"
        assert cam.state == "unconfigured"
        assert cam.transitions() == ["configure", "shutdown"]
        assert cam.transition("configure") == "inactive"
        assert cam.activate() == "active"  # only what is left: activate
        assert cam.wait_for_state("active") == "active"
        assert cam.set_state("unconfigured") == "unconfigured"  # deactivate, cleanup
        assert cam.set_state("finalized") == "finalized"
        with pytest.raises(LaunchError, match="no transition from 'finalized'"):
            cam.activate()
        with pytest.raises(ValueError, match="lifecycle state 'on'"):
            cam.set_state("on")

        auto = lifecycle_node(*TALKER, name="auto", autostart=True)
        half = lifecycle_node(*TALKER, name="half", autostart="inactive")
        with pytest.raises(ValueError, match="autostart="):
            lifecycle_node(*TALKER, name="bad", autostart="running")

        ext = lifecycle_at("arm")  # relative: in the namespace
        assert ext.fqn == "/lc/arm" and not ext.finalize
        assert lifecycle_at("/lc/cam") is cam  # one we started
        with pytest.raises(AttributeError, match="started elsewhere"):
            ext.pid
        assert cam.window == "lc"  # the process handle's

    assert auto.state == "active" and half.state == "inactive"
    assert run.plan[0].name == "lc:cam"
    assert [p.name for p in run.plan[1:3]] == ["lc:cam.configure", "lc:cam.activate"]
    assert _sets(run) == [
        "ros2 lifecycle set /lc/cam configure",
        "ros2 lifecycle set /lc/cam activate",
        "ros2 lifecycle set /lc/cam deactivate",
        "ros2 lifecycle set /lc/cam cleanup",
        "ros2 lifecycle set /lc/cam shutdown",
        "ros2 lifecycle set /lc/auto configure",
        "ros2 lifecycle set /lc/auto activate",
        "ros2 lifecycle set /lc/half configure",
    ]
    assert [h.fqn for h in run.lifecycle] == ["/lc/cam", "/lc/auto", "/lc/half", "/lc/arm"]
    run.teardown()  # a dry run sends nothing


# ------------------------------------------------------------------ live
class Live(BaseModel):
    pass


@launch
def live(config: Live):
    with namespace("lct"):
        talker = lifecycle_node(*TALKER, name="talker")
        assert talker.state == "unconfigured"
        with pytest.raises(LaunchError, match="only configure, shutdown"):
            talker.transition("activate")
        assert talker.transition("configure") == "inactive"
        assert talker.activate() == "active"
        assert talker.wait_for_state("active", timeout=5) == "active"
        assert "deactivate" in talker.transitions()
        assert lifecycle_at("talker") is talker
        live.seen = talker.state
    shutdown("checked")


@pytest.mark.live
def test_lifecycle_live(tmp_path, capsys):
    processes = pytest.importorskip("libtmux_easy.processes")
    pytest.importorskip("rclpy")
    pytest.importorskip("lifecycle_msgs")
    _need_talker()

    procs = processes.Processes("t", log_dir=str(tmp_path), prefix="")
    run = engine.Run(procs, label="live", params_dir=str(tmp_path))
    code = engine.execute(live, Live(), run)
    out = capsys.readouterr().out
    assert code == 0, out
    assert live.seen == "active"
    assert "/lct/talker: configure -> inactive" in out
    assert "/lct/talker: activate -> active" in out
    # teardown: shutdown before the SIGINT, so on_shutdown ran
    assert "/lct/talker: shutdown -> finalized" in out
    assert "on shutdown is called from state active" in procs.output("lct:talker")
    assert not run.deaths
