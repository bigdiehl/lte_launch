"""Tracking the processes a launch starts, and reporting when one dies (Q27).

Launch reports a death and then forgets it: a process can exit 3 and
``LaunchService.run()`` still returns **0**, so a failed bringup looks like a
successful run to anything reading the exit code. This module keeps one record
per process -- pid, command, node name, exit status -- so a run can end with a
summary and a non-zero exit code, and optionally publish ``/diagnostics``.

Decided 2026-10-01:

* tracking, the error line and the end-of-run summary are **always on** -- they
  cost nothing and need no ROS node;
* publishing is **opt-in** (``--diagnostics``), because it needs an rclpy node
  inside the launch process, and nothing listens by default;
* an **unexpected** death makes ``tlaunch`` exit non-zero. Unexpected means: we
  did not ask it to stop, it was not dying because the launch was shutting down,
  and it exited non-zero or was killed by a signal (``returncode`` is ``-N``);
* a diagnostic is **named by the node's fully qualified name**, with the process
  name alongside pid, returncode and command in its key/value list;
* we promise **direct children only**. A wrapper script's own children are
  invisible to launch (it only learns the wrapper's pid) -- a wrapper should pass
  its child's exit code up. A composable node has no pid at all: it is a thread
  inside a container process.

All mutation happens on launch's own thread, in the event handlers, so there is
no lock here. Each handler sets the record's fields **before** the flag that
announces them (``started``/``exited`` last), so another thread reading the
record without a lock cannot see a half-filled one.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .tmux import PANE_SCRIPT

#: Lines kept per process, so a late reader can still match output that went by.
OUTPUT_LINES = 2000

#: How often diagnostics are published. Aggregators expect liveliness, so this
#: is a heartbeat, not only a change notification.
PUBLISH_PERIOD = 1.0

TOPIC = "/diagnostics"


@dataclass
class ProcessInfo:
    """What we know about one launched process."""

    action: Any
    key: str
    process_name: str | None = None
    node_name: str | None = None
    pid: int | None = None
    returncode: int | None = None
    cmd: list[str] = field(default_factory=list)
    env: dict | None = None
    started: bool = False
    exited: bool = False
    stop_requested: bool = False
    during_shutdown: bool = False
    lines: deque = field(default_factory=lambda: deque(maxlen=OUTPUT_LINES))

    @property
    def running(self) -> bool:
        return self.started and not self.exited

    @property
    def died_unexpectedly(self) -> bool:
        """A death nobody asked for. Exiting 0 on its own is not a death."""
        return (
            self.exited
            and not self.stop_requested
            and not self.during_shutdown
            and bool(self.returncode)
        )

    @property
    def label(self) -> str:
        """How this process is named in a message to a human."""
        if self.node_name and self.process_name:
            return f"{self.node_name} ({self.process_name})"
        return self.node_name or self.process_name or self.key

    def why(self) -> str:
        """Plain words for how it ended."""
        if self.returncode is None:
            return "still running" if self.started else "not started"
        if self.returncode < 0:
            import signal

            try:
                name = signal.Signals(-self.returncode).name
            except ValueError:
                name = f"signal {-self.returncode}"
            return f"killed by {name}"
        return f"exit code {self.returncode}"


def node_name_of(action) -> str | None:
    """A ``Node`` action's fully qualified name, once it is executing.

    ``Node.node_name`` resolves namespace and remappings and raises before the
    action has run, so this is only called from a process event.

    A node started without a `name` has no name to resolve -- rcl lets the
    executable pick its own at startup -- and launch says so with a placeholder
    (``/<node_name_unspecified>``). That is worse than useless in a label, so
    it counts as no name at all and the process name is used instead.
    """
    try:
        name = action.node_name
    except Exception:
        return None
    if not isinstance(name, str) or "<" in name:
        return None
    return name


def real_cmd(cmd) -> list[str]:
    """The command as it was written, with any tmux stand-in taken off.

    A process in a tmux pane is started through `tlaunch-pane` (see tmux.py),
    so that is what launch's events carry. Reports are about the node, not
    about how its output got onto the screen, so the wrapper is dropped here.
    """
    cmd = list(cmd)
    if cmd and os.path.basename(cmd[0]) == PANE_SCRIPT and "--" in cmd:
        return cmd[cmd.index("--") + 1:]
    return cmd


class Monitor:
    """Records every process a launch starts, and what became of it.

    Register a callback with ``listen()`` to be told about each update; the
    session uses that to wake whatever is waiting.
    """

    def __init__(self, *, log: bool = True):
        self.records: dict[Any, ProcessInfo] = {}
        self.shutting_down = False
        self._listeners: list[Callable[[ProcessInfo], None]] = []
        self._log = log

    def listen(self, callback: Callable[[ProcessInfo], None]) -> None:
        """Be called after every update, on launch's thread (the session uses
        this to wake whatever is waiting)."""
        self._listeners.append(callback)

    # ----------------------------------------------------------- registration
    def handlers(self) -> list:
        """The launch actions that wire this monitor up. Put them first."""
        from launch.actions import RegisterEventHandler
        from launch.event_handlers import (
            OnProcessExit,
            OnProcessIO,
            OnProcessStart,
            OnShutdown,
        )

        return [
            RegisterEventHandler(OnProcessStart(on_start=self.on_start)),
            RegisterEventHandler(OnProcessExit(on_exit=self.on_exit)),
            RegisterEventHandler(OnProcessIO(on_stdout=self.on_io, on_stderr=self.on_io)),
            RegisterEventHandler(OnShutdown(on_shutdown=self.on_shutdown)),
        ]

    def track(self, action, key: str | None = None) -> ProcessInfo:
        """Record an action we are about to start (so it is known before it runs)."""
        info = self.records.get(action)
        if info is None:
            info = ProcessInfo(action=action, key=key or "process")
            self.records[action] = info
        elif key:
            info.key = key
        return info

    def get(self, action) -> ProcessInfo | None:
        return self.records.get(action)

    def all(self) -> list[ProcessInfo]:
        return list(self.records.values())

    def note_stop(self, action) -> None:
        """Remember that *we* asked this process to stop, so its death is expected."""
        info = self.records.get(action)
        if info is not None:
            info.stop_requested = True

    # --------------------------------------------------------------- handlers
    def on_start(self, event, context=None) -> None:
        info = self.track(event.action, key=event.process_name)
        info.process_name = event.process_name
        info.node_name = node_name_of(event.action)
        info.pid = event.pid
        info.cmd = real_cmd(event.cmd or [])
        info.env = getattr(event, "env", None)
        info.started = True                      # announce last
        self._changed(info)

    def on_exit(self, event, context=None) -> None:
        info = self.track(event.action, key=event.process_name)
        info.process_name = info.process_name or event.process_name
        info.returncode = event.returncode
        info.during_shutdown = self.shutting_down
        info.started = True
        info.exited = True                       # announce last
        if self._log and info.died_unexpectedly:
            self._report(info)
        self._changed(info)

    def on_io(self, event) -> None:
        text = event.text
        text = text.decode(errors="replace") if isinstance(text, bytes) else str(text)
        info = self.track(event.action, key=event.process_name)
        info.env = info.env or getattr(event, "env", None)
        info.lines.extend(line for line in text.splitlines() if line)
        self._changed(info)

    def on_shutdown(self, event=None, context=None) -> None:
        self.shutting_down = True

    def _changed(self, info: ProcessInfo) -> None:
        for callback in self._listeners:
            callback(info)

    def _report(self, info: ProcessInfo) -> None:
        """One line naming the node, which launch's own death message cannot."""
        import launch.logging

        launch.logging.get_logger("tlaunch").error(
            f"{info.label} died unexpectedly: {info.why()} [pid {info.pid}]"
        )

    # ---------------------------------------------------------------- results
    def deaths(self) -> list[ProcessInfo]:
        return [info for info in self.records.values() if info.died_unexpectedly]

    def never_started(self) -> list[ProcessInfo]:
        return [info for info in self.records.values() if not info.started]

    def summary(self) -> str:
        """A closing report, or "" when everything behaved."""
        deaths = self.deaths()
        if not deaths:
            return ""
        lines = [f"{len(deaths)} process(es) died unexpectedly:"]
        lines += [
            f"  {info.label}: {info.why()} [pid {info.pid}]"
            + (f"\n    cmd: {' '.join(info.cmd)}" if info.cmd else "")
            for info in deaths
        ]
        return "\n".join(lines)


def report(monitor: "Monitor", code: int, *, prog: str = "tlaunch") -> int:
    """Print what died unexpectedly, and make sure the exit code says so.

    This is the one place a run's outcome is summarised, for a plain launch and
    for a session alike.
    """
    summary = monitor.summary()
    if not summary:
        return code
    print(f"{prog}: {summary}", file=sys.stderr)
    return code or 1


class Diagnostics:
    """Publishes one ``DiagnosticStatus`` per tracked process on ``/diagnostics``.

    Opt-in (``--diagnostics``): it needs an rclpy node inside the launch process.
    Publishing runs on its own thread at ``PUBLISH_PERIOD``, so it keeps
    heartbeating while launch's loop is busy.

    ``DiagnosticStatus.level`` **must** be the bytes constant
    (``DiagnosticStatus.ERROR``). Assigning the int ``2`` passes Python and then
    aborts the whole process inside the C conversion -- found the hard way.
    """

    def __init__(self, monitor: Monitor, *, period: float = PUBLISH_PERIOD,
                 topic: str = TOPIC):
        self.monitor = monitor
        self.period = period
        self.topic = topic
        self._node = None
        self._publisher = None
        self._own_rclpy = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import rclpy
        from diagnostic_msgs.msg import DiagnosticArray
        from rclpy.node import Node as RclpyNode

        if not rclpy.ok():
            rclpy.init(args=[])
            self._own_rclpy = True
        # Hidden (leading underscore), so it does not clutter `ros2 node list`.
        self._node = RclpyNode(f"_tlaunch_monitor_{socket.gethostname().split('.')[0]}")
        self._publisher = self._node.create_publisher(DiagnosticArray, self.topic, 10)
        self._thread = threading.Thread(
            target=self._run, name="tlaunch-diagnostics", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2 * self.period + 1)
        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception:
                pass
            self._node = None
        if self._own_rclpy:
            import rclpy

            try:
                rclpy.shutdown()
            except Exception:
                pass
            self._own_rclpy = False

    def _run(self) -> None:
        while not self._stop.wait(self.period):
            try:
                self.publish()
            except Exception:
                # A publishing failure must never take the launch down with it.
                break

    def publish(self) -> None:
        from diagnostic_msgs.msg import DiagnosticArray

        if self._publisher is None:
            return
        message = DiagnosticArray()
        message.header.stamp = self._node.get_clock().now().to_msg()
        message.status = [self.status(info) for info in self.monitor.all()]
        self._publisher.publish(message)

    def status(self, info: ProcessInfo):
        from diagnostic_msgs.msg import DiagnosticStatus, KeyValue

        status = DiagnosticStatus()
        status.name = info.node_name or info.process_name or info.key
        status.hardware_id = socket.gethostname()
        if info.died_unexpectedly:
            status.level = DiagnosticStatus.ERROR
            status.message = f"died unexpectedly: {info.why()}"
        elif info.exited:
            status.level = DiagnosticStatus.OK
            status.message = f"stopped: {info.why()}"
        elif info.started:
            status.level = DiagnosticStatus.OK
            status.message = "running"
        else:
            status.level = DiagnosticStatus.STALE
            status.message = "not started"
        status.values = [
            KeyValue(key="process", value=str(info.process_name or "")),
            KeyValue(key="pid", value="" if info.pid is None else str(info.pid)),
            KeyValue(
                key="returncode",
                value="" if info.returncode is None else str(info.returncode),
            ),
            KeyValue(key="cmd", value=" ".join(info.cmd)),
        ]
        return status
