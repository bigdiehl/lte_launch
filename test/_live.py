"""Helpers for the live tests (real processes, ROS nodes, tlaunch itself).

The fixtures that use these are in conftest.py; tests import the plain
helpers directly (``from _live import until, need_ros``).
"""

from __future__ import annotations

import contextlib
import itertools
import os
import re
import sys
import threading
import time

import pytest

PY = sys.executable
counter = itertools.count()

# Processes with known stop behaviour; each prints "up" when ready.
#: exits 0 at once on SIGINT
QUIET = (f"{PY} -c \"import signal, time; print('up', flush=True); "
         "signal.signal(signal.SIGINT, lambda *a: exit(0)); time.sleep(60)\"")  # fmt: skip
#: exits 0 on SIGINT, after 1.5 s
SLOW = (f"{PY} -c \"import signal, time; print('up', flush=True); "
        "signal.signal(signal.SIGINT, lambda *a: (time.sleep(1.5), exit(0))); "
        "time.sleep(60)\"")  # fmt: skip
#: ignores SIGINT: only SIGTERM or SIGKILL end it
DEAF = (f"{PY} -c \"import signal, time; print('up', flush=True); "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(60)\"")  # fmt: skip


def need_ros(*packages: str) -> None:
    """Skip unless rclpy and each ROS package are there."""
    pytest.importorskip("rclpy")
    pytest.importorskip("ament_index_python")
    from ament_index_python.packages import (
        PackageNotFoundError,
        get_package_prefix,
    )

    for package in packages:
        try:
            get_package_prefix(package)
        except PackageNotFoundError:
            pytest.skip(f"ROS package {package} not installed")


def until(fn, timeout: float = 15.0, what: str = "condition", interval=0.1):
    """Poll ``fn`` until it returns something truthy; -> that."""
    deadline = time.monotonic() + timeout
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout} s waiting for {what}")
        time.sleep(interval)


def later(seconds: float, fn, *args) -> threading.Timer:
    """Call ``fn`` on a daemon timer thread (to Ctrl-C ourselves mid-run)."""
    timer = threading.Timer(seconds, fn, args=args)
    timer.daemon = True
    timer.start()
    return timer


def when(condition, fn, *args, timeout: float = 30.0) -> threading.Thread:
    """Call ``fn`` on a daemon thread once ``condition()`` holds (or give up)."""

    def watch():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with contextlib.suppress(Exception):
                if condition():
                    fn(*args)
                    return
            time.sleep(0.05)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


class Probe:
    """A test's own rclpy node, on a context of its own: the graph as an
    outsider sees it."""

    def __init__(self):
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor

        self.context = Context()
        rclpy.init(args=[], context=self.context)
        self.node = rclpy.create_node(
            f"_probe_{os.getpid()}_{next(counter)}", context=self.context
        )
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)

    def nodes(self) -> set:
        return {
            f"{ns.rstrip('/')}/{name}"
            for name, ns in self.node.get_node_names_and_namespaces()
        }

    def wait_node(self, name: str, timeout: float = 20.0, present: bool = True):
        return until(
            lambda: (name in self.nodes()) == present,
            timeout,
            f"node {name} {'in' if present else 'gone from'} the graph",
        )

    def publishers(self, topic: str) -> int:
        return self.node.count_publishers(topic)

    def call(self, srv_type, service: str, request, timeout: float = 10.0):
        client = self.node.create_client(srv_type, service)
        try:
            if not client.wait_for_service(timeout_sec=timeout):
                raise AssertionError(f"no service {service}")
            future = client.call_async(request)
            self.executor.spin_until_future_complete(future, timeout_sec=timeout)
            if not future.done():
                raise AssertionError(f"{service}: no answer")
            return future.result()
        finally:
            self.node.destroy_client(client)

    def components(self, container: str) -> list[str]:
        """The full names of what ``container`` has loaded."""
        from composition_interfaces.srv import ListNodes

        reply = self.call(
            ListNodes, f"{container}/_container/list_nodes", ListNodes.Request()
        )
        return list(reply.full_node_names)

    def param(self, node: str, name: str):
        from rcl_interfaces.srv import GetParameters
        from rclpy.parameter import parameter_value_to_python

        reply = self.call(
            GetParameters, f"{node}/get_parameters", GetParameters.Request(names=[name])
        )
        return parameter_value_to_python(reply.values[0])

    def destroy(self) -> None:
        self.executor.shutdown()
        self.node.destroy_node()
        self.context.try_shutdown()


class Reader:
    """Collects a Popen's text output on a thread: ``wait_for(regex)``."""

    def __init__(self, proc):
        self.proc = proc
        self.lines: list[str] = []
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def wait_for(self, pattern: str, timeout: float = 20.0) -> str:
        rx = re.compile(pattern)

        def seen():
            for line in list(self.lines):
                if rx.search(line):
                    return line
            if self.proc.poll() is not None and not self.thread.is_alive():
                raise AssertionError(
                    f"ended ({self.proc.returncode}) before {pattern!r}:\n{self.text}"
                )
            return None

        return until(seen, timeout, repr(pattern))

    def finish(self, timeout: float = 30.0) -> int:
        code = self.proc.wait(timeout)
        self.thread.join(5)
        return code
