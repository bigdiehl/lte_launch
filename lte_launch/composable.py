"""Composable nodes: where a component runs is one argument.

    component("composition", "composition::Talker", name="talker", into=where)

| ``into`` | The component |
|---|---|
| None or ``""`` | standalone: ``executable=`` as a node, else a container of its own |
| ``"box"`` (relative) | loaded into our container ``box``, started on first use |
| ``"/robot/box"`` | loaded into that container: ours if we started it, else one already running |
| a ``Container`` | loaded into it (``container()``, ``container_at()``) |

So a string config field picks the placement from the command line
(``--container ""``, ``--container box``, ``--container /robot/box``).

``load()`` calls the container's ``_container/load_node`` service and blocks
until it answers. A failed load is an error at that line (stock launch logs it
and carries on). In our container a component dies with it, and the container's
death is the death reported. In a container started elsewhere, a component is
held: the run stays up until Ctrl-C, unloads it at teardown, and fails if the
container leaves the graph.

Parameters and remappings work as for ``node()``: config-file node sections
matching the component's name, then ``params=`` (typed wins). In a dry run a
load is a ``ros2 component load`` command line in the plan.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

import yaml

from . import engine
from .api import node, node_params
from .command import fqn, join_namespace
from .engine import LaunchError, Proc

#: rcutils severities as LoadNode's log_level takes them.
_SEVERITY = {"debug": 10, "info": 20, "warn": 30, "error": 40, "fatal": 50}

#: container kinds -> rclcpp_components executables
EXECUTABLES = {
    "single": "component_container",
    "multithreaded": "component_container_mt",
    "isolated": "component_container_isolated",
}


@dataclass
class Component:
    """One component: loaded into a container, or (``proc``) a node of its own."""

    run: engine.Run
    plugin: str
    name: str  # its fully-qualified name, as the container reports it
    container: "Container | None" = None
    unique_id: int | None = None
    proc: Proc | None = None

    def wait_for_node(self, timeout: float | None = 30.0) -> str:
        return self.run.wait_for_node(self.name, timeout)

    def unload(self, timeout: float | None = 10.0) -> None:
        """Unload it from its container (a standalone node: stop it)."""
        if self.proc is not None:
            self.proc.stop()
            return
        if self in self.run.held:
            self.run.held.remove(self)
        self._unload(timeout, check=True)

    # held in a container started elsewhere: the run's teardown hooks
    def release(self) -> None:
        self._unload(3.0, check=False)

    def lost(self) -> str | None:
        return self.container.lost() if self.container is not None else None

    def _unload(self, timeout: float | None, check: bool) -> None:
        run, box = self.run, self.container
        if run.dry or box is None or self.unique_id is None:
            return
        from composition_interfaces.srv import UnloadNode

        client = run.graph().create_client(UnloadNode, f"{box.fqn}/_container/unload_node")
        reply = run.call(
            client, UnloadNode.Request(unique_id=self.unique_id), timeout,
            f"{box.fqn} to unload {self.name}", check,
        )  # fmt: skip
        if not reply.success:
            raise LaunchError(f"unloading {self.name}: {reply.error_message}")
        if run.log_commands or not check:
            engine.log(f"unloaded {self.name} from {box.fqn}")


class Container(engine.ProcHandle):
    """A component container and ``load()``: ours (``proc``), or one started
    elsewhere (``proc`` None)."""

    kind = "container"

    def __init__(self, run: engine.Run, fqn: str, proc: Proc | None = None):
        self.run = run
        self.fqn = fqn
        self.proc = proc
        self.components: list[Component] = []
        self._client = None

    def lost(self) -> str | None:
        """Why it is gone (None: still in the graph)."""
        if self.fqn in self.run.node_names():
            return None
        return f"container {self.fqn} left the ROS graph (started elsewhere)"

    def _load_client(self):
        if self._client is None:
            from composition_interfaces.srv import LoadNode

            self._client = self.run.graph().create_client(
                LoadNode, f"{self.fqn}/_container/load_node"
            )
        return self._client

    def load(
        self,
        package: str,
        plugin: str,
        *,
        name: str | None = None,
        namespace: str | None = None,
        params: Any = None,
        remaps: dict[str, str] | None = None,
        intra_process: bool = False,
        extra: dict[str, Any] | None = None,
        timeout: float | None = 30.0,
    ) -> Component:
        """Load ``plugin`` from ``package`` now; -> its handle. ``namespace``:
        relative to the current one. ``intra_process``:
        ``use_intra_process_comms``; ``extra``: other extra arguments."""
        run = self.run
        ns = join_namespace(run.namespace, namespace)
        full = fqn(name, ns)
        merged, section_remaps = node_params(run, full, params)
        rules = {**section_remaps, **(remaps or {})}
        extras = dict(extra or {})
        if intra_process:
            extras["use_intra_process_comms"] = True
        levels = [lv for lv in run.log_levels if ":=" not in lv]

        if run.dry:
            argv = ["ros2", "component", "load", self.fqn, package, plugin]
            argv += ["--node-namespace", ns] + (["--node-name", name] if name else [])
            argv += [a for k, v in merged.items() for a in ("-p", f"{k}:={_yaml(v)}")]
            argv += [a for k, v in rules.items() for a in ("-r", f"{k}:={v}")]
            argv += [a for k, v in extras.items() for a in ("-e", f"{k}:={_yaml(v)}")]
            if levels:
                argv += ["--log-level", levels[-1].upper()]
            label = f"{self.window}:{name or plugin.split('::')[-1]}"
            run.plan.append(engine.Planned(label, shlex.join(argv), argv))
            component = Component(run, plugin, full or plugin, self)
            self.components.append(component)
            return component

        from composition_interfaces.srv import LoadNode
        from rclpy.parameter import Parameter

        request = LoadNode.Request(
            package_name=package,
            plugin_name=plugin,
            node_name=name or "",
            node_namespace=ns,
            log_level=_SEVERITY[levels[-1]] if levels else 0,
            remap_rules=[f"{k}:={v}" for k, v in rules.items()],
            parameters=[Parameter(k, value=v).to_parameter_msg() for k, v in merged.items()],
            extra_arguments=[
                Parameter(k, value=v).to_parameter_msg() for k, v in extras.items()
            ],
        )
        reply = run.call(self._load_client(), request, timeout, f"{self.fqn} to load {plugin}")
        if not reply.success:
            raise LaunchError(
                f"{self.fqn}: failed to load {plugin} ({package}) as "
                f"{full or 'its default name'}: {reply.error_message}"
            )
        component = Component(run, plugin, reply.full_node_name, self, reply.unique_id)
        self.components.append(component)
        if self.external:
            run.held.append(component)
        if run.log_commands or self.external:
            engine.log(f"{self.fqn}: loaded {plugin} as {reply.full_node_name}")
        return component


def container(
    name: str,
    *,
    namespace: str | None = None,
    kind: str = "single",
    package: str = "rclcpp_components",
    executable: str | None = None,
    params: Any = None,
    window: str | None = None,
    arguments: list[str] | None = None,
    **node_options,
) -> Container:
    """Start a component container now; -> its handle. ``kind``: ``single``,
    ``multithreaded`` or ``isolated`` (rclcpp_components'), unless
    ``package``/``executable`` name another. Other keywords go to ``node()``."""
    if executable is None:
        if kind not in EXECUTABLES:
            raise ValueError(f"container kind '{kind}': one of {', '.join(EXECUTABLES)}")
        executable = EXECUTABLES[kind]
    run = engine.current()
    full = fqn(name, join_namespace(run.namespace, namespace))
    if full in run.containers:
        raise LaunchError(f"container {full} is already started")
    proc = node(
        package, executable, name=name, namespace=namespace, params=params,
        window=window, arguments=arguments, **node_options,
    )  # fmt: skip
    box = run.containers[full] = Container(run, full, proc)
    return box


def container_at(name: str, *, timeout: float | None = 30.0) -> Container:
    """A container started elsewhere, by full name (relative: in the current
    namespace); waits for its load service now. -> its handle. One we started
    is returned as is."""
    run = engine.current()
    full = name if name.startswith("/") else join_namespace(run.namespace, name)
    if full in run.containers:
        return run.containers[full]
    box = run.containers[full] = Container(run, full)
    if not run.dry:
        client = box._load_client()
        try:
            run.wait_for_service(
                client, timeout, f"container {full} (its {client.srv_name} service)"
            )
        except engine.WaitTimeout:
            del run.containers[full]
            raise
    return box


def component(
    package: str,
    plugin: str,
    *,
    name: str,
    into: "Container | str | None" = None,
    executable: str | None = None,
    namespace: str | None = None,
    params: Any = None,
    remaps: dict[str, str] | None = None,
    intra_process: bool = False,
    extra: dict[str, Any] | None = None,
    timeout: float | None = 30.0,
    **node_options,
) -> Component:
    """Run ``plugin`` where ``into`` says (see the module docstring). Other
    keywords go to ``node()`` (standalone) or the container's ``node()``."""
    if into:
        box = _resolve(into)
    elif executable:
        proc = node(
            package, executable, name=name, namespace=namespace, params=params,
            remaps=remaps, **node_options,
        )  # fmt: skip
        return Component(engine.current(), plugin, proc.fqn or name, proc=proc)
    else:
        box = container(f"{name}_container", namespace=namespace, **node_options)
    return box.load(
        package, plugin, name=name, namespace=namespace, params=params,
        remaps=remaps, intra_process=intra_process, extra=extra, timeout=timeout,
    )  # fmt: skip


def _resolve(into: "Container | str") -> Container:
    if isinstance(into, Container):
        return into
    run = engine.current()
    full = into if into.startswith("/") else join_namespace(run.namespace, into)
    if full in run.containers:
        return run.containers[full]
    if into.startswith("/"):
        return container_at(full)
    where, _, base = into.rpartition("/")
    return container(base, namespace=where or None)


def _yaml(value: Any) -> str:
    return yaml.safe_dump(value, default_flow_style=True).strip().removesuffix("\n...")
