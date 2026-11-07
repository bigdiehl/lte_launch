"""Config sources and layering: defaults < --config files (in order) < CLI.

A config file has two parts: the launch's own settings under ``launch_args:``
(validated against its schema) and node-pattern sections carrying ROS parameters
and remappings (untyped, see ``ros_config.py``). ``$(...)`` in any value is
expanded by the stock launch parser (see ``subst.py``), but **in two stages**,
because one substitution reads the config itself:

1. ``launch_args`` are expanded as each file is read -- after their keys have been
   checked against the schema, so a key that does not belong to this launch is
   reported before a substitution that cannot be resolved;
2. the node sections are expanded **once the launch args are merged and
   validated**, which is what lets ``$(var NAME)`` in a section read a launch
   argument by its dotted path (``$(var turtlesim.namespace)``). Launch args
   cannot refer to each other -- see ``reject_vars``.
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

    ``$(...)`` substitutions are **not** expanded here -- see ``expand_values``.
    They are expanded only after the file's ``launch_args`` have been checked
    against the schema, so that a key that does not belong to this launch is
    reported before a substitution that cannot be resolved. Substitutions never
    appear in a key, so checking first is safe.
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
    return data


def expand_values(
    value: Any, source: str, variables: dict[str, str] | None = None
) -> Any:
    """Expand ``$(...)`` in every string in ``value`` (see ``subst.py``)."""
    try:
        return subst.expand(value, source, variables)
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
        key
        for key, value in data.items()
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


def variables(launch_args: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Validated launch args -> what ``$(var NAME)`` may read.

    Returns ``(usable, unusable)``: every single-valued argument by its dotted
    path, as the string a substitution produces (``true``/``false`` for bools,
    as in launch's own spelling), plus the ones that have no string form, each
    with the reason, so the error can say why rather than "does not exist".
    """
    usable: dict[str, str] = {}
    unusable: dict[str, str] = {}
    for name, value in rc.flatten_params(launch_args).items():
        if isinstance(value, bool):
            usable[name] = "true" if value else "false"
        elif isinstance(value, (int, float, str)):
            usable[name] = str(value)
        elif value is None:
            unusable[name] = "is not set"
        elif isinstance(value, list):
            unusable[name] = "is a list"
        else:
            unusable[name] = f"is a {type(value).__name__}"
    return usable, unusable


def unknown_vars(sections: list[rc.Section], known: set[str]) -> list[str]:
    """``$(var NAME)`` in a node section naming no argument of this launch.

    Checked against the **schema**, next to the unknown-key check and before
    anything is expanded, because it answers the same question: does this config
    belong to this launch at all? One line per distinct name, however many places
    use it -- a config written for another launch mentions the same argument over
    and over, and repeating it says nothing new.
    """
    import difflib

    seen: dict[str, int] = {}
    for section in sections:
        for value in (section.pattern, section.params, section.remaps):
            for name in subst.var_names(value):
                if name not in known:
                    seen[name] = seen.get(name, 0) + 1

    problems = []
    for name, count in seen.items():
        close = difflib.get_close_matches(name, sorted(known), n=1)
        hint = f" (did you mean '{close[0]}'?)" if close else ""
        places = "" if count == 1 else f", used {count} times"
        problems.append(f"$(var {name}): not an argument of this launch{places}{hint}")
    return problems


def check_vars(
    value: Any, where: str, usable: dict[str, str], unusable: dict[str, str]
) -> None:
    """Fail on a ``$(var NAME)`` we cannot answer, saying which name and why."""
    import difflib

    for name in subst.var_names(value):
        if name in usable:
            continue
        if name in unusable:
            raise ConfigFileError(
                f"{where}: $(var {name}) cannot be used: launch argument "
                f"'{name}' {unusable[name]}"
            )
        # Unknown names are normally caught against the schema, before this
        # (see unknown_vars); this is the backstop when expand_sections is
        # called on its own.
        known = sorted(usable) + sorted(unusable)
        close = difflib.get_close_matches(name, known, n=1)
        hint = f" (did you mean '{close[0]}'?)" if close else ""
        raise ConfigFileError(
            f"{where}: $(var {name}) is not an argument of this launch{hint}"
        )


def reject_vars(launch_args: dict, source: str) -> None:
    """``$(var ...)`` is not allowed in ``launch_args`` itself.

    It reads the launch's arguments, which are still being assembled at that
    point -- allowing it would mean arguments that refer to each other, and
    cycles with them.
    """
    names = subst.var_names(launch_args)
    if names:
        raise ConfigFileError(
            f"in '{source}': $(var {names[0]}) under '{rc.LAUNCH_ARGS_KEY}': a "
            "launch argument cannot be built from another one (write the value "
            "out, or use $(var ...) in a node section, where the arguments are "
            "already known)"
        )


def expand_sections(sections: list[rc.Section], launch_args: dict) -> None:
    """Expand ``$(...)`` in the untyped lanes, in place.

    Called once the launch args are final, so ``$(var NAME)`` can read them.
    """
    usable, unusable = variables(launch_args)
    for section in sections:
        where = f"{section.source}:{section.pattern}"
        # The pattern itself can be built from an argument, so a config aimed at
        # a namespaced node follows --namespace instead of hard-coding it.
        if subst.MARKER in section.pattern:
            check_vars(section.pattern, where, usable, unusable)
            # tidy, not checked: a pattern is ours, and carries * and **.
            section.retarget(rc.tidy(expand_values(section.pattern, where, usable)))
        for key, values in (
            (rc.PARAMS_KEY, section.params),
            (rc.REMAPS_KEY, section.remaps),
        ):
            check_vars(values, f"{where}.{key}", usable, unusable)
        section.params = expand_values(
            section.params, f"{where}.{rc.PARAMS_KEY}", usable
        )
        section.remaps = expand_values(
            section.remaps, f"{where}.{rc.REMAPS_KEY}", usable
        )
        # Both sides of a remap are ROS names, and they are the one thing here
        # that reaches rcl as a name, so they are repaired and checked now.
        try:
            section.remaps = {
                rc.check_name(key, f"{where}.{rc.REMAPS_KEY}"): rc.check_name(
                    str(value), f"{where}.{rc.REMAPS_KEY}.{key}"
                )
                for key, value in section.remaps.items()
            }
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
