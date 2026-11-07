"""Lifecycle nodes: each transition is a line of the body.

    cam = lifecycle_node("pkg", "exe", name="camera")   # starts now
    cam.transition("configure")     # blocks; a failed transition raises here
    cam.activate()                  # whatever transitions reach "active"
    cam.wait_for_state("active", timeout=10)
    cam.state                       # "active"

Transitions are the node's own ``change_state`` service, called by label
(``configure``, ``activate``, ``deactivate``, ``cleanup``, ``shutdown``) from
the engine's rclpy node, on the same loop as every wait: a death meanwhile
fails the line. A transition the node refuses or fails raises ``LaunchError``
naming the state it ended in (stock launch only logs it).

``autostart=True`` (or ``"inactive"``) brings the node there as it starts.

Teardown: every lifecycle node we started that is still running is sent
``shutdown`` (all at once, 5 s at most) before the SIGINT, so its
``on_shutdown`` runs; stock launch only sends SIGINT. ``finalize=False``
opts out. ``lifecycle_at()`` handles a node started elsewhere (a stock
include, a component): never shut down by us unless ``finalize=True``.

In a dry run each transition is a ``ros2 lifecycle set`` command line in the
plan, and the state is tracked as if every transition succeeded.
"""

from __future__ import annotations

import shlex
import time

from . import engine
from .api import node
from .command import join_namespace
from .engine import LaunchError, Proc

#: primary states, as ``get_state`` labels them
PRIMARY = ("unconfigured", "inactive", "active", "finalized")

#: (from, toward) -> the next transition
_STEP = {
    ("unconfigured", "inactive"): "configure",
    ("unconfigured", "active"): "configure",
    ("inactive", "active"): "activate",
    ("inactive", "unconfigured"): "cleanup",
    ("active", "inactive"): "deactivate",
    ("active", "unconfigured"): "deactivate",
}

#: transition -> the state it leads to (a dry run's bookkeeping)
_GOAL = {
    "configure": "inactive",
    "activate": "active",
    "deactivate": "inactive",
    "cleanup": "unconfigured",
    "shutdown": "finalized",
}

#: how long teardown waits for every ``shutdown`` together
TEARDOWN_TIMEOUT = 5.0


class LifecycleNode(engine.ProcHandle):
    """A lifecycle node: ours (``proc``), or one started elsewhere."""

    kind = "lifecycle node"

    def __init__(
        self,
        run: engine.Run,
        fqn: str,
        proc: Proc | None = None,
        finalize: bool = True,
    ):
        self.run = run
        self.fqn = fqn
        self.proc = proc
        self.finalize = finalize
        self._clients: dict[str, object] = {}
        self._dry_state = "unconfigured"

    def __repr__(self) -> str:
        return f"LifecycleNode({self.fqn!r})"

    # ----------------------------------------------------------- services
    def _client(self, service: str):
        if service not in self._clients:
            from lifecycle_msgs import srv

            kind = {
                "change_state": srv.ChangeState,
                "get_state": srv.GetState,
                "get_available_transitions": srv.GetAvailableTransitions,
            }[service]
            self._clients[service] = self.run.graph().create_client(
                kind, f"{self.fqn}/{service}"
            )
        return self._clients[service]

    def get_state(self, timeout: float | None = 10.0, check: bool = True) -> str:
        """Ask the node its state now; -> its label (``"active"``, ...)."""
        if self.run.dry:
            return self._dry_state
        from lifecycle_msgs.srv import GetState

        reply = self.run.call(
            self._client("get_state"), GetState.Request(), timeout,
            f"{self.fqn} to report its state", check,
        )  # fmt: skip
        return reply.current_state.label

    @property
    def state(self) -> str:
        """The node's current state label."""
        return self.get_state()

    def transitions(self, timeout: float | None = 10.0) -> list[str]:
        """The transitions available from the current state, by label."""
        if self.run.dry:
            now = self._dry_state
            steps = {s for (a, _), s in _STEP.items() if a == now}
            return sorted(steps) + (["shutdown"] if now != "finalized" else [])
        from lifecycle_msgs.srv import GetAvailableTransitions

        reply = self.run.call(
            self._client("get_available_transitions"),
            GetAvailableTransitions.Request(), timeout,
            f"{self.fqn} to list its transitions",
        )  # fmt: skip
        return list(dict.fromkeys(t.transition.label for t in reply.available_transitions))

    # -------------------------------------------------------- transitions
    def transition(self, label: str, timeout: float | None = 30.0) -> str:
        """Trigger ``label`` now and block until it completes; -> the new
        state. A refused or failed transition raises ``LaunchError``."""
        run = self.run
        if run.dry:
            argv = ["ros2", "lifecycle", "set", self.fqn, label]
            name = self.fqn.rsplit("/", 1)[-1]
            run.plan.append(
                engine.Planned(f"{self.window}:{name}.{label}", shlex.join(argv), argv)
            )
            self._dry_state = _GOAL.get(label, self._dry_state)
            return self._dry_state

        from lifecycle_msgs.msg import Transition
        from lifecycle_msgs.srv import ChangeState

        request = ChangeState.Request(transition=Transition(label=label))
        reply = run.call(
            self._client("change_state"), request, timeout,
            f"{self.fqn} to {label}",
        )  # fmt: skip
        state = self.get_state(timeout)
        if not reply.success:
            available = self.transitions(timeout)
            hint = (
                f"; from '{state}' only {', '.join(available) or 'nothing'}"
                if label not in available
                else ""
            )
            raise LaunchError(
                f"{self.fqn}: transition '{label}' failed, now '{state}'{hint}"
            )
        engine.log(f"{self.fqn}: {label} -> {state}")
        return state

    def set_state(self, target: str, timeout: float | None = 30.0) -> str:
        """Make whatever transitions reach the primary state ``target``
        (``unconfigured``, ``inactive``, ``active``, ``finalized``)."""
        if target not in PRIMARY:
            raise ValueError(f"lifecycle state '{target}': one of {', '.join(PRIMARY)}")
        for _ in range(len(PRIMARY)):
            now = self.get_state(timeout)
            if now == target:
                return now
            step = "shutdown" if target == "finalized" else _STEP.get((now, target))
            if step is None:
                raise LaunchError(f"{self.fqn}: no transition from '{now}' toward '{target}'")
            self.transition(step, timeout)
        return self.get_state(timeout)

    def activate(self, timeout: float | None = 30.0) -> str:
        """Configure (if needed) and activate."""
        return self.set_state("active", timeout)

    def wait_for_state(self, label: str, timeout: float | None = 30.0) -> str:
        """Wait until the node reports ``label`` (it, or someone else, makes
        the transitions)."""
        client = self._client("get_state") if not self.run.dry else None

        def reached():
            if not client.service_is_ready():
                return None
            return label if self.get_state(timeout) == label else None

        result = self.run.wait(reached, timeout, f"{self.fqn} to be '{label}'")
        return label if result is None else result


def lifecycle_node(
    package: str,
    executable: str,
    *,
    name: str,
    autostart: bool | str = False,
    finalize: bool = True,
    timeout: float = 30.0,
    **node_options,
) -> LifecycleNode:
    """Start a lifecycle node now; -> its handle (the process handle's
    methods too). ``autostart``: ``True``/``"active"`` or ``"inactive"``,
    reached before returning. ``finalize``: send it ``shutdown`` at teardown,
    before the SIGINT. Other keywords go to ``node()``."""
    if autostart is True:
        autostart = "active"
    if autostart and autostart not in ("inactive", "active"):
        raise ValueError(f"autostart={autostart!r}: True, 'active' or 'inactive'")
    proc = node(package, executable, name=name, timeout=timeout, **node_options)
    run = engine.current()
    handle = LifecycleNode(run, proc.fqn, proc, finalize)
    run.lifecycle.append(handle)
    if autostart:
        handle.set_state(autostart, timeout)
    return handle


def lifecycle_at(
    name: str, *, finalize: bool = False, timeout: float | None = 30.0
) -> LifecycleNode:
    """A lifecycle node started elsewhere, by name (relative: in the current
    namespace); waits for its ``get_state`` service now. ``finalize``: send it
    ``shutdown`` at teardown (default: leave it as it is)."""
    run = engine.current()
    full = name if name.startswith("/") else join_namespace(run.namespace, name)
    for handle in run.lifecycle:
        if handle.fqn == full:
            return handle
    handle = LifecycleNode(run, full, None, finalize)
    if not run.dry:
        client = handle._client("get_state")
        run.wait_for_service(
            client, timeout, f"lifecycle node {full} (its {client.srv_name} service)"
        )
    run.lifecycle.append(handle)
    return handle


def finalize_all(run: engine.Run, timeout: float = TEARDOWN_TIMEOUT) -> None:
    """Teardown: ``shutdown`` to every lifecycle node to finalize, all at
    once; waits for the answers (or the process ending), ``timeout`` at most.
    Never raises: the processes must still be stopped."""
    if run.dry or run._graph is None:
        return
    try:
        _finalize(run, timeout)
    except Exception as exc:
        engine.log(f"lifecycle shutdown: {exc}", "WARN")


def _finalize(run: engine.Run, timeout: float) -> None:
    from lifecycle_msgs.msg import Transition
    from lifecycle_msgs.srv import ChangeState

    pending = []
    for handle in reversed(run.lifecycle):
        if not handle.finalize or (handle.proc is not None and not handle.proc.running):
            continue
        client = handle._client("change_state")
        if not client.service_is_ready():
            continue
        request = ChangeState.Request(transition=Transition(label="shutdown"))
        pending.append((handle, client.call_async(request)))

    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        run.spin()
        for item in list(pending):
            handle, future = item
            if future.done():
                pending.remove(item)
                if future.result() is not None and future.result().success:
                    engine.log(f"{handle.fqn}: shutdown -> finalized")
            elif handle.proc is not None and not handle.proc.running:
                pending.remove(item)
    for handle, _ in pending:
        engine.log(f"{handle.fqn}: no answer to shutdown in {timeout:g} s", "WARN")
