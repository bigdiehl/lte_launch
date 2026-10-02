"""Imperative (synchronous) launching: a script that drives a launch (Q26).

Stock launch is fire-and-forget -- you hand over a ``LaunchDescription`` and have
no control afterwards. A **session** inverts that. Your script runs top to
bottom: it starts things, waits for them, checks the ROS graph, does its own
work, stops things, and starts more -- while keeping what launch gives you (live
output, event handling, and a process dying without taking the script down).

    @script(config=BringupConfig)
    def bringup(session, config):
        nav = session.start(nav_launch, NavConfig())
        nav.wait_started()
        nav["planner"].wait_output("ready")
        session.wait_for_node("/planner")
        ...                                    # your own checks, in order
        nav.stop()

The one hard constraint, which shapes all of this: ``LaunchService.run()``
refuses to run anywhere but the main thread (*"LaunchService can only be run in
the main thread."*). So the framework owns ``main()`` -- the service runs on the
main thread and **your script runs on a worker thread**. That is legal because
``emit_event`` and ``include_launch_description`` are both documented
thread-safe; everything here goes through them.

Two consequences worth knowing:

* **Ctrl-C makes whatever wait you are in raise** ``SessionShutdown``, so your
  own ``try/finally`` cleanup runs before the session goes down. A wait never
  returns a comforting lie about a session that is already shutting down.
* **The session always stops what it started.** When the script returns or
  raises, everything the session started is shut down.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from typing import Any, Callable, Iterator

from . import ros_config as rc
from .core import TypedLaunch
from .monitor import Monitor, ProcessInfo

#: How often a wait re-checks its predicate even when no launch event arrives.
#: Waits are woken by events too; this only bounds graph/poll-style predicates.
POLL = 0.05

#: Set in the environment of every process a stage starts, which is how a
#: process started by an *included* launch file -- whose action we never see --
#: is still attributed to the stage that caused it.
STAGE_ENV = "TLAUNCH_STAGE"


class SessionError(RuntimeError):
    """A session failure, reported without a traceback."""


class WaitTimeout(SessionError):
    """A wait hit its timeout."""


class SessionShutdown(SessionError):
    """The session started shutting down (Ctrl-C, or shutdown) while a wait was
    blocked. Raised at once, by design, so the script's cleanup can run."""


def _matcher(pattern: Any) -> Callable[[str], bool]:
    """A plain string matches as a substring; a compiled regex by search."""
    if isinstance(pattern, str):
        return lambda line: pattern in line
    if isinstance(pattern, re.Pattern):
        return lambda line: pattern.search(line) is not None
    raise TypeError(f"pattern must be a string or a compiled regex, got {type(pattern).__name__}")


def _execute_processes(entities: list) -> list:
    """Every process action reachable in an entity tree, in order.

    Only what is knowable *before* anything runs: an ``IncludeLaunchDescription``
    has not been read yet, so processes from an included stock launch file are
    not here. They are picked up at runtime instead (see ``STAGE_ENV``).
    """
    from launch.actions import ExecuteProcess

    found = []
    for entity in entities:
        if isinstance(entity, ExecuteProcess):
            found.append(entity)
            continue
        try:
            subs = entity.get_sub_entities()
        except Exception:
            subs = []
        found.extend(_execute_processes(list(subs or [])))
    return found


def _plain_text(value) -> str | None:
    """``value`` as a plain string, if it is one.

    Launch normalizes a name into a list of substitutions, so ``name="driver"``
    arrives as ``[TextSubstitution("driver")]``. A name built from a real
    substitution (a ``LaunchConfiguration``, say) has no value until launch
    executes the action, so it gets no key.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and value:
        from launch.substitutions import TextSubstitution

        if all(isinstance(item, TextSubstitution) for item in value):
            return "".join(item.text for item in value)
    return None


def _declared_name(action) -> str | None:
    """The name a stage addresses a process by, if it has one at build time.

    A ``Node`` with ``name="planner"`` is ``stage["planner"]``; so is an
    ``ExecuteProcess(name="driver")``. A node named by a substitution, or not
    named at all, has no key until it starts.
    """
    name = _plain_text(getattr(action, "_Node__node_name", None))   # launch_ros Node
    if name and not name.startswith("<"):            # <node_name_unspecified>
        return name
    return _plain_text(getattr(action, "name", None))  # ExecuteProcess(name=...)


class Process:
    """One launched process: wait on it, read its state, stop it.

    The state itself lives in the monitor's record (``monitor.py``), which is the
    one place process facts are tracked, for sessions and plain runs alike. This
    is the handle over it.
    """

    def __init__(self, session: "Session", key: str, info: ProcessInfo):
        self._session = session
        self.key = key
        self.info = info

    @property
    def action(self):
        return self.info.action

    @property
    def pid(self) -> int | None:
        return self.info.pid

    @property
    def returncode(self) -> int | None:
        return self.info.returncode

    @property
    def started(self) -> bool:
        return self.info.started

    @property
    def exited(self) -> bool:
        return self.info.exited

    @property
    def cmd(self) -> list[str]:
        return list(self.info.cmd)

    @property
    def process_name(self) -> str | None:
        return self.info.process_name

    @property
    def node_name(self) -> str | None:
        """The node's fully qualified name, once launch has resolved it."""
        return self.info.node_name

    @property
    def running(self) -> bool:
        return self.info.running

    def output(self) -> list[str]:
        """The lines this process has printed (the most recent ones)."""
        return list(self.info.lines)

    def wait_started(self, timeout: float | None = None) -> "Process":
        self._session._wait(
            lambda: self.started, timeout, f"process '{self.key}' to start"
        )
        return self

    def wait_exited(self, timeout: float | None = None) -> int:
        self._session._wait(
            lambda: self.exited, timeout, f"process '{self.key}' to exit"
        )
        return self.returncode

    def wait_output(self, pattern: Any, timeout: float | None = None) -> str:
        """Wait for a line of this process's output to match ``pattern``.

        A string matches as a substring, a compiled regex by ``search``. Lines
        already printed count, so a process that prints its signal before you
        ask still satisfies the wait. Returns the matching line.
        """
        matches = _matcher(pattern)

        def found():
            for line in self.output():
                if matches(line):
                    return line
            return None

        return self._session._wait(
            found, timeout, f"output matching {pattern!r} from '{self.key}'"
        )

    def stop(self, *, wait: bool = True, timeout: float | None = 10.0) -> "Process":
        """Shut this one process down, leaving the rest of the session running."""
        self._session._stop_actions([self.action])
        if wait and self.running:
            try:
                self.wait_exited(timeout)
            except SessionShutdown:
                pass
        return self

    def restart(self, *, timeout: float | None = 10.0):
        """Not supported for one process -- restart its stage instead.

        Launch will not execute an action twice, so a restart means rebuilding;
        rebuilding one node out of a launch body that returns a list would drop
        whatever scope its siblings put it in (an enclosing ``PushRosNamespace``,
        say). Restarting the stage keeps that intact.
        """
        raise SessionError(
            f"'{self.key}' cannot be restarted on its own; restart its stage "
            "(stage.restart()), which rebuilds it with its scope intact"
        )

    def __repr__(self) -> str:
        if not self.started:
            state = "not started"
        elif self.exited:
            state = f"exited rc={self.returncode}"
        else:
            state = f"running pid={self.pid}"
        return f"<Process {self.key} ({state})>"


class Stage:
    """Everything one ``session.start()`` call started, as one handle.

    The stage as a whole can be waited on and stopped; the processes inside it
    are addressed by the name they were declared with::

        nav = session.start(nav_launch, NavConfig())
        nav.wait_started()                   # all of them
        nav["planner"].wait_output("ready")  # one of them
        nav.stop()
    """

    def __init__(self, session: "Session", name: str, stage_id: str,
                 target: Any = None, config: Any = None):
        self.name = name
        self.id = stage_id
        self.target = target
        self.config = config
        self._session = session
        self._processes: dict[str, Process] = {}

    @property
    def rebuildable(self) -> bool:
        """Whether this stage can be restarted.

        Launch refuses to execute an action twice ("executed more than once"),
        and that exception takes the whole service down -- so a restart has to
        *rebuild*. A stage started from a typed launch, or from a function that
        returns actions, can be; one started from action objects cannot.
        """
        return isinstance(self.target, TypedLaunch) or (
            callable(self.target) and not isinstance(self.target, type)
        )

    # -- building ---------------------------------------------------------
    def _add(self, action=None, key: str | None = None) -> Process:
        base = key or _declared_name(action) or "process"
        unique, n = base, 1
        while unique in self._processes:
            n += 1
            unique = f"{base}#{n}"
        info = self._session.monitor.track(action, key=base)
        process = Process(self._session, unique, info)
        self._processes[unique] = process
        return process

    # -- reading ----------------------------------------------------------
    @property
    def processes(self) -> dict[str, Process]:
        """Name -> process, including any picked up at runtime."""
        return dict(self._processes)

    @property
    def pids(self) -> dict[str, int]:
        return {k: p.pid for k, p in self._processes.items() if p.pid is not None}

    def __getitem__(self, key: str) -> Process:
        try:
            return self._processes[key]
        except KeyError:
            known = ", ".join(sorted(self._processes)) or "none"
            raise KeyError(
                f"stage '{self.name}' has no process named '{key}' (it has: {known}; "
                "a process is only addressable by name if its node was given one "
                "at build time)"
            ) from None

    def __iter__(self) -> Iterator[Process]:
        return iter(list(self._processes.values()))

    def __len__(self) -> int:
        return len(self._processes)

    # -- waiting ----------------------------------------------------------
    def wait_started(self, timeout: float | None = None) -> "Stage":
        """Wait until every process the stage knows about has started."""
        self._session._wait(
            lambda: bool(self._processes) and all(p.started for p in self),
            timeout,
            f"stage '{self.name}' to start",
        )
        return self

    def wait_exited(self, timeout: float | None = None) -> "Stage":
        self._session._wait(
            lambda: bool(self._processes) and all(p.exited for p in self),
            timeout,
            f"stage '{self.name}' to exit",
        )
        return self

    def wait_output(self, pattern: Any, timeout: float | None = None) -> str:
        """Wait for any process in the stage to print a matching line."""
        matches = _matcher(pattern)

        def found():
            for process in self:
                for line in process.output():
                    if matches(line):
                        return line
            return None

        return self._session._wait(
            found, timeout, f"output matching {pattern!r} from stage '{self.name}'"
        )

    # -- stopping ---------------------------------------------------------
    def stop(self, *, wait: bool = True, timeout: float | None = 10.0) -> "Stage":
        """Shut down this stage, leaving the rest of the session running."""
        actions = [p.action for p in self if p.action is not None and p.running]
        self._session._stop_actions(actions)
        if wait:
            try:
                self._session._wait(
                    lambda: all(not p.running for p in self),
                    timeout,
                    f"stage '{self.name}' to stop",
                )
            except SessionShutdown:
                pass
        return self

    def restart(self, *, timeout: float | None = 10.0) -> "Stage":
        """Stop this stage and start it again, freshly built.

        The same handle and the same keys: ``stage["planner"]`` still works
        afterwards, pointing at the new process.
        """
        self._session._restart_stage(self, timeout)
        return self

    def _reset(self) -> None:
        for process in self._processes.values():
            self._session._by_action.pop(process.action, None)
        self._processes.clear()   # the monitor keeps its records, for the summary

    def __repr__(self) -> str:
        return f"<Stage {self.name} ({len(self)} process(es))>"


class Session:
    """The handle a script drives a launch through.

    Created for you by ``@script``; the script body receives it as its first
    argument. Everything here is called from the script's thread, never from
    launch's.
    """

    def __init__(self, service, *, ros: rc.RosConfig | None = None, debug: bool = False,
                 monitor: Monitor | None = None):
        self._service = service
        self._ros = ros if ros is not None else rc.RosConfig()
        self.debug = debug
        # One tracking implementation for sessions and plain runs alike; the
        # session adds handles, stage attribution and the waits on top (Q27).
        self.monitor = monitor if monitor is not None else Monitor()
        self.monitor.listen(self._record_changed)
        self._stages: list[Stage] = []
        self._by_action: dict[Any, Process] = {}
        self._by_stage: dict[str, Stage] = {}
        self._changed = threading.Condition()
        self._shutdown = threading.Event()
        self._interrupted = False
        self._running = threading.Event()
        self._rclpy_node = None
        self._rclpy_ours = False

    # ------------------------------------------------------------------ waits
    def _wait(self, predicate: Callable[[], Any], timeout: float | None, what: str) -> Any:
        """Block until ``predicate()`` is truthy; return what it returned.

        Raises ``SessionShutdown`` the moment the session starts shutting down
        (so Ctrl-C does not hang in a wait) and ``WaitTimeout`` on timeout.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._changed:
            while True:
                value = predicate()
                if value:
                    return value
                if self._shutdown.is_set():
                    raise SessionShutdown(f"shutting down while waiting for {what}")
                slice_ = POLL
                if deadline is not None:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise WaitTimeout(f"timed out after {timeout}s waiting for {what}")
                    slice_ = min(POLL, left)
                self._changed.wait(slice_)

    def sleep(self, seconds: float) -> None:
        """Like ``time.sleep``, but a Ctrl-C raises instead of being slept through."""
        try:
            self._wait(lambda: False, seconds, f"{seconds}s to pass")
        except WaitTimeout:
            return

    @property
    def shutting_down(self) -> bool:
        return self._shutdown.is_set()

    # ---------------------------------------------------------------- startin
    def start(self, target: Any, config: Any = None, *, name: str | None = None) -> Stage:
        """Start a typed launch (or raw actions) now, and return its handle.

        ``target`` is a ``@launch``-decorated typed launch, a ``LaunchDescription``,
        a list of launch actions, or a single action. The call returns as soon as
        the work is queued with the service -- wait on the handle for anything
        that has to have happened.
        """
        actions, label = self._build(target, config)
        stage = Stage(self, name or label, str(len(self._stages) + 1), target, config)
        self._stages.append(stage)
        self._by_stage[stage.id] = stage
        self._launch_stage(stage, actions)
        return stage

    def _launch_stage(self, stage: Stage, actions: list) -> None:
        import launch
        from launch.actions import GroupAction, SetEnvironmentVariable

        stage._reset()
        for action in _execute_processes(actions):
            self._by_action[action] = stage._add(action)
        # A scoped group keeps each stage's configurations and environment to
        # itself; the marker is how runtime-discovered processes find their stage.
        group = GroupAction(
            [SetEnvironmentVariable(STAGE_ENV, stage.id), *actions], scoped=True
        )
        self._service.include_launch_description(launch.LaunchDescription([group]))

    def _restart_stage(self, stage: Stage, timeout: float | None) -> None:
        if not stage.rebuildable:
            raise SessionError(
                f"stage '{stage.name}' cannot be restarted: it was started from "
                "actions, and launch will not run an action twice -- start it from "
                "a typed launch, or from a function that builds the actions "
                "(session.start(lambda: driver_process(ns))), and it can be rebuilt"
            )
        stage.stop(wait=True, timeout=timeout)
        actions, _ = self._build(stage.target, stage.config)
        self._launch_stage(stage, actions)

    def _build(self, target: Any, config: Any) -> tuple[list, str]:
        import launch

        if isinstance(target, TypedLaunch):
            if config is None:
                config = target.config_type()
            elif not isinstance(config, target.config_type):
                raise TypeError(
                    f"'{target.name}' takes {target.config_type.__name__}, got "
                    f"{type(config).__name__}"
                )
            with rc.active(self._ros):
                return list(target.build(config)), target.name
        if callable(target) and not isinstance(target, type):
            # A function that builds the actions -- the restartable form of
            # passing actions directly.
            label = getattr(target, "__name__", "actions")
            actions, _ = self._build(target() if config is None else target(config), None)
            return actions, "actions" if label == "<lambda>" else label
        if config is not None:
            raise TypeError("config is only accepted with a typed launch or a factory")
        if isinstance(target, launch.LaunchDescription):
            return list(target.entities), "launch_description"
        if isinstance(target, (list, tuple)):
            return list(target), "actions"
        if isinstance(target, launch.LaunchDescriptionEntity):
            return [target], _declared_name(target) or "action"
        raise TypeError(
            "start() takes a typed launch, a LaunchDescription, a list of "
            f"actions, or one action; got {type(target).__name__}"
        )

    @property
    def stages(self) -> list[Stage]:
        return list(self._stages)

    @property
    def processes(self) -> list[Process]:
        """Every process the session has seen, including unattributed ones."""
        return list(self._by_action.values())

    # ---------------------------------------------------------------- stopping
    def _stop_actions(self, actions: list) -> None:
        from launch.events import matches_action
        from launch.events.process import ShutdownProcess

        for action in actions:
            # Tell the monitor we asked for this, so it is not reported as a death.
            self.monitor.note_stop(action)
            self._service.emit_event(ShutdownProcess(process_matcher=matches_action(action)))

    def stop_all(self, *, wait: bool = True, timeout: float | None = 10.0) -> None:
        """Stop every stage, keeping the session itself alive."""
        for stage in self._stages:
            stage.stop(wait=wait, timeout=timeout)

    # ------------------------------------------------------------- graph waits
    def _node_handle(self):
        """A hidden rclpy node, created on first use, for graph queries."""
        if self._rclpy_node is None:
            import rclpy
            from rclpy.node import Node as RclpyNode

            if not rclpy.ok():
                rclpy.init(args=[])
                self._rclpy_ours = True
            # A leading underscore makes it a hidden node, so it does not
            # show up in `ros2 node list` and confuse what is actually up.
            self._rclpy_node = RclpyNode(f"_tlaunch_session_{os.getpid()}")
        return self._rclpy_node

    def _close_rclpy(self) -> None:
        if self._rclpy_node is not None:
            try:
                self._rclpy_node.destroy_node()
            except Exception:
                pass
            self._rclpy_node = None
        if self._rclpy_ours:
            import rclpy

            try:
                rclpy.shutdown()
            except Exception:
                pass
            self._rclpy_ours = False

    def node_names(self) -> list[str]:
        """Every node currently in the graph, fully qualified.

        Minus the session's own hidden query node, which is never what the
        caller is asking about.
        """
        node = self._node_handle()
        mine = node.get_name()
        return [
            f"{namespace.rstrip('/')}/{name}"
            for name, namespace in node.get_node_names_and_namespaces()
            if name != mine
        ]

    def wait_for_node(self, name: str, timeout: float | None = 30.0) -> str:
        """Wait until a node appears in the ROS graph (a real bringup check).

        ``name`` is fully qualified, e.g. ``/demo/turtlesim``.
        """
        want = name if name.startswith("/") else f"/{name}"
        return self._wait(
            lambda: want if want in self.node_names() else None,
            timeout,
            f"node '{want}' to appear",
        )

    def wait_for_topic(
        self, topic: str, timeout: float | None = 30.0, *, publishers: int = 1
    ) -> str:
        """Wait until ``topic`` exists with at least ``publishers`` publishers."""
        node = self._node_handle()
        want = topic if topic.startswith("/") else f"/{topic}"

        def ready():
            return want if node.count_publishers(want) >= publishers else None

        return self._wait(
            ready, timeout, f"topic '{want}' to have {publishers} publisher(s)"
        )

    # ----------------------------------------------------------------- logging
    def log(self, message: str) -> None:
        """Print in launch's own style, so script and launch output interleave
        readably."""
        print(f"[INFO] [tlaunch]: {message}", flush=True)

    # ------------------------------------------------------- launch-side hooks
    # Everything below runs on launch's thread, never the script's.
    def bootstrap(self):
        """The launch description that wires the session to the service."""
        import launch
        from launch.actions import OpaqueFunction, RegisterEventHandler
        from launch.event_handlers import OnShutdown

        return launch.LaunchDescription([
            *self.monitor.handlers(),
            RegisterEventHandler(OnShutdown(on_shutdown=self._on_shutdown)),
            OpaqueFunction(function=self._on_running),
        ])

    def _record_changed(self, info: ProcessInfo) -> None:
        """A process event reached the monitor: adopt anything new, wake waiters."""
        with self._changed:
            if info.action not in self._by_action:
                self._adopt(info)
            self._changed.notify_all()

    def _adopt(self, info: ProcessInfo) -> None:
        """Give a handle to a process we did not build.

        It came from an included launch file, whose actions we never see, so the
        stage it belongs to is read from the marker its environment carries.
        """
        stage = self._by_stage.get((info.env or {}).get(STAGE_ENV, ""))
        key = info.process_name or info.key
        process = (
            stage._add(info.action, key=key)
            if stage is not None
            else Process(self, key, info)
        )
        self._by_action[info.action] = process

    def _on_running(self, context) -> None:
        with self._changed:
            self._running.set()
            self._changed.notify_all()

    # Kept as thin wrappers: the monitor does the recording, and these are the
    # names the session's own tests drive events through.
    def _on_start(self, event, context=None) -> None:
        self.monitor.on_start(event, context)

    def _on_exit(self, event, context=None) -> None:
        self.monitor.on_exit(event, context)

    def _on_io(self, event) -> None:
        self.monitor.on_io(event)

    def _on_shutdown(self, event=None, context=None) -> None:
        self.monitor.on_shutdown(event, context)
        with self._changed:
            self._interrupted = getattr(event, "due_to_sigint", False)
            self._shutdown.set()
            self._changed.notify_all()

    def _finish(self) -> None:
        """Stop everything the session started and let the service's run() end.

        Retried, because ``LaunchService.shutdown()`` does nothing if the service
        is not running yet -- which a script that failed immediately can beat.
        """
        self._close_rclpy()
        deadline = time.monotonic() + 15.0
        while not self._shutdown.is_set() and time.monotonic() < deadline:
            try:
                self._service.shutdown()
            except Exception:
                break
            if self._shutdown.wait(0.1):
                break


def run_session(
    body: Callable[[Session], Any],
    *,
    ros: rc.RosConfig | None = None,
    noninteractive: bool = False,
    debug: bool = False,
    diagnostics: bool = False,
) -> int:
    """Run ``body(session)`` on a worker thread, with the service on this one.

    Must be called from the main thread -- that is launch's rule, not ours.
    Returns the script's exit code: its return value if it returned an int,
    otherwise the launch service's, and non-zero if anything died unexpectedly
    (Q27). A script that raises propagates.
    """
    import launch

    from . import monitor as mon

    service = launch.LaunchService(noninteractive=noninteractive, debug=debug)
    monitor = mon.Monitor()
    session = Session(service, ros=ros, debug=debug, monitor=monitor)
    service.include_launch_description(session.bootstrap())
    publisher = mon.Diagnostics(monitor) if diagnostics else None

    result: dict[str, Any] = {}

    def worker() -> None:
        try:
            session._wait(
                lambda: session._running.is_set(), 30.0, "the launch service to start"
            )
            result["value"] = body(session)
        except BaseException as exc:  # noqa: BLE001 -- handed to the caller as-is
            result["error"] = exc
        finally:
            session._finish()

    thread = threading.Thread(target=worker, name="tlaunch-script", daemon=True)
    thread.start()
    if publisher is not None:
        publisher.start()
    try:
        service_rc = service.run(shutdown_when_idle=False)
    finally:
        if publisher is not None:
            publisher.stop()
    thread.join(timeout=15.0)

    error = result.get("error")
    if isinstance(error, SessionShutdown) and session._interrupted:
        # Ctrl-C: the wait raised so the script's cleanup could run, and the
        # script let it through. That is a cancellation, not a failure.
        print("[INFO] [tlaunch]: interrupted", flush=True)
        return 130
    if error is not None:
        raise error
    value = result.get("value")
    code = value if isinstance(value, int) and not isinstance(value, bool) else service_rc
    return mon.report(monitor, code)
