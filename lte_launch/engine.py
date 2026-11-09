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

import collections
import contextlib
import inspect
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

PROG = "lte-launch"

#: Every process gets these: output as it happens, not when a buffer fills.
BASE_ENV = {"PYTHONUNBUFFERED": "1", "RCUTILS_LOGGING_BUFFERED_STREAM": "0"}


class LaunchError(RuntimeError):
    """The bringup failed; already reported, so no traceback."""


class ProcessDied(LaunchError):
    pass


class WaitTimeout(LaunchError):
    pass


def supports_kill_timeout(procs: Any) -> bool:
    """Whether this libtmux_easy ``Processes`` (a group, or the class) has a
    SIGTERM grace, ``start(sigterm_timeout=)``: libtmux_easy 0.5.0 on."""
    start = getattr(procs, "start", None)
    try:
        return "sigterm_timeout" in inspect.signature(start).parameters
    except (TypeError, ValueError):
        return False


KILL_TIMEOUT_NEEDS = (
    "needs libtmux_easy 0.5.0 or later (Processes(sigterm_timeout=)); "
    "this one has a fixed 5 s SIGTERM grace"
)


class Shutdown(Exception):
    """``shutdown()`` from the body: stop everything, cleanly."""


class Reason(str):
    """Why a run ended, as ``on_shutdown`` callbacks get it: the text, and
    ``kind``: interrupted, shutdown, died, failed or finished."""

    kind: str

    def __new__(cls, kind: str, text: str | None = None) -> "Reason":
        self = super().__new__(cls, text or kind)
        self.kind = kind
        return self


def reason_for(exc: BaseException | None, run: "Run") -> Reason:
    """The Reason a run ends with ``exc`` (None: every process finished)."""
    if exc is None:
        return Reason("finished", "every process has finished")
    if isinstance(exc, KeyboardInterrupt):
        return Reason("interrupted")
    if isinstance(exc, Shutdown):
        return Reason("shutdown", str(exc))
    if run.deaths:
        return Reason("died", ", ".join(p.who for p in run.deaths) + " died")
    return Reason("failed", str(exc) or type(exc).__name__)


def _takes_reason(fn: Callable) -> bool:
    try:
        inspect.signature(fn).bind(None)
        return True
    except (TypeError, ValueError):
        return False


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


# --------------------------------------------------------------------------
# a stock include's processes: what 'ros2 launch' prints about them
# --------------------------------------------------------------------------
#: 'ros2 launch' reports the end of a process it started, then keeps running
#: (and exits 0 at the end whatever happened). As lte_launch_test's
#: ``launch_death``, plus the clean end.
STOCK_DIED = re.compile(
    r"\[ERROR\] \[(?P<name>[^\]\s]+)\]: process has died "
    r"\[pid (?P<pid>\d+), exit code (?P<code>-?\d+)"
)
STOCK_FINISHED = re.compile(
    r"\[INFO\] \[(?P<name>[^\]\s]+)\]: process has finished cleanly "
    r"\[pid (?P<pid>\d+)\]"
)


def stock_ended(line: str) -> tuple[str, int, int] | None:
    """A 'ros2 launch' line about a process that ended -> (its name as stock
    names it, e.g. ``talker-1``; pid; exit code, -N for signal N), or None."""
    m = STOCK_DIED.search(line)
    if m:
        return m["name"], int(m["pid"]), int(m["code"])
    m = STOCK_FINISHED.search(line)
    if m:
        return m["name"], int(m["pid"]), 0
    return None


def _codes(exits) -> tuple | None:
    if exits is None:
        return None
    return (exits,) if isinstance(exits, int) else tuple(exits)


# --------------------------------------------------------------------------
# event callbacks: on_exit, on_output, on_state
# --------------------------------------------------------------------------
@dataclass(eq=False)
class Handler:
    """One registered callback: ``fn`` for ``event`` of ``source`` (a
    ``Proc`` or a ``LifecycleNode``), where ``match`` (a regex, a state label
    or None) fits the value."""

    source: Any
    event: str  # exit, output, state
    fn: Callable
    match: Any = None
    once: bool = False

    def fits(self, value) -> bool:
        if self.match is None:
            return True
        if isinstance(self.match, re.Pattern):
            return bool(self.match.search(value))
        return self.match == value


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
    stop_timeout: float | None = None  # SIGINT's grace; None: the group's
    kill_timeout: float | None = None  # SIGTERM's grace; None: the group's
    respawns: int = 0
    pending: bool = False  # respawning: until the new run's exit is handled
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

    # ----------------------------------------------------------- callbacks
    def on_exit(self, fn: Callable | None = None, /, *, once: bool = False):
        """``fn(code)`` (or ``fn()``) each time it ends on its own and that is
        no death: a code in ``exits``, or a respawn. Not for a death, nor for
        ``stop()``/``restart()`` or teardown. Runs on the main thread (see
        ``Run.dispatch``). A decorator too; -> ``fn``."""
        return self.run.register(self, "exit", fn, None, once)

    def on_output(
        self, pattern: str, fn: Callable | None = None, /, *, once: bool = False
    ):
        """``fn(line)`` (or ``fn()``) for each line matching ``pattern`` it
        prints from now on (not lines already read). A decorator too."""
        return self.run.register(self, "output", fn, re.compile(pattern), once)

    def off(self, fn: Callable | None = None) -> int:
        """Unregister ``fn`` (None: every callback of this process); ->
        how many were removed."""
        return self.run.off(fn, (self,))


class ProcHandle:
    """A handle on something that may be one of our processes (``proc``) or
    started elsewhere (``proc`` None): the process handle's attributes (pid,
    stop, wait_for, ...) pass through."""

    kind = "node"  # for messages
    run: "Run"
    proc: Proc | None
    fqn: str

    @property
    def external(self) -> bool:
        return self.proc is None

    @property
    def window(self) -> str:
        """Its process's window, else the current scope's (a dry run's label)."""
        if self.proc is not None:
            return self.proc.window
        return self.run.window_for(self.run.namespace)

    def __getattr__(self, attr):
        proc = self.__dict__.get("proc")
        if proc is None:
            raise AttributeError(
                f"{attr}: {self.kind} {self.__dict__.get('fqn')} was started elsewhere"
            )
        return getattr(proc, attr)


@dataclass
class InnerDeath:
    """A process a stock include started that died: in ``run.deaths`` beside
    our own ``Proc``s, with the same ``who``/``returncode``/``output()``."""

    parent: Proc  # the include's ``ros2 launch``
    name: str  # stock's name for it: ``talker-1``
    pid: int
    returncode: int

    @property
    def who(self) -> str:
        return f"{self.name} in {self.parent.who}"

    def output(self) -> str:
        """Its lines of the include's output (stock prefixes them ``[name]``)."""
        tag = f"[{self.name}] "
        return "\n".join(
            line for line in self.parent.output().splitlines() if line.startswith(tag)
        )


def _base(name: str) -> str:
    """``talker-1`` -> ``talker``: stock's name without its counter."""
    return re.sub(r"-\d+$", "", name)


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
        self.params_dir = params_dir or tempfile.mkdtemp(prefix="lte-launch-params-")
        self.log_levels = list(log_levels or [])
        self.log_commands = log_commands
        self.prefix = shlex.split(launch_prefix) if launch_prefix else []
        self.prefix_filter = launch_prefix_filter
        self.started: list[Proc] = []
        self.plan: list[Planned] = []
        self.deaths: list[Proc | InnerDeath] = []
        self.closing = False
        self.shutdown_requested: str | None = None
        #: ``on_shutdown`` callbacks: (fn, timeout), called last first
        self.callbacks: list[tuple[Callable, float | None]] = []
        self.callback_failed = False
        self._ending = False  # the callbacks are running
        self._cut_all = False  # a second Ctrl-C: every callback thread
        self._cut: set[int] = set()  # threads of callbacks we stopped waiting for
        #: things outside our processes this run keeps alive until teardown
        #: (components in another's container): ``.lost()``, ``.release()``
        self.held: list = []
        #: containers by full name (``composable``)
        self.containers: dict = {}
        #: lifecycle nodes (``lifecycle``): sent ``shutdown`` at teardown
        self.lifecycle: list = []
        #: event callbacks; (handler, value) queued from watcher/reader
        #: threads, called on the main thread by ``dispatch()``
        self.handlers: list[Handler] = []
        self._events: collections.deque = collections.deque()
        self._dispatching = False
        self.subscribed = False  # a callback needs our rclpy node spun
        self._wake = threading.Event()
        self._namespaces: list[str] = ["/"]
        self._windows: list[str | None] = [None]
        self._envs: list[dict] = [{}]
        #: ``ros_params()``/``ros_remaps()`` scopes: (parameters, remappings)
        self._ros: list[tuple[dict, dict]] = [({}, {})]
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

    @property
    def scope_params(self) -> dict:
        """ROS parameters every node started here gets (under its own)."""
        return self._ros[-1][0]

    @property
    def scope_remaps(self) -> dict:
        """Remappings every node started here gets (under its own)."""
        return self._ros[-1][1]

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

    @contextlib.contextmanager
    def in_ros(self, params: dict | None = None, remaps: dict | None = None):
        """Nested scopes merge; the inner one wins."""
        outer_params, outer_remaps = self._ros[-1]
        self._ros.append(
            ({**outer_params, **(params or {})}, {**outer_remaps, **(remaps or {})})
        )
        try:
            yield
        finally:
            self._ros.pop()

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
        prefix: str | list[str] | None = None,
        on_line: Callable[[Proc, str], None] | None = None,
        stop_timeout: float | None = None,
        kill_timeout: float | None = None,
    ) -> Proc:
        """Start ``cmd`` (an argv, or a shell command line) as ``window:name``.

        ``prefix``: put before the command (``"gdb -ex run --args"``) instead
        of the run's ``--launch-prefix``, whatever its filter (``""``: none).
        ``on_line(proc, line)``: each line it prints, from a reader thread.
        ``stop_timeout``: seconds SIGINT gets before SIGTERM when stopped;
        ``kill_timeout``: seconds SIGTERM then gets before SIGKILL.
        """
        self.check()
        if (
            kill_timeout is not None
            and self.procs is not None
            and not supports_kill_timeout(self.procs)
        ):
            raise LaunchError(f"{name}: kill_timeout= {KILL_TIMEOUT_NEEDS}")
        window = window or self.window_for(self.namespace)
        argv = None
        words = shlex.split(prefix) if isinstance(prefix, str) else prefix
        if isinstance(cmd, str):  # the run's prefix never applies to a shell line
            command = f"{shlex.join(words)} {cmd}" if words else cmd
        else:
            if words is None:
                argv = with_prefix(list(cmd), self.prefix, self.prefix_filter)
            else:
                argv = [*words, *cmd]
            command = shlex.join(argv)
        if cwd:
            command = f"cd {shlex.quote(cwd)} && {command}"
        respawn = 0.0 if respawn is True else (respawn or None)
        proc = Proc(
            self, name, window, command, argv, fqn, _codes(exits), respawn,
            respawn_max, required, stop_timeout, kill_timeout,
        )  # fmt: skip
        if self.log_commands:
            log(f"{proc.key}: {command}")
        if self.dry:
            self.plan.append(Planned(proc.key, command, argv))
        else:
            def hook(line: str) -> None:  # a reader thread
                if on_line is not None:
                    on_line(proc, line)
                self.emit(proc, "output", line)

            self.procs.start(
                proc.key,
                command,
                exits=proc.exits,
                env={**BASE_ENV, **self.env, **(env or {})},
                fail_on=[hook],  # returns None: never a fail_on hit itself
                sigint_timeout=stop_timeout,
                # only when asked: an older libtmux_easy has no such option
                **({} if kill_timeout is None else {"sigterm_timeout": kill_timeout}),
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
            if proc is not None:
                proc.pending = False
            return
        code = code_of(status)
        respawns = proc.respawns
        death = False
        if proc.respawn is not None and (
            proc.respawn_max < 0 or proc.respawns < proc.respawn_max
        ):
            proc.respawns += 1
            proc.pending = True
            log(f"{proc.who} ended ({why(code)}); respawning in {proc.respawn:g} s", "WARN")
            threading.Timer(proc.respawn, self._respawn, args=(proc,)).start()
        elif died:
            death = True
            self._died(proc, code)
        else:
            log(f"{proc.key} finished ({why(code)})")
        if not death:
            self.emit(proc, "exit", code)
        if proc.required and not died and self.shutdown_requested is None:
            self.shutdown_requested = f"{proc.key} (required) ended"
        if proc.respawns == respawns:  # no new respawn scheduled: settled
            proc.pending = False
        self._wake.set()

    def stock_watch(self, exits=(0,), allow=()) -> Callable[[Proc, str], None]:
        """An ``on_line`` for a stock include: a process its ``ros2 launch``
        reports ended with a code not in ``exits`` (None: any end) is a death
        of this run, unless its name (``talker`` or ``talker-1``) is in
        ``allow``."""
        exits, allow = _codes(exits), set(allow)

        def on_line(proc: Proc, line: str) -> None:  # a reader thread
            ended = stock_ended(line)
            if ended is None or proc.stopping or self.closing:
                return
            name, pid, code = ended
            if (exits is not None and code in exits) or {name, _base(name)} & allow:
                return
            self._died(InnerDeath(proc, name, pid, code), code)
            self._wake.set()

        return on_line

    def _died(self, who: "Proc | InnerDeath", code: int | None) -> None:
        """Record a death and report it with its last lines."""
        self.deaths.append(who)
        log(f"{who.who} died unexpectedly: {why(code)}", "ERROR")
        for line in who.output().splitlines()[-5:]:
            print(f"    | {line}", flush=True)

    def _respawn(self, proc: Proc) -> None:
        # Still pending once started: _exited() settles it when this run ends.
        # (Cleared here, supervise() could see nothing running and nothing
        # pending before the new run is up, and end the run.)
        started = False
        if not (self.closing or proc.stopping):
            with contextlib.suppress(Exception):
                self.procs[proc.key].start()
                started = True
        if not started:
            proc.pending = False
        self._wake.set()

    # ------------------------------------------------------------ waiting
    def check(self) -> None:
        """Raise if anything died or a shutdown was asked for: every wait calls
        this. Not in an on_shutdown callback: the run is ending anyway."""
        if self._is_cut():
            raise WaitTimeout("on_shutdown callback cut short")
        if self._ending:
            return
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
                self.dispatch()
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
                f"_lte_launch_{os.getpid()}", context=context
            )
            self._executor = SingleThreadedExecutor(context=context)
            self._executor.add_node(self._graph)
        return self._graph

    def spin(self, seconds: float = 0.05) -> None:
        """Handle what our rclpy node has waiting (service answers)."""
        self._executor.spin_once(timeout_sec=seconds)

    def wait_for_service(
        self, client, timeout: float | None, what: str | None = None, check: bool = True
    ) -> None:
        self.wait(
            lambda: True if client.service_is_ready() else None,
            timeout,
            what or f"service {client.srv_name}",
            check,
        )

    def call(
        self, client, request, timeout: float | None, what: str, check: bool = True
    ):
        """A service call that fails on any death meanwhile; -> the response."""
        self.wait_for_service(client, timeout, check=check)
        future = client.call_async(request)

        def done():
            self.spin()
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
            self.dispatch()
            if not (
                any(p.running for p in self.started)
                or self._respawning()
                or self.held
                or self._events
            ):
                if self._wake.wait(0.3):  # a reader may still be on its last lines
                    self._wake.clear()
                    continue
                log("every process has finished")
                return
            if self.held and time.monotonic() - last > 2.0:
                last = time.monotonic()
                self._check_held(misses)
            self._wake.wait(0.1 if self.subscribed else 0.5)
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

    # ------------------------------------------------------------ callbacks
    def register(self, source, event: str, fn, match=None, once: bool = False):
        """Add a callback (``fn`` None: -> a decorator); -> ``fn``."""

        def add(fn: Callable) -> Callable:
            self.handlers.append(Handler(source, event, fn, match, once))
            return fn

        return add(fn) if fn is not None else add

    def off(self, fn: Callable | None = None, sources: tuple = ()) -> int:
        """Remove the callbacks ``fn`` (None: any) of ``sources``; -> how many."""
        keep = [
            h for h in self.handlers
            if not ((fn is None or h.fn == fn) and h.source in sources)
        ]  # fmt: skip
        removed = len(self.handlers) - len(keep)
        self.handlers = keep
        return removed

    def emit(self, source, event: str, value) -> None:
        """Queue the callbacks ``value`` fits (any thread). Dropped once the
        run is ending: no callbacks for teardown's exits."""
        if self.closing or self._ending:
            return
        hits = [
            h for h in list(self.handlers)
            if h.source is source and h.event == event and h.fits(value)
        ]  # fmt: skip
        if hits:
            self._events.extend((h, value) for h in hits)
            self._wake.set()

    def dispatch(self) -> None:
        """Call the queued callbacks, one at a time, on the main thread:
        inside every wait and in ``supervise()``. Not inside a callback (its
        own waits queue the rest), an ``on_shutdown`` callback, or once the
        run is ending. A callback's error propagates: it fails the run as a
        body error would (``shutdown()`` in one ends it cleanly)."""
        if (
            self._dispatching
            or self._ending
            or self.closing
            or threading.current_thread() is not threading.main_thread()
        ):
            return
        if self.subscribed and self._graph is not None:
            self.spin(0)
        self._dispatching = True
        try:
            while self._events:
                handler, value = self._events.popleft()
                if handler not in self.handlers:
                    continue  # unregistered meanwhile, or a ``once`` done
                if handler.once:
                    self.handlers.remove(handler)
                args = (value,) if _takes_reason(handler.fn) else ()
                try:
                    handler.fn(*args)
                except (Shutdown, KeyboardInterrupt):
                    raise
                except BaseException:
                    name = getattr(handler.fn, "__name__", None) or repr(handler.fn)
                    log(f"on_{handler.event} callback {name} failed", "ERROR")
                    raise
                self.check()
        finally:
            self._dispatching = False

    def on_shutdown(self, fn: Callable, timeout: float | None = 10.0) -> None:
        self.callbacks.append((fn, timeout))

    def call_on_shutdown(self, reason: str = "ended") -> bool:
        """The ``on_shutdown`` callbacks, last registered first, while every
        process still runs. Each gets ``timeout`` s; an error is logged and
        the rest still run; a second Ctrl-C skips the rest. Not in a dry run.
        -> False if one failed."""
        if self.dry or not self.callbacks:
            return True
        if not isinstance(reason, Reason):
            reason = Reason("shutdown", reason)
        self.closing = True  # an exit from here on is no death, no respawn
        self._events.clear()
        ok = True
        try:
            self._ending = True
            with active(self):
                while self.callbacks:
                    fn, timeout = self.callbacks.pop()
                    ok = self._call(fn, timeout, reason) and ok
        except KeyboardInterrupt:
            self._cut_all = True
            log(f"on_shutdown: {len(self.callbacks) + 1} callback(s) cut short", "WARN")
            self.callbacks.clear()
        finally:
            self._ending = False
        self.callback_failed = self.callback_failed or not ok
        return ok

    def _call(self, fn: Callable, timeout: float | None, reason: Reason) -> bool:
        """One callback, on a thread of its own so that it can time out."""
        name = getattr(fn, "__name__", None) or repr(fn)
        args = (reason,) if _takes_reason(fn) else ()
        errors: list[BaseException] = []

        def call():
            try:
                fn(*args)
            except Shutdown:
                pass
            except BaseException as exc:  # noqa: BLE001 -- reported below
                if not self._is_cut():
                    errors.append(exc)
            finally:
                self._cut.discard(threading.get_ident())  # idents are reused

        log(f"on_shutdown: {name} ({reason})")
        thread = threading.Thread(target=call, name=f"on_shutdown {name}", daemon=True)
        thread.start()
        deadline = None if timeout is None else time.monotonic() + timeout
        while thread.is_alive():
            if deadline is not None and time.monotonic() > deadline:
                self._cut.add(thread.ident)  # its next wait raises; left to end
                log(f"on_shutdown: {name} still running after {timeout:g} s", "ERROR")
                return False
            thread.join(0.05)
        for exc in errors:
            log(f"on_shutdown: {name} failed: {exc}", "ERROR")
            if not isinstance(exc, LaunchError):
                traceback.print_exception(exc)
        return not errors

    def _is_cut(self) -> bool:
        """This is a callback's thread we stopped waiting for."""
        if threading.current_thread() is threading.main_thread():
            return False
        return self._cut_all or threading.get_ident() in self._cut

    def teardown(self, reason: str | None = None) -> None:
        """Stop everything still running, all at once (each process on its
        own ``stop_timeout`` and ``kill_timeout``); see ``_end``."""
        procs = self.procs
        self._end(
            reason if reason is not None else reason_for(None, self),
            lambda: procs is not None and procs.close(check=False),
        )

    def _end(self, reason: Reason, stop: Callable[[], Any]) -> None:
        """The end of a run, in order: ``on_shutdown`` callbacks (every
        process still up), lifecycle ``shutdown``, unload held components
        (an external container may be one of ours, so before the stop),
        ``stop()``, drop our rclpy node."""
        self.call_on_shutdown(reason)
        self.closing = True
        try:
            self._release()
        finally:
            try:
                stop()
            finally:
                self.close_graph()

    def _release(self) -> None:
        """Lifecycle nodes to ``finalized``, then unload what we hold outside
        our processes (components in a container started elsewhere)."""
        if self.lifecycle:
            from .lifecycle import finalize_all

            finalize_all(self)
        for item in reversed(self.held):
            try:
                item.release()
            except Exception as exc:  # the container may be gone already
                log(f"{exc}", "WARN")
        self.held.clear()

    def bind_close(self) -> None:
        """After ``detach()``: the group's ``close()`` ends this run as
        ``teardown()`` does (``_end``), with the group's own close as the stop.
        """
        procs = self.procs
        if procs is None:
            return
        group_close = procs.close

        def close(*args, **kwargs):
            self._end(
                Reason("finished", "the test's group closed"),
                lambda: group_close(*args, **kwargs),
            )

        procs.close = close  # also what an aborted test's group calls

    def close_graph(self) -> None:
        """Drop our rclpy node, if it was made."""
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
            "lte_launch's node(), include(), ... only work inside an "
            "@launch body run by lte-launch (or a test's procs.build)"
        )
    return _active


def make_config(model: type, config: Any = None, fields: dict | None = None):
    if config is not None:
        return config
    try:
        return schema.validate(model, fields or {})
    except schema.SchemaError as exc:  # an include()'s fields: name each one
        issues = "; ".join(
            f"{'.'.join(i.path) or '(config)'}: {i.message}" for i in exc.issues
        )
        raise LaunchError(f"{model.__name__}: {issues}") from None


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
    reason = None

    def terminate(signum, frame):
        raise KeyboardInterrupt

    def kill(signum, frame):  # a second Ctrl-C while stopping
        log("second interrupt: killing everything", "WARN")
        if run.procs is not None:
            run.procs.kill_all()
        if run._ending:
            raise KeyboardInterrupt  # out of the on_shutdown callbacks

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
            except (KeyboardInterrupt, Exception) as exc:
                reason = reason_for(exc, run)
                if isinstance(exc, KeyboardInterrupt):
                    interrupted = True
                    print()
                    log("interrupted; stopping")
                elif isinstance(exc, Shutdown):
                    log(f"shutdown: {exc}")
                elif isinstance(exc, (LaunchError, CommandError)):
                    failed = True
                    if not (isinstance(exc, ProcessDied) and run.deaths):
                        log(str(exc), "ERROR")  # a death is reported already
                else:
                    failed = True
                    traceback.print_exc()
            finally:
                signal.signal(signal.SIGINT, kill)
                try:
                    run.teardown(reason)
                finally:
                    signal.signal(signal.SIGINT, signal.default_int_handler)
    finally:
        signal.signal(signal.SIGTERM, old_term)

    failed = failed or bool(run.deaths) or run.callback_failed
    if run.deaths:
        print(f"{PROG}: {len(run.deaths)} process(es) died unexpectedly:")
        for proc in run.deaths:
            print(f"  {proc.who}: {why(proc.returncode)}")
    if interrupted and not failed:
        return 0
    return 1 if failed else 0
