"""A typed launch as a stock ``LaunchDescription``, so stock launch can include it (Q17).

    ros2 launch my_pkg my_launch.py turtlesim.namespace:=alpha lte_launch_config:=a.yaml

Each schema field with a flag becomes a ``DeclareLaunchArgument`` named by its
dotted path (required fields get no default, so stock reports them). One more,
``lte_launch_config``, takes config files, ';'-separated. The arguments are
validated here, at include time, then the launch runs as **one process**:
``lte-launch`` with the given values in a generated config file. Stock sees that
process start, stop on Ctrl-C, and exit non-zero if anything in it died; its
nodes are lte-launch's, not stock's.
"""

from __future__ import annotations

import argparse
import difflib
import inspect
import os
import tempfile

import yaml

from . import cli, schema
from .core import TypedLaunch

CONFIG_ARG = "lte_launch_config"


def _text(value) -> str:
    """A default as stock shows it (``ros2 launch -s``) and as a string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _value(spec: schema.FieldSpec, text: str):
    """A launch argument (always text) as the schema expects it. Scalars stay text
    for the schema to convert, as with a flag; collections are YAML."""
    if spec.kind not in ("list", "dict"):
        return text
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise cli.TlaunchError(f"{spec.dotted}: cannot parse {text!r} as YAML: {exc}")


def _own_defaults() -> dict:
    """lte-launch's own options at their defaults: none of them apply here."""
    parser = argparse.ArgumentParser()
    cli.add_own_args(parser)
    return {k[1:]: v for k, v in vars(parser.parse_args([])).items()}


def target_args(typed: TypedLaunch) -> list[str]:
    """How ``lte-launch`` addresses this launch: ``<package> <dotted.module>``, or
    the file's path when it is not in a package."""
    module = typed.factory.__module__
    package, _, rest = module.partition(".")
    if rest and not module.startswith("_lte_launch_"):
        return [package, rest]
    return [inspect.getfile(inspect.unwrap(typed.factory))]


def _nest(dotted: dict) -> dict:
    out: dict = {}
    for key, value in dotted.items():
        *parents, leaf = key.split(".")
        node = out
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = value
    return out


def description(typed: TypedLaunch):
    from launch import LaunchDescription
    from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction

    every = schema.walk(typed.config_type)
    specs = [s for s in every if s.supported]
    names = [s.dotted for s in specs]
    groups = {s.path[0] for s in every if len(s.path) > 1}
    flagless = {s.dotted for s in every if not s.supported}
    given: set[str] = set()

    def snapshot(context):
        # Runs before the declarations: whatever is already set came from the
        # includer or the command line. A declared default is not "given", so a
        # config file can still set that field.
        given.clear()
        given.update(n for n in names if n in context.launch_configurations)
        # Stock ignores an argument nobody declared. One under a group of ours
        # (turtlesim.nmespace) is a typo of one of ours, so say so.
        typos = [
            n
            for n in context.launch_configurations
            if "." in n and n.split(".")[0] in groups and n not in names
        ]
        if typos:
            lines = []
            for n in sorted(typos):
                if n in flagless:
                    lines.append(f"  {n}: has no launch argument; set it in {CONFIG_ARG}")
                    continue
                close = difflib.get_close_matches(n, names, n=1)
                lines.append(
                    f"  {n}: not an argument of this launch"
                    + (f" (did you mean '{close[0]}'?)" if close else "")
                )
            raise cli.TlaunchError(f"typed launch '{typed.name}':\n" + "\n".join(lines))

    def run(context):
        values = context.launch_configurations
        files = [r.strip() for r in values.get(CONFIG_ARG, "").split(";") if r.strip()]
        fields = {s.dotted: _value(s, values[s.dotted]) for s in specs if s.dotted in given}
        try:  # fail here, at include time, with the issues listed
            cli.configure(typed, files, fields, _own_defaults(), as_flags=False)
        except cli.TlaunchError as exc:
            raise cli.TlaunchError(f"typed launch '{typed.name}': {exc}") from None
        cmd = ["lte-launch", *target_args(typed)]
        for ref in files:
            cmd += ["--config", ref]
        if fields:
            fd, path = tempfile.mkstemp(prefix=f"lte-launch-{typed.name}-", suffix=".yaml")
            with os.fdopen(fd, "w") as f:
                yaml.safe_dump({"launch_args": _nest(fields)}, f)
            cmd += ["--config", path]
        return [ExecuteProcess(cmd=cmd, name=typed.name, output="screen")]

    declared = [
        DeclareLaunchArgument(
            s.dotted,
            **({} if s.required else {"default_value": _text(s.default)}),
            description=" ".join(
                filter(
                    None,
                    [
                        s.description or s.type_name.lower(),
                        s.constraints and f"({s.constraints})",
                    ],
                )
            ),
        )
        for s in specs
    ]
    declared.append(
        DeclareLaunchArgument(
            CONFIG_ARG,
            default_value="",
            description="lte-launch config files, ';'-separated (launch_args and node sections)",
        )
    )
    return LaunchDescription(
        [OpaqueFunction(function=snapshot), *declared, OpaqueFunction(function=run)]
    )
