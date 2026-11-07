"""The synchronous engine: a launch body that starts things as it runs.

``node()`` starts a process and returns its handle; a wait blocks until it
passes, times out, or *any* process dies unexpectedly. When the body returns
the run is up: it prints a ready line and supervises until Ctrl-C, a death or
``shutdown()``, then stops everything at once (SIGINT, then SIGTERM, SIGKILL;
a second Ctrl-C kills).

Processes run in a libtmux_easy ``Processes`` group: subprocesses, tmux panes,
or a test's own group (lte_launch_test). No group is a dry run: nothing
starts, every wait passes, and ``run.plan`` lists what would have started.

The body runs on the main thread -- there is no launch service to own it --
so Ctrl-C is a plain ``KeyboardInterrupt`` wherever the body happens to be.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import signal
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from . import ros_config as rc
from . import schema
from .command import CommandError, join_namespace, with_prefix

PROG = "tlaunch"

#: Every process gets these: output as it happens, not when a buffer fills.
BASE_ENV = {"PYTHONUNBUFFERED": "1", "RCUTILS_LOGGING_BUFFERED_STREAM": "0"}


class LaunchError(RuntimeError):
    """The bringup failed; already reported, so no traceback."""


class ProcessDied(LaunchError):
    pass


class WaitTimeout(LaunchError):
    pass


class Shutdown(Exception):
    """``shutdown()`` from the body: stop everything, cleanly."""


def log(message: str, level: str = "INFO") -> None:
    print(f"[{level}] [{PROG}]: {message}", flush=True)


def code_of(status) -> int | None:
    """A PaneStatus as one int: the exit code, or -N for signal N."""
    if status is None or status.running:
        return None
    e = status.exit_status
    return e.code if e.code is not None else -(e.signal or 0)


def why(code: int | None) -> str:
    if code is None:
        return "still running"
    if code < 0:
        try:
            return f"killed by {signal.Signals(-code).name}"
        except ValueError:
            return f"killed by signal {-code}"
    return f"exit code {code}"


def _codes(exits) -> tuple | None:
    if exits is None:
        return None
    return (exits,) if isinstance(exits, int) else tuple(exits)


# --------------------------------------------------------------------------
# a process handle
# --------------------------------------------------------------------------
@dataclass
class Planned:
    """One process of a dry run."""

    name: str  # window:name
    command: str
    argv: list[str] | None = None


@dataclass
class Proc:
    """One started process: what the body gets back from ``node()``."""

    run: "Run"
    name: str
    window: str
    command: str
    argv: list[str] | None = None
    fqn: str | None = None
    exits: tuple | None = (0,)
    respawn: float | None = None  # seconds before a respawn; None: no respawn
    respawn_max: int = -1
    required: bool = False
    respawns: int = 0
    pending: bool = False  # a respawn is scheduled
    stopping: bool = False
    changed: threading.Event = field(default_factory=threading.Event)

    @property
    def key(self) -> str:
        return f"{self.window}:{self.name}"

    @property
    def who(self) -> str:
        return f"{self.fqn} ({self.key})" if self.fqn else self.key

    def status(self):
        return None if self.run.dry else self.run.procs.status(self.key)

    @property
    def running(self) -> bool:
        status = self.status()
        return bool(status and status.running)

    @property
    def returncode(self) -> int | None:
        return code_of(self.status())

    @property
    def pid(self) -> int | None:
        status = self.status()
        return status.target_pids[0] if status and status.target_pids else None

    def output(self) -> str:
        return "" if self.run.dry else self.run.procs.output(self.key)

    def wait_for(self, pattern: str, timeout: float | None = 30.0) -> str:
        """Wait for a line matching ``pattern`` (printed already counts)."""
        rx = re.compile(pattern)

        def seen():
            for line in self.output().splitlines():
                if rx.search(line):
                    return line
            if not self.running:
                raise ProcessDied(
                    f"{self.who} ended ({why(self.returncode)}) "
                    f"before printing {pattern!r}"
                )
            return None

        return self.run.wait(seen, timeout, f"{self.key} to print {pattern!r}")

    def wait_for_node(self, timeout: float | None = 30.0) -> str:
        """Wait until this node is in the ROS graph."""
        if not self.fqn:
            raise ValueError(f"{self.key}: no name given, so no node to wait for")
        return self.run.wait_for_node(self.fqn, timeout)

    def wait_for_exit(self, timeout: float | None = 30.0) -> int:
        """Wait for it to end; -> its exit code (-N: signal N)."""
        return self.run.wait(
            lambda: None if self.running else self.returncode,
            timeout,
            f"{self.key} to exit",
        )

    def stop(self, timeout: float | None = None) -> int | None:
        """SIGINT, then SIGTERM, then SIGKILL. Not a death."""
        if self.running:
            self.stopping = True
            self.run.procs.stop(self.key, timeout, check=False)
        return self.returncode

    def restart(self) -> "Proc":
        """Stop it and start the same command again; same handle."""
        if self.run.dry:
            return self
        self.stop()
        self.stopping = False
        self.run.procs[self.key].start()
        return self


# --------------------------------------------------------------------------
# a run
# --------------------------------------------------------------------------
class Run:
    """One run of a launch body: its processes, scopes and health.

    ``procs``: a libtmux_easy ``Processes`` (ours, or a test's); None: a dry
    run.
    """

    def __init__(
        self,
        procs=None,
        *,
        label: str,
        ros: rc.RosConfig | None = None,
        params_dir: str | None = None,
        log_levels: list[str] | None = None,
        log_commands: bool = False,
        launch_prefix: str | None = None,
        launch_prefix_filter: str | None = None,
    ):
        self.procs = procs
        self.label = label
        self.ros = ros if ros is not None else rc.RosConfig()
        self.params_dir = params_dir or tempfile.mkdtemp(prefix="tlaunch-params-")
        self.log_levels = list(log_levels or [])
        self.log_commands = log_commands
        self.prefix = shlex.split(launch_prefix) if launch_prefix else []
        self.prefix_filter = launch_prefix_filter
        self.started: list[Proc] = []
        self.plan: list[Planned] = []
        self.deaths: list[Proc] = []
        self.closing = False
        self.shutdown_requested: str | None = None
        #: things outside our processes this run keeps alive until teardown
        #: (components in another's container): ``.lost()``, ``.release()``
        self.held: list = []
        #: containers by full name (``composable``)
        self.containers: dict = {}
        self._wake = threading.Event()
        self._namespaces: list[str] = ["/"]
        self._windows: list[str | None] = [None]
        self._envs: list[dict] = [{}]
        self._graph = None
        self._executor = None
        self._chained = None
        if procs is not None:
            self._chained = procs.on_exit
            procs.on_exit = self._exited

    @property
    def dry(self) -> bool:
        return self.procs is None

    def detach(self) -> None:
        """Stop hearing the group's exits: a test's group is the test's again."""
        if self.procs is not None and self.procs.on_exit == self._exited:
            self.procs.on_exit = self._chained
        self.closing = True

    # ------------------------------------------------------------- scopes
    @property
    def namespace(self) -> str:
        return self._namespaces[-1]

    @property
    def env(self) -> dict:
        return self._envs[-1]

    def window_for(self, namespace: str) -> str:
        explicit = self._windows[-1]
        name = explicit or namespace.strip("/").replace("/", "-") or self.label
        return re.sub(r"[^A-Za-z0-9_-]", "-", name)

    @contextlib.contextmanager
    def in_namespace(self, ns: str):
        self._namespaces.append(join_namespace(self.namespace, ns))
        try:
            yield self.namespace
        finally:
            self._namespaces.pop()

    @contextlib.contextmanager
    def in_window(self, name: str):
        self._windows.append(name)
        try:
            yield name
        finally:
            self._windows.pop()

    @contextlib.contextmanager
    def in_env(self, values: dict):
        self._envs.append({**self.env, **values})
        try:
            yield
        finally:
            self._envs.pop()

    # ----------------------------------------------------------- starting
    def start(
        self,
        name: str,
        cmd: list[str] | str,
        *,
        window: str | None = None,
        fqn: str | None = None,
        exits=(0,),
        env: dict | None = None,
        cwd: str | None = None,
        respawn: float | bool | None = None,
        respawn_max: int = -1,
        required: bool = False,
    ) -> Proc:
        """Start ``cmd`` (an argv, or a shell command line) as ``window:name``."""
        self.check()
        window = window or self.window_for(self.namespace)
        argv = None
        if isinstance(cmd, str):
            command = cmd
        else:
            argv = with_prefix(list(cmd), self.prefix, self.prefix_filter)
            command = shlex.join(argv)
        if cwd:
            command = f"cd {shlex.quote(cwd)} && {command}"
        respawn = 0.0 if respawn is True else (respawn or None)
        proc = Proc(
            self, name, window, command, argv, fqn, _codes(exits), respawn,
            respawn_max, required,
        )  # fmt: skip
        if self.log_commands:
            log(f"{proc.key}: {command}")
        if self.dry:
            self.plan.append(Planned(proc.key, command, argv))
        else:
            self.procs.start(
                proc.key,
                command,
                exits=proc.exits,
                env={**BASE_ENV, **self.env, **(env or {})},
            )
        self.started.append(proc)
        return proc

    def _find(self, key: str) -> Proc | None:
        for proc in reversed(self.started):
            if proc.key == key:
                return proc
        return None

    def _exited(self, key: str, status, died: bool) -> None:  # a watcher thread
        if self._chained is not None:
            self._chained(key, status, died)
        proc = self._find(key)
        if proc is None or proc.stopping or self.closing:
            return
        code = code_of(status)
        if proc.respawn is not None and (
            proc.respawn_max < 0 or proc.respawns < proc.respawn_max
        ):
            proc.respawns += 1
            proc.pending = True
            log(f"{proc.who} ended ({why(code)}); respawning in {proc.respawn:g} s", "WARN")
            threading.Timer(proc.respawn, self._respawn, args=(proc,)).start()
        elif died:
            self.deaths.append(proc)
            log(f"{proc.who} died unexpectedly: {why(code)}", "ERROR")
            for line in proc.output().splitlines()[-5:]:
                print(f"    | {line}", flush=True)
        else:
            log(f"{proc.key} finished ({why(code)})")
        if proc.required and not died and self.shutdown_requested is None:
            self.shutdown_requested = f"{proc.key} (required) ended"
        self._wake.set()

    def _respawn(self, proc: Proc) -> None:
        proc.pending = False
        if not (self.closing or proc.stopping):
            with contextlib.suppress(Exception):
                self.procs[proc.key].start()

    # ------------------------------------------------------------ waiting
    def check(self) -> None:
        """Raise if anything died or a shutdown was asked for: every wait calls
        this."""
        if self.deaths:
            raise ProcessDied(
                "bringup stopped: " + ", ".join(p.who for p in self.deaths) + " died"
            )
        if self.shutdown_requested is not None:
            raise Shutdown(self.shutdown_requested)

    def wait(
        self,
        ready: Callable[[], Any],
        timeout: float | None,
        what: str,
        check: bool = True,
    ):
        """Poll ``ready`` until not None. ``check``: fail on a death meanwhile
        (False: teardown's own calls)."""
        if self.dry:
            return None
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if check:
                self.check()
            result = ready()
            if result is not None:
                return result
            if deadline is not None and time.monotonic() > deadline:
                raise WaitTimeout(f"timed out after {timeout:g} s waiting for {what}")
            self._wake.wait(0.1)
            self._wake.clear()

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        self.wait(lambda: True if time.monotonic() >= end else None, None, "sleep")

    # ----------------------------------------------------------- the graph
    def graph(self):
        """Our rclpy node, on a context of its own (made on first use)."""
        if self._graph is None:
            import rclpy
            from rclpy.context import Context
            from rclpy.executors import SingleThreadedExecutor

            context = Context()
            rclpy.init(args=[], context=context)
            self._graph = rclpy.create_node(
                f"_tlaunch_{os.getpid()}", context=context
            )
            self._executor = SingleThreadedExecutor(context=context)
            self._executor.add_node(self._graph)
        return self._graph

    def call(
        self, client, request, timeout: float | None, what: str, check: bool = True
    ):
        """A service call that fails on any death meanwhile; -> the response."""
        self.wait(
            lambda: True if client.service_is_ready() else None,
            timeout,
            f"service {client.srv_name}",
            check,
        )
        future = client.call_async(request)

        def done():
            self._executor.spin_once(timeout_sec=0.05)
            return future.result() if future.done() else None

        return self.wait(done, timeout, what, check)

    def node_names(self) -> list[str]:
        node = self.graph()
        return [
            f"{ns.rstrip('/')}/{name}"
            for name, ns in node.get_node_names_and_namespaces()
            if name != node.get_name()
        ]

    def wait_for_node(self, name: str, timeout: float | None = 30.0) -> str:
        want = name if name.startswith("/") else join_namespace(self.namespace, name)
        return self.wait(
            lambda: want if want in self.node_names() else None,
            timeout,
            f"node {want} in the ROS graph",
        )

    def wait_for_topic(
        self, topic: str, timeout: float | None = 30.0, publishers: int = 1
    ) -> str:
        want = topic if topic.startswith("/") else join_namespace(self.namespace, topic)

        def ready():
            return want if self.graph().count_publishers(want) >= publishers else None

        return self.wait(ready, timeout, f"{want} to have {publishers} publisher(s)")

    # ------------------------------------------------------------ the end
    def supervise(self) -> None:
        """Up: wait for Ctrl-C, a death, shutdown(), or every process to end."""
        misses: dict[int, int] = {}
        last = time.monotonic()
        while True:
            self.check()
            if not (
                any(p.running for p in self.started) or self._respawning() or self.held
            ):
                log("every process has finished")
                return
            if self.held and time.monotonic() - last > 2.0:
                last = time.monotonic()
                self._check_held(misses)
            self._wake.wait(0.5)
            self._wake.clear()

    def _check_held(self, misses: dict[int, int]) -> None:
        """What we hold outside our processes is still there (twice missing:
        gone; graph discovery can blink)."""
        for item in list(self.held):
            reason = item.lost()
            misses[id(item)] = misses.get(id(item), 0) + 1 if reason else 0
            if misses[id(item)] >= 2:
                # nothing to release there any more
                self.held = [h for h in self.held if h.lost() != reason]
                raise LaunchError(reason)

    def _respawning(self) -> bool:
        return any(p.pending for p in self.started)

    def teardown(self) -> None:
        """Stop everything still running, all at once."""
        self.closing = True
        for item in reversed(self.held):
            try:
                item.release()
            except Exception as exc:  # the container may be gone already
                log(f"{exc}", "WARN")
        self.held.clear()
        if self.procs is not None:
            self.procs.close(check=False)
        if self._graph is not None:
            context = self._graph.context
            self._executor.shutdown()
            self._graph.destroy_node()
            context.try_shutdown()
            self._graph = None


# --------------------------------------------------------------------------
# the active run, for the body's free functions
# --------------------------------------------------------------------------
_active: Run | None = None


@contextlib.contextmanager
def active(run: Run):
    global _active
    previous, _active = _active, run
    try:
        with rc.active(run.ros):
            yield run
    finally:
        _active = previous


def current() -> Run:
    if _active is None:
        raise RuntimeError(
            "ros2_typed_launch's node(), include(), ... only work inside an "
            "@launch body run by tlaunch (or a test's procs.build)"
        )
    return _active


def make_config(model: type, config: Any = None, fields: dict | None = None):
    if config is not None:
        return config
    return schema.validate(model, fields or {})


# --------------------------------------------------------------------------
# tmux: a session of our own
# --------------------------------------------------------------------------
def open_tmux(name: str, dashboard: bool, join: bool = False):
    """The run's panes' session (wrap mode): a new detached one named
    ``name``, or with ``join`` the one we run in (only our windows are
    closed at the end)."""
    from libtmux_easy import PANE_MODE_WRAP, TmuxSession

    if join:
        session = TmuxSession.current(env=os.environ, mode=PANE_MODE_WRAP, wait_start=False)
    else:
        session = TmuxSession.create(
            name, kill_existing=False, mode=PANE_MODE_WRAP, env=os.environ, wait_start=False
        )
    if dashboard:
        session.dashboard()
    return session


def close_tmux(session, keep: bool, joined: bool = False) -> str | None:
    """Kill the run's session, or keep it to look at (a failure, a dashboard)."""
    name = session.session_name
    if keep:
        session.release(keep_windows=True)
        if joined:
            return "tmux windows kept"
        return f"tmux session kept: tmux attach -t {name}"
    session.kill_session()
    return None


# --------------------------------------------------------------------------
# a whole run
# --------------------------------------------------------------------------
def execute(typed, config, run: Run, attach_hint: str | None = None) -> int:
    """Run a body to the end: bring up, supervise, tear down. -> exit code."""
    failed = interrupted = False

    def terminate(signum, frame):
        raise KeyboardInterrupt

    def kill(signum, frame):  # a second Ctrl-C while stopping
        log("second interrupt: killing everything", "WARN")
        if run.procs is not None:
            run.procs.kill_all()

    old_term = signal.signal(signal.SIGTERM, terminate)
    try:
        with active(run):
            try:
                typed.factory(config)
                if run.dry:
                    return 0
                running = sum(p.running for p in run.started)
                held = f", {len(run.held)} held elsewhere" if run.held else ""
                log(f"up: {running} process(es) running{held}")
                if attach_hint:
                    log(attach_hint)
                run.supervise()
            except KeyboardInterrupt:
                interrupted = True
                print()
                log("interrupted; stopping")
            except Shutdown as exc:
                log(f"shutdown: {exc}")
            except LaunchError as exc:
                failed = True
                if not isinstance(exc, ProcessDied) or not run.deaths:
                    log(str(exc), "ERROR")
            except CommandError as exc:
                failed = True
                log(str(exc), "ERROR")
            except Exception:
                failed = True
                traceback.print_exc()
            finally:
                signal.signal(signal.SIGINT, kill)
                try:
                    run.teardown()
                finally:
                    signal.signal(signal.SIGINT, signal.default_int_handler)
    finally:
        signal.signal(signal.SIGTERM, old_term)

    failed = failed or bool(run.deaths)
    if run.deaths:
        print(f"{PROG}: {len(run.deaths)} process(es) died unexpectedly:")
        for proc in run.deaths:
            print(f"  {proc.who}: {why(proc.returncode)}")
    if interrupted and not failed:
        return 0
    return 1 if failed else 0
