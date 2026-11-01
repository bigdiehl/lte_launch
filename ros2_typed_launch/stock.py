"""A typed launch as a stock ``LaunchDescription``, so stock launch can include it (Q17).

    ros2 launch my_pkg my_launch.py turtlesim.namespace:=alpha tlaunch_config:=a.yaml

Each schema field with a flag becomes a ``DeclareLaunchArgument`` named by its
dotted path (required fields get no default, so stock reports them). One more,
``tlaunch_config``, takes config files, ';'-separated. At launch time the given
arguments run through the same pipeline as ``tlaunch``: layering (defaults <
files < arguments), validation, ``$(var ...)``, node sections. What tlaunch adds
around a run is not there: monitoring, panes, ``--show-config``.
"""

from __future__ import annotations

import argparse
import difflib

import yaml

from . import cli, schema
from .core import TypedLaunch

CONFIG_ARG = "tlaunch_config"


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
    """tlaunch's own options at their defaults: none of them apply here."""
    parser = argparse.ArgumentParser()
    cli.add_own_args(parser)
    return {k[1:]: v for k, v in vars(parser.parse_args([])).items()}


def description(typed: TypedLaunch):
    from launch import LaunchDescription
    from launch.actions import DeclareLaunchArgument, OpaqueFunction

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
            n for n in context.launch_configurations
            if "." in n and n.split(".")[0] in groups and n not in names
        ]
        if typos:
            lines = []
            for n in sorted(typos):
                if n in flagless:
                    lines.append(f"  {n}: has no launch argument; set it in {CONFIG_ARG}")
                    continue
                close = difflib.get_close_matches(n, names, n=1)
                lines.append(f"  {n}: not an argument of this launch"
                             + (f" (did you mean '{close[0]}'?)" if close else ""))
            raise cli.TlaunchError(f"typed launch '{typed.name}':\n" + "\n".join(lines))

    def build(context):
        values = context.launch_configurations
        files = [ref.strip() for ref in values.get(CONFIG_ARG, "").split(";") if ref.strip()]
        own = _own_defaults()
        try:
            fields = {s.dotted: _value(s, values[s.dotted]) for s in specs
                      if s.dotted in given}
            config, _, ros = cli.configure(typed, files, fields, own, as_flags=False)
            return cli.build_actions(typed, config, ros, own)
        except cli.TlaunchError as exc:
            raise cli.TlaunchError(f"typed launch '{typed.name}': {exc}") from None

    declared = [
        DeclareLaunchArgument(
            s.dotted,
            **({} if s.required else {"default_value": _text(s.default)}),
            description=" ".join(filter(None, [
                s.description or s.type_name.lower(),
                s.constraints and f"({s.constraints})",
            ])),
        )
        for s in specs
    ]
    declared.append(DeclareLaunchArgument(
        CONFIG_ARG, default_value="",
        description="tlaunch config files, ';'-separated (launch_args and node sections)",
    ))
    return LaunchDescription(
        [OpaqueFunction(function=snapshot), *declared, OpaqueFunction(function=build)]
    )
