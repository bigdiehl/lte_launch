"""Config sources and layering: defaults < --config files (in order) < CLI.

A config file has two parts: the launch's own settings under ``launch_args:``
(validated against its schema) and node-pattern sections carrying ROS parameters
and remappings (untyped, see ``ros_config.py``). ``$(...)`` in any value is
expanded when the file is read (see ``subst.py``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from . import ros_config as rc
from . import subst


class ConfigFileError(ValueError):
    pass


_PACKAGE_REF = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.+)$")


def _package_share_dir(package: str) -> str:
    from ament_index_python.packages import (
        PackageNotFoundError,
        get_package_share_directory,
    )

    try:
        return get_package_share_directory(package)
    except PackageNotFoundError:
        raise ConfigFileError(f"package '{package}' not found") from None


def resolve_config_ref(ref: str) -> str:
    """Turn a ``--config`` argument into a file path.

    ``<package>:<relative/path.yaml>`` refers to a file in that package's share
    directory (the same place ``tlaunch <package> <launch_file>`` looks). A path
    that exists on disk always wins over that reading.
    """
    if os.path.exists(ref):
        return ref
    match = _PACKAGE_REF.match(ref)
    if match is None:
        return ref  # not a package reference; let the loader report it
    package, relative = match.groups()
    path = os.path.join(_package_share_dir(package), relative.lstrip("/"))
    if not os.path.isfile(path):
        raise ConfigFileError(
            f"'{ref}': no file '{relative}' in the share directory of "
            f"package '{package}' (looked for {path})"
        )
    return path


def load_config_file(path: str) -> dict:
    """Read a YAML config file; the top level must be a mapping.

    ``$(...)`` substitutions are expanded here, once, for the whole file.
    """
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise ConfigFileError(f"cannot read config file '{path}': {exc}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigFileError(f"invalid YAML in '{path}': {exc}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigFileError(
            f"config file '{path}' must contain a mapping at the top level"
        )
    try:
        return subst.expand(data, path)
    except subst.SubstitutionError as exc:
        raise ConfigFileError(str(exc)) from None


def split_file(data: dict, source: str) -> tuple[dict, list[rc.Section]]:
    """One loaded config file -> (launch_args, node sections).

    The launch's own settings live under ``launch_args:``; every other top-level
    key must be a node pattern (see ``ros_config.is_node_pattern``). A bare
    schema key at the top level is the pre-``launch_args`` file shape, and gets
    an error saying so rather than being silently ignored.
    """
    launch_args = data.get(rc.LAUNCH_ARGS_KEY, {})
    if launch_args is None:
        launch_args = {}
    if not isinstance(launch_args, dict):
        raise ConfigFileError(
            f"in '{source}': '{rc.LAUNCH_ARGS_KEY}' must be a mapping"
        )

    stray = [
        key for key, value in data.items()
        if key != rc.LAUNCH_ARGS_KEY and not rc.is_node_pattern(key, value)
    ]
    if stray:
        raise ConfigFileError(
            f"in '{source}': top-level key(s) {', '.join(repr(s) for s in stray)} "
            f"are neither '{rc.LAUNCH_ARGS_KEY}' nor a node pattern; this launch's "
            f"own settings now go under '{rc.LAUNCH_ARGS_KEY}:', and node "
            f"parameters under a pattern like '/**:' with '{rc.PARAMS_KEY}:'"
        )

    try:
        return launch_args, rc.parse_sections(data, source)
    except rc.RosConfigError as exc:
        raise ConfigFileError(str(exc)) from None


def deep_merge(base: dict, over: dict) -> dict:
    """Merge ``over`` onto ``base``: dicts merge key by key, anything else
    (including lists) in ``over`` replaces the value in ``base``."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def nest(flat: dict[str, Any]) -> dict:
    """{"a.b": 1} -> {"a": {"b": 1}}"""
    out: dict = {}
    for dotted, value in flat.items():
        cur = out
        *parents, leaf = dotted.split(".")
        for part in parents:
            cur = cur.setdefault(part, {})
        cur[leaf] = value
    return out


def lookup(data: dict, path: tuple[str, ...]) -> tuple[bool, Any]:
    """Find ``path`` in a nested dict -> (found, value)."""
    cur: Any = data
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur
