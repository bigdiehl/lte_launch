"""Checks a plain launch declares: wait for a node, a topic or a line of output.

    driver = ExecuteProcess(cmd=["ros2", "topic", "pub", ...])
    return [
        turtlesim,
        checks.node("/demo/turtlesim", timeout=10, then=[driver]),
        checks.output(driver, r"publishing", timeout=10),
    ]

The same waits a script has on its ``Session`` (Q26), for a launch that is not a
script. A check is an ordinary launch action: it waits in launch's own event
loop, so nothing else is held up, and its ``then`` actions start once it
passes -- which is how a launch says "this, and only after it is really up,
that". A check that times out logs an error, is a failure in the run's summary
(so ``tlaunch`` exits non-zero), and by default shuts the launch down;
``on_fail="continue"`` only reports it.

Graph checks use launch_ros's own node for the launch (``get_ros_node``), so no
second rclpy context is set up. Under stock ``ros2 launch`` checks work the
same, minus the summary and exit code, which are tlaunch's.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable

import launch
import launch.logging
from launch.event_handlers import OnProcessIO, OnShutdown

from .session import _declared_name

#: How often a check looks again.
POLL = 0.2

#: Where the run's Monitor puts itself in the launch context (monitor.py).
MONITOR_KEY = "ros2_typed_launch_monitor"

ON_FAIL = ("shutdown", "continue")


class CheckDone(launch.Event):
    """A check has passed or failed. Its ``then`` actions wait for the first."""

    name = "ros2_typed_launch.checks.CheckDone"

    def __init__(self, check: "Check", passed: bool):
        super().__init__()
        self.check = check
        self.passed = passed


class Check(launch.Action):
    """Wait until ``ready(context)`` is truthy, then run ``then``."""

    def __init__(self, what: str, ready: Callable[[Any], Any], *,
                 timeout: float | None, then: list | None, on_fail: str,
                 setup: Callable[[Any], None] | None = None):
        if on_fail not in ON_FAIL:
            raise ValueError(f"on_fail must be one of {ON_FAIL}, got {on_fail!r}")
        super().__init__()
        self.what = what
        self.timeout = timeout
        self.then = list(then or [])
        self.on_fail = on_fail
        self.passed: bool | None = None
        self._ready = ready
        self._setup = setup
        self._task: asyncio.Task | None = None

    def execute(self, context):
        if self._setup is not None:
            self._setup(context)
        context.register_event_handler(launch.EventHandler(
            matcher=lambda event: (isinstance(event, CheckDone) and event.check is self
                                   and event.passed),
            entities=self.then,
            handle_once=True,
        ))
        context.register_event_handler(OnShutdown(on_shutdown=self._cancel))
        self._task = context.asyncio_loop.create_task(self._run(context))

    def get_asyncio_future(self):
        return self._task

    def _cancel(self, event, context):
        if self._task is not None:
            self._task.cancel()

    async def _run(self, context) -> None:
        start = time.monotonic()
        while True:
            try:
                if self._ready(context):
                    break
            except Exception:
                pass            # e.g. the graph is not reachable yet: look again
            if self.timeout is not None and time.monotonic() - start >= self.timeout:
                await self._done(context, False, f"not after {self.timeout:g}s")
                return
            await asyncio.sleep(POLL)
        await self._done(context, True, f"{time.monotonic() - start:.1f}s")

    async def _done(self, context, passed: bool, detail: str) -> None:
        self.passed = passed
        log = launch.logging.get_logger("tlaunch")
        if passed:
            log.info(f"check passed: {self.what} ({detail})")
        else:
            log.error(f"check failed: {self.what}, {detail}")
            monitor = context.get_locals_as_dict().get(MONITOR_KEY)
            if monitor is not None:
                monitor.note_failed_check(f"{self.what}, {detail}")
        await context.emit_event(CheckDone(self, passed))
        if not passed and self.on_fail == "shutdown":
            from launch.events import Shutdown

            await context.emit_event(Shutdown(reason=f"check failed: {self.what}"))


def _absolute(name: str) -> str:
    return name if name.startswith("/") else f"/{name}"


def node(name: str, *, timeout: float | None = 30.0, then: list | None = None,
         on_fail: str = "shutdown") -> Check:
    """Wait until a node is in the ROS graph. ``name`` is fully qualified."""
    want = _absolute(name)

    def ready(context):
        from launch_ros.ros_adapters import get_ros_node

        ros = get_ros_node(context)
        mine = ros.get_name()
        return any(
            f"{namespace.rstrip('/')}/{found}" == want
            for found, namespace in ros.get_node_names_and_namespaces()
            if found != mine
        )

    return Check(f"node '{want}'", ready, timeout=timeout, then=then, on_fail=on_fail)


def topic(name: str, *, publishers: int = 1, timeout: float | None = 30.0,
          then: list | None = None, on_fail: str = "shutdown") -> Check:
    """Wait until a topic has at least ``publishers`` publishers."""
    want = _absolute(name)

    def ready(context):
        from launch_ros.ros_adapters import get_ros_node

        return get_ros_node(context).count_publishers(want) >= publishers

    return Check(f"topic '{want}' with {publishers} publisher(s)", ready,
                 timeout=timeout, then=then, on_fail=on_fail)


def output(action, pattern: str | re.Pattern, *, timeout: float | None = 30.0,
           then: list | None = None, on_fail: str = "shutdown") -> Check:
    """Wait until a process prints a line matching ``pattern`` (``re.search``).

    ``action`` is the process's own launch action. Lines it printed before the
    check started count too, when the run keeps them (tlaunch's monitor does).
    """
    regex = re.compile(pattern) if isinstance(pattern, str) else pattern
    seen = {"match": False}

    def lines(text) -> None:
        text = text.decode(errors="replace") if isinstance(text, bytes) else str(text)
        if any(regex.search(line) for line in text.splitlines()):
            seen["match"] = True

    def setup(context):
        monitor = context.get_locals_as_dict().get(MONITOR_KEY)
        info = monitor.get(action) if monitor is not None else None
        if info is not None:
            lines("\n".join(info.lines))
        context.register_event_handler(OnProcessIO(
            target_action=action,
            on_stdout=lambda event: lines(event.text),
            on_stderr=lambda event: lines(event.text),
        ))

    label = _declared_name(action) or type(action).__name__
    return Check(f"output /{regex.pattern}/ from {label}", lambda context: seen["match"],
                 timeout=timeout, then=then, on_fail=on_fail, setup=setup)
