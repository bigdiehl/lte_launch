"""A ROS node's command line, without launch_ros.

What ``launch_ros.actions.Node`` does when it executes, done up front as plain
data: find the executable the way ``ros2 run`` does, then append ``--ros-args``
with the node's name, namespace, parameters file, remappings and log levels.
Pure functions; ROS is only touched to look up the package prefix.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

#: rcutils' severities, as ``--log-level`` takes them.
LEVELS = ("debug", "info", "warn", "error", "fatal")


class CommandError(ValueError):
    """A node that cannot be started: no such package or executable."""


def find_executable(package: str, executable: str) -> str:
    """``<prefix>/lib/<package>/<executable>``, as ``ros2 run`` finds it.

    Checked here, before anything starts, so a typo fails at the line that made
    it rather than as a process that dies on start.
    """
    from ament_index_python.packages import (
        PackageNotFoundError,
        get_package_prefix,
    )

    try:
        prefix = get_package_prefix(package)
    except PackageNotFoundError:
        raise CommandError(f"package '{package}' not found") from None
    libdir = Path(prefix) / "lib" / package
    path = libdir / executable
    if path.is_file() and os.access(path, os.X_OK):
        return str(path)
    have = sorted(
        p.name for p in libdir.glob("*") if p.is_file() and os.access(p, os.X_OK)
    )
    raise CommandError(
        f"no executable '{executable}' in package '{package}'"
        + (f" (it has: {', '.join(have)})" if have else "")
    )


def join_namespace(*parts: str | None) -> str:
    """Join namespaces; an absolute part restarts. ``"" -> "/"``."""
    ns = ""
    for part in parts:
        if not part:
            continue
        ns = part if part.startswith("/") else f"{ns.rstrip('/')}/{part}"
    ns = "/" + "/".join(t for t in ns.split("/") if t)
    return ns


def fqn(name: str | None, namespace: str) -> str | None:
    """Fully-qualified node name, or None when rcl will pick the name."""
    if not name:
        return None
    return f"{namespace.rstrip('/')}/{name}"


def params_file(params: dict[str, Any], directory: str, stem: str) -> str:
    """Write ``params`` as a ROS parameter file for one node; -> its path.

    ``/**`` is safe: the file is handed to this node alone.
    """
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{stem}.params.yaml")
    with open(path, "w") as f:
        yaml.safe_dump({"/**": {"ros__parameters": params}}, f, sort_keys=False)
    return path


def check_levels(values: list[str]) -> list[str]:
    """``LEVEL`` or ``LOGGER:=LEVEL`` each; -> the values, levels lower-cased.
    Raises ``ValueError`` naming the bad one."""
    checked = []
    for value in values:
        logger, sep, level = value.rpartition(":=")
        if level.lower() not in LEVELS or (sep and not logger):
            raise ValueError(
                f"--log-level '{value}': expected LEVEL or LOGGER:=LEVEL, "
                f"LEVEL one of {', '.join(LEVELS)}"
            )
        checked.append(f"{logger}{sep}{level.lower()}")
    return checked


def stock_arg(value: Any) -> str:
    """A typed value as a stock launch argument (always a string): bools are
    ``true``/``false``, an Enum its value, anything else ``str()``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    value = getattr(value, "value", value)
    return "" if value is None else str(value)


def with_prefix(argv: list[str], prefix: list[str], pattern: str | None) -> list[str]:
    """``--launch-prefix``: ``prefix + argv``, if the executable matches
    ``pattern`` (a regex searched in its path; None: every one)."""
    if not prefix or (pattern and not re.search(pattern, argv[0])):
        return argv
    return [*prefix, *argv]


def node_command(
    executable: str,
    *,
    name: str | None,
    namespace: str,
    params_path: str | None = None,
    remaps: dict[str, str] | None = None,
    arguments: list[str] | None = None,
    log_levels: list[str] | None = None,
    param_files: list[str] | None = None,
    ros_arguments: list[str] | None = None,
) -> list[str]:
    """The argv for one node. ``param_files`` come before the generated one
    and ``ros_arguments`` last, so under ``/**`` typed and config-file values
    win over the files and ``-p`` over both (rcl: later wins per node key;
    keys apply in first-seen order -- see docs/design.md)."""
    argv = [executable, *(arguments or []), "--ros-args"]
    if name:
        argv += ["-r", f"__node:={name}"]
    if namespace and namespace != "/":
        argv += ["-r", f"__ns:={namespace}"]
    for path in param_files or []:
        argv += ["--params-file", path]
    if params_path:
        argv += ["--params-file", params_path]
    for src, dst in (remaps or {}).items():
        argv += ["-r", f"{src}:={dst}"]
    for level in log_levels or []:
        argv += ["--log-level", level]
    return argv + list(ros_arguments or [])
