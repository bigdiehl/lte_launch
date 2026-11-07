"""What a launch body calls: ``node()``, ``process()``, ``include()``, scopes and waits.

    from ros2_typed_launch import launch, node, namespace

    @launch(config=Config)
    def bringup(config):
        with namespace(config.ns):
            sim = node("turtlesim", "turtlesim_node", name="sim")   # starts now
            sim.wait_for_node()                                     # blocks
            node("turtlesim", "turtle_teleop_key", name="teleop", window="input")

Each call acts at once on the run ``tlaunch`` (or a test) set up; see
``engine.py``. Importable without ROS; ROS is touched only when something
starts.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

from . import engine
from . import ros_config as rc
from . import schema
from .command import find_executable, fqn, join_namespace, node_command
from .command import params_file, stock_arg
from .engine import Proc


def node_params(run: engine.Run, full: str | None, params: Any):
    """A node's parameters and remappings, lowest first: the enclosing
    ``ros_params()``/``ros_remaps()`` scopes, every config-file section
    matching ``full``, then ``params`` (a RosParams model or a dict; typed
    wins). The caller puts its own ``remaps=`` over the remappings."""
    typed: dict[str, Any] = {}
    if params is not None:
        if schema.is_ros_params(params):
            typed = rc.flatten_params(schema.to_plain(params))
        elif isinstance(params, dict):
            typed = rc.flatten_params(params)
        else:
            raise TypeError(
                f"params= takes a RosParams model or a dict, got {type(params).__name__}"
            )
    untyped, remaps = run.ros.for_node(full)
    clashes = sorted(set(typed) & set(untyped))
    if clashes:
        # Advisory, so only under -d: the effective value is the typed one.
        run.ros.note_shadowed(full, clashes)
        if run.ros.debug:
            print(
                f"tlaunch: warning: {full}: ros__parameters also sets "
                f"{', '.join(repr(c) for c in clashes)}, which this launch declares "
                f"as typed config; the typed value wins "
                f"({', '.join(f'{c}={typed[c]!r}' for c in clashes)})",
                file=sys.stderr,
            )
    return (
        {**run.scope_params, **untyped, **typed},
        {**run.scope_remaps, **remaps},
    )


def node(
    package: str,
    executable: str,
    *,
    name: str | None = None,
    namespace: str | None = None,
    params: Any = None,
    param_files: list[str] | None = None,
    remaps: dict[str, str] | None = None,
    arguments: list[str] | None = None,
    ros_arguments: list[str] | None = None,
    window: str | None = None,
    ready: str | None = None,
    exits=(0,),
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    respawn: float | bool | None = None,
    respawn_max: int = -1,
    required: bool = False,
    stop_timeout: float | None = None,
    timeout: float = 30.0,
    prefix: str | list[str] | None = None,
) -> Proc:
    """Start a ROS node now; -> its handle.

    ``params``: a ``RosParams`` model or a dict; config-file node sections
    matching this node are merged under it (typed wins). ``param_files``:
    parameter files given as they are, under both. ``ready``: wait for a line
    matching it before returning. ``exits``: codes it may end with on its own
    (None: any end is a death). ``respawn``: start it again after that many
    seconds when it ends (``True``: at once), ``respawn_max`` times (-1: no
    limit). ``required``: when it ends, the run ends (a death if its code is
    not in ``exits``). ``prefix``: a command to run it under (``"gdb -ex run
    --args"``), instead of ``--launch-prefix`` (``""``: none); a debugger
    needs a TTY, so ``--tmux``.
    ``stop_timeout``: seconds it gets after SIGINT before SIGTERM, when
    stopped (default: ``--stop-timeout``, 10 s; stock: ``sigterm_timeout``).
    """
    run = engine.current()
    ns = join_namespace(run.namespace, namespace)
    full = fqn(name, ns)
    merged, section_remaps = node_params(run, full, params)
    exe = find_executable(package, executable)
    pname = name or executable
    win = window or run.window_for(ns)
    path = params_file(merged, run.params_dir, f"{win}.{pname}") if merged else None
    argv = node_command(
        exe,
        name=name,
        namespace=ns,
        params_path=path,
        remaps={**section_remaps, **(remaps or {})},
        arguments=arguments,
        log_levels=run.log_levels,
        param_files=param_files,
        ros_arguments=ros_arguments,
    )
    proc = run.start(
        pname, argv, window=win, fqn=full, exits=exits, env=env, cwd=cwd,
        respawn=respawn, respawn_max=respawn_max, required=required,
        prefix=prefix,
        stop_timeout=stop_timeout,
    )  # fmt: skip
    if ready:
        proc.wait_for(ready, timeout)
    return proc


def process(
    name: str,
    cmd: list[str] | str,
    *,
    window: str | None = None,
    ready: str | None = None,
    exits=(0,),
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    respawn: float | bool | None = None,
    respawn_max: int = -1,
    required: bool = False,
    stop_timeout: float | None = None,
    timeout: float = 30.0,
    prefix: str | list[str] | None = None,
) -> Proc:
    """Start any command now; -> its handle. A string is a shell command line,
    a list an argv. The rest as for ``node()``."""
    proc = engine.current().start(
        name, cmd, window=window, exits=exits, env=env, cwd=cwd, respawn=respawn,
        respawn_max=respawn_max, required=required, prefix=prefix,
        stop_timeout=stop_timeout,
    )  # fmt: skip
    if ready:
        proc.wait_for(ready, timeout)
    return proc


def include(target, config: Any = None, /, **fields):
    """Include another launch.

    * a typed launch: its body runs here, in this run and scope -- a plain
      function call. ``config`` or keyword fields. -> what the body returns.
    * ``include("pkg", "file.launch.py", **args)``, or ``include(path, **args)``:
      a stock launch file, run as one ``ros2 launch`` process. Args go
      through ``stock_arg``. -> its handle. A process it reports dead
      (``process has died``) is a death of this run, as one of ours is.
      Options, not passed on: ``name=``, ``window=``; ``exits=`` codes its
      processes (and ``ros2 launch``) may end with (None: any end is a
      death); ``allow=`` names of its processes whose end is no death
      (``talker``, for one it respawns itself); ``ready=``, ``timeout=`` as
      for ``process()``.
    """
    from .core import TypedLaunch

    if isinstance(target, TypedLaunch):
        return target.factory(engine.make_config(target.config_type, config, fields))
    if isinstance(target, str) and (isinstance(config, str) or config is None):
        file = config or target
        name = fields.pop("name", None) or os.path.basename(file).split(".")[0]
        window = fields.pop("window", None)
        exits = fields.pop("exits", (0,))
        allow = fields.pop("allow", ())
        ready = fields.pop("ready", None)
        timeout = fields.pop("timeout", 30.0)
        argv = ["ros2", "launch", target] + ([config] if config else [])
        argv += [f"{k}:={stock_arg(v)}" for k, v in fields.items()]
        run = engine.current()
        proc = run.start(
            name, argv, window=window, exits=exits,
            on_line=run.stock_watch(exits, allow),
        )  # fmt: skip
        if ready:
            proc.wait_for(ready, timeout)
        return proc
    raise TypeError(
        "include(typed_launch, config), include('package', 'file.launch.py', ...) "
        "or include('path/file.launch.py', ...)"
    )


def namespace(ns: str):
    """``with namespace("demo"):`` -- nodes started inside get it."""
    return engine.current().in_namespace(ns)


def window(name: str):
    """``with window("input"):`` -- processes started inside share a tmux window."""
    return engine.current().in_window(name)


def environment(**values: str):
    """``with environment(RMW_IMPLEMENTATION="rmw_cyclonedds_cpp"):`` -- processes
    started inside get these variables."""
    return engine.current().in_env({k: str(v) for k, v in values.items()})


def ros_params(values: Any = None, /, **more: Any):
    """``with ros_params(use_sim_time=True):`` -- every node, container and
    component started inside gets these ROS parameters (stock:
    ``SetParameter``). A dict (or RosParams model) for dotted names. Nested
    scopes merge, the inner wins; config-file sections and the node's own
    ``params=`` win over them."""
    if values is None:
        values = {}
    elif schema.is_ros_params(values):
        values = schema.to_plain(values)
    elif not isinstance(values, dict):
        raise TypeError(
            "ros_params() takes a dict or a RosParams model, "
            f"got {type(values).__name__}"
        )
    return engine.current().in_ros(params=rc.flatten_params({**values, **more}))


def ros_remaps(rules: dict[str, str]):
    """``with ros_remaps({"/tf": "tf"}):`` -- every node, container and
    component started inside gets these remappings (stock: ``SetRemap``).
    Nested scopes merge, the inner wins; config-file sections and the node's
    own ``remaps=`` win over them."""
    checked = {
        rc.check_name(src, "ros_remaps()"): rc.check_name(dst, "ros_remaps()")
        for src, dst in rules.items()
    }
    return engine.current().in_ros(remaps=checked)


def share(package: str, *path: str) -> str:
    """``<package's share dir>/<path...>`` (stock: ``FindPackageShare``)."""
    from ament_index_python.packages import get_package_share_directory

    return os.path.join(get_package_share_directory(package), *path)


def wait_for_node(name: str, timeout: float | None = 30.0) -> str:
    """Wait until ``name`` (relative: to the current namespace) is in the graph."""
    return engine.current().wait_for_node(name, timeout)


def wait_for_topic(topic: str, timeout: float | None = 30.0, publishers: int = 1):
    """Wait until ``topic`` has ``publishers`` publishers."""
    return engine.current().wait_for_topic(topic, timeout, publishers)


def sleep(seconds: float) -> None:
    """Sleep; a death meanwhile fails it (stock: ``TimerAction``)."""
    engine.current().sleep(seconds)


def shutdown(reason: str = "shutdown()") -> None:
    """Stop everything and end the run cleanly, from here (stock: ``Shutdown``)."""
    run = engine.current()
    if not run.dry:
        raise engine.Shutdown(reason)


def on_shutdown(fn: Callable | None = None, /, *, timeout: float | None = 10.0):
    """Call ``fn`` when the run ends, however it ends (Ctrl-C, ``shutdown()``,
    a death, a ``required`` process ending, a failed bringup), *before* any
    process is signalled: nodes are still up for a last service call. Last
    registered, first called. ``fn(reason)`` gets an ``engine.Reason`` (a str
    with ``.kind``: interrupted, shutdown, died, failed, finished) if it takes
    an argument. Waits work inside it; an error, or running past ``timeout``
    s, is logged and fails the run (exit 1), and the rest still run. Not
    called in a dry run. Also a decorator: ``@on_shutdown``,
    ``@on_shutdown(timeout=30)``. -> ``fn``. (stock: ``OnShutdown``)"""
    run = engine.current()

    def register(fn: Callable) -> Callable:
        run.on_shutdown(fn, timeout)
        return fn

    return register(fn) if fn is not None else register


def log(message: str) -> None:
    """Print in tlaunch's style; silent in a dry run (nothing really happened)."""
    if not engine.current().dry:
        engine.log(message)
