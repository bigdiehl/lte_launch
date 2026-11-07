"""lte-launch: run a typed launch file with a schema-generated command line.

    lte-launch <package> <file> [options] [launch arguments]
    lte-launch <path/to/standalone_launch.py> [options] [launch arguments]

``<file>`` is the full filename, e.g. ``turtle_demo_launch.py`` (``.py`` required);
it is found by searching the installed package for a ``*_launch.py`` file with
that name -- the way ``ros2 launch <package> <file>`` searches ``share/<package>/``
-- then loaded with a real ``import``, exactly like anything else in that
installed package (see ``loader.py``). Its config
schema turns into command-line flags (one per field, named by the dotted
field path). Values are layered as: model defaults < --config files (in
order, deep-merged; a file is a path or <package>:<path>) < command line
flags, validated, and only then is anything started.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any

import yaml

from . import config as cfg
from . import effective
from . import loader
from . import ros_config as rc
from . import schema
from .command import check_levels
from .core import TypedTarget

PROG = "lte-launch"
WARN = f"{PROG}: warning:"
USAGE = (
    f"{PROG} [lte-launch options] <package> <file> [arguments]\n"
    f"       {PROG} [lte-launch options] <path/to/standalone_launch.py> [arguments]"
)

# lte-launch's own options; schema fields must not collide with these. Every option
# added from here on is hyphenated, which a Python identifier cannot be, so it
# can never collide with a schema field (Q18).
OWN_WITH_VALUE = {
    "--config",
    "--launch-prefix",
    "--launch-prefix-filter",
    "--ros-param",
    "--ros-remap",
    "--tmux-session",
    "--log-level",
    "--stop-timeout",
}
OWN_FLAGS = {
    "-h",
    "--help",
    "--schema",
    "--show-config",
    "-d",
    "--debug",
    "--strict",
    "--tmux",
    "--tmux-dash",
    "--log-commands",
    "--dry-run",
}
RESERVED = OWN_WITH_VALUE | OWN_FLAGS

# What --ros-param/--ros-remap take, for their metavar and their error message.
ROS_CLI_SHAPE = {"param": "[PATTERN:]NAME=VALUE", "remap": "[PATTERN:]FROM=TO"}


class TlaunchError(Exception):
    """A user-facing error: printed without a traceback."""


# --------------------------------------------------------------------------
# 1. Find and load the launch
# --------------------------------------------------------------------------
def split_target(argv: list[str]) -> tuple[list[str], list[str]]:
    """Separate the launch target from the remaining options.

    The target is one existing file path, or ``<package> <file>``, and
    must come before the first schema flag (lte-launch's own options may appear
    anywhere before or between them).
    """
    positionals: list[int] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in OWN_WITH_VALUE:
            i += 2
        elif arg.split("=")[0] in OWN_WITH_VALUE or arg in OWN_FLAGS:
            i += 1
        elif arg.startswith("-"):
            break
        else:
            positionals.append(i)
            i += 1
            first = argv[positionals[0]]
            if len(positionals) == 2 or os.path.isfile(first):
                break
    target = [argv[i] for i in positionals]
    rest = [a for i, a in enumerate(argv) if i not in positionals]
    return target, rest


def _complete_package_or_path(prefix, parsed_args, **kwargs):
    """First positional: a package name, or a standalone launch file/dir path
    (mirrors `ros2 launch`'s own package_name completer)."""
    from ament_index_python.packages import get_packages_with_prefixes

    completions = list(get_packages_with_prefixes())
    try:
        from argcomplete.completers import FilesCompleter

        completions += FilesCompleter()(
            prefix=prefix, parsed_args=parsed_args, **kwargs
        )
    except ImportError:
        pass
    return completions


def _complete_launch_file(prefix, parsed_args, **kwargs):
    """Second positional: the full filename (".py" required) of a *_launch.py
    file installed in the package already typed as the first one."""
    return [n + ".py" for n in loader.launch_file_names(parsed_args.package)]


def _completion_parser() -> argparse.ArgumentParser:
    """A parser that exists only to drive `argcomplete`: same option strings as
    the real CLI (so completion can skip over e.g. ``--config FILE`` to reach
    the positionals), plus the target as two completable positionals.
    `split_target`'s manual scan has no static shape for argcomplete to use."""
    parser = argparse.ArgumentParser(add_help=False)
    for flag in OWN_WITH_VALUE:
        parser.add_argument(flag)
    for flag in OWN_FLAGS:
        parser.add_argument(flag, action="store_true")
    parser.add_argument("package").completer = _complete_package_or_path
    parser.add_argument("file", nargs="?").completer = _complete_launch_file
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    return parser


# --------------------------------------------------------------------------
# 2. Schema -> argparse
# --------------------------------------------------------------------------
def _fmt_default(value: Any) -> str:
    if value is schema.MISSING:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return repr(value)
    if hasattr(value, "value") and not isinstance(value, (int, float)):
        return repr(value.value)  # Enum
    return str(value)


def _esc(text: str) -> str:
    return text.replace("%", "%%")


def _join(bits: list[str], note: str) -> str:
    """Insert ``(note)`` before the trailing [default]/[required] marker."""
    *front, tail = bits
    return "  ".join([b for b in front if b] + [f"({note})", tail])


class _KeyValueAction(argparse.Action):
    """``--flag key=value`` (repeatable) -> dict; later keys override."""

    def __init__(self, *args, infer: bool = False, choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.infer = infer
        self.value_choices = choices

    def __call__(self, parser, namespace, value, option_string=None):
        key, sep, raw = value.partition("=")
        if not sep or not key:
            parser.error(f"argument {option_string}: expected KEY=VALUE, got '{value}'")
        if self.value_choices is not None and raw not in self.value_choices:
            parser.error(
                f"argument {option_string}: invalid value '{raw}' for '{key}' "
                f"(choose from {', '.join(self.value_choices)})"
            )
        if self.infer:
            try:
                parsed = yaml.safe_load(raw)
            except yaml.YAMLError as exc:
                parser.error(
                    f"argument {option_string}: cannot parse '{raw}' as YAML: {exc}"
                )
        else:
            parsed = raw
        current = dict(getattr(namespace, self.dest, None) or {})
        current[key] = parsed
        setattr(namespace, self.dest, current)


def add_schema_args(parser: argparse.ArgumentParser, model: type):
    """Add one group per nested model and one flag per supported field.

    Returns the list of fields that got no flag (collections etc.)."""
    groups: dict[tuple, Any] = {}
    skipped: list[schema.FieldSpec] = []

    for spec in schema.walk(model):
        flag = "--" + spec.dotted
        if flag in RESERVED:
            raise TlaunchError(
                f"field '{spec.dotted}' of {spec.owner.__name__} collides with "
                f"lte-launch's own option {flag}; rename the field"
            )
        if not spec.supported:
            skipped.append(spec)
            continue

        owner_path = spec.path[:-1]
        if owner_path not in groups:
            title = ".".join(owner_path) or "arguments"
            groups[owner_path] = parser.add_argument_group(
                f"{title}  [{spec.owner.__name__}]",
                # The top level's summary is already the help's description.
                (_esc(schema.summary(spec.owner)) or None) if owner_path else None,
            )
        group = groups[owner_path]

        bits = [spec.description]
        if spec.constraints:
            bits.append(f"({spec.constraints})")
        bits.append(
            "[required]"
            if spec.required
            else f"[default: {_fmt_default(spec.default)}]"
        )
        help_ = _esc("  ".join(b for b in bits if b))

        kwargs = dict(dest=spec.dotted, default=argparse.SUPPRESS)
        if spec.is_bool:
            group.add_argument(
                flag, action=argparse.BooleanOptionalAction, help=help_, **kwargs
            )
        elif spec.kind == "list":
            group.add_argument(
                flag,
                action="append",
                metavar=spec.type_name,
                choices=spec.choices,
                help=_esc(_join(bits, "repeatable")),
                **kwargs,
            )
        elif spec.kind == "dict":
            note = "repeatable"
            if spec.infer:
                note += "; values are parsed as YAML, quote to force a string"
            group.add_argument(
                flag,
                action=_KeyValueAction,
                metavar="KEY=" + spec.type_name,
                infer=spec.infer,
                choices=spec.choices,
                help=_esc(_join(bits, note)),
                **kwargs,
            )
        else:
            group.add_argument(
                flag, metavar=spec.type_name, choices=spec.choices, help=help_, **kwargs
            )
    return skipped


def add_own_args(parser: argparse.ArgumentParser):
    """lte-launch's own options, in three groups, after the launch's arguments."""
    g = parser.add_argument_group("lte-launch: config")
    g.add_argument(
        "--config",
        action="append",
        default=[],
        metavar="FILE",
        dest="$config",
        help="YAML config file, or <package>:<path>. Repeatable; later files win "
        "(deep merge, lists replaced), flags win over all files",
    )
    g.add_argument(
        "--show-config",
        action="store_true",
        dest="$show_config",
        help="print the effective config, each value with its source, and exit",
    )
    g.add_argument(
        "--schema",
        action="store_true",
        dest="$schema",
        help="print the JSON Schema of this launch's config and exit",
    )
    g.add_argument(
        "--ros-param",
        action="append",
        default=[],
        dest="$ros_param",
        metavar=ROS_CLI_SHAPE["param"],
        help="set a ROS parameter on the nodes matching PATTERN (/**, /**/planner, "
        "/ns/*; default /**). Value parsed as YAML. Repeatable",
    )
    g.add_argument(
        "--ros-remap",
        action="append",
        default=[],
        dest="$ros_remap",
        metavar=ROS_CLI_SHAPE["remap"],
        help="remap a name on the nodes matching PATTERN (default /**). Repeatable",
    )
    g.add_argument(
        "--strict",
        action="store_true",
        dest="$strict",
        help="refuse untyped ROS parameters and remappings; config warnings "
        "become errors",
    )

    g = parser.add_argument_group("lte-launch: running")
    g.add_argument(
        "--log-level",
        action="append",
        default=[],
        dest="$log_level",
        metavar="LEVEL",
        help="log level of every ROS node: debug, info, warn, error, fatal; or "
        "LOGGER:=LEVEL for one logger. Repeatable",
    )
    g.add_argument(
        "--log-commands",
        action="store_true",
        dest="$log_commands",
        help="log each process's final command line as it starts",
    )
    g.add_argument(
        "-d",
        "--debug",
        action="store_true",
        dest="$debug",
        help="Python tracebacks and advisory config warnings",
    )
    g.add_argument(
        "--launch-prefix",
        metavar="CMD",
        dest="$launch_prefix",
        help="prefix command for every executable (gdb -ex run --args, ...)",
    )
    g.add_argument(
        "--launch-prefix-filter",
        metavar="REGEX",
        dest="$launch_prefix_filter",
        help="only apply --launch-prefix to executables matching REGEX",
    )
    g.add_argument(
        "--stop-timeout",
        type=float,
        default=10.0,
        metavar="S",
        dest="$stop_timeout",
        help="seconds each process gets after SIGINT before SIGTERM, when "
        "stopping (default 10; a node's stop_timeout= overrides it)",
    )
    g.add_argument(
        "--dry-run",
        action="store_true",
        dest="$dry_run",
        help="start nothing; print each process's command line",
    )

    g = parser.add_argument_group("lte-launch: tmux")
    g.add_argument(
        "--tmux",
        action="store_true",
        dest="$tmux",
        help="run each process in its own tmux pane (joins the current session, "
        "or opens one)",
    )
    g.add_argument(
        "--tmux-dash",
        action="store_true",
        dest="$tmux_dash",
        help="add a dashboard window: every pane with its status (lte-dash)",
    )
    g.add_argument(
        "--tmux-session",
        dest="$tmux_session",
        metavar="NAME",
        help="open the panes in a new session of this name",
    )


def build_parser(label: str, typed: TypedTarget, *, direct: bool = False):
    """``direct`` is for a file run as its own program (``python3 my_script.py``),
    where there is no target to name on the command line."""
    summary = schema.summary(typed.config_type)
    program = os.path.basename(sys.argv[0]) if direct else PROG
    parser = argparse.ArgumentParser(
        prog=program,
        allow_abbrev=False,
        usage=f"{program} [options]" if direct else USAGE,
        description=_esc(f"{label}\n{summary}".strip()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # The launch's own arguments first: they are what -h is usually for.
    skipped = add_schema_args(parser, typed.config_type)
    add_own_args(parser)
    if skipped:
        names = ", ".join(f"{s.dotted} ({s.type_name.lower()})" for s in skipped)
        parser.epilog = _esc(
            f"fields without a command line flag (set them with --config): {names}"
        )
    return parser


# --------------------------------------------------------------------------
# 3. Config layering and validation
# --------------------------------------------------------------------------
def _issue_lines(issues: list[schema.Issue], as_flags: bool) -> list[str]:
    lines = []
    for issue in issues:
        where = ".".join(issue.path)
        label = f"--{where}" if as_flags else where
        if issue.missing:
            lines.append(
                f"  {label}: required (give --{where} or set it in a --config file)"
            )
        elif issue.input is None:
            lines.append(f"  {label}: {issue.message}")
        else:
            lines.append(f"  {label}: {issue.message} (got {issue.input!r})")
    return lines


def parse_ros_cli(values: list[str], kind: str) -> list[rc.Section]:
    """``--ros-param '[pattern:]name=value'`` -> one section per value.

    The pattern is optional and defaults to ``/**`` (every node). ROS names
    contain no ``:``, so the first colon, when there is one, separates them.
    """
    sections = []
    for raw in values:
        head, sep, rest = raw.partition(":")
        pattern, body = (head, rest) if sep else ("/**", raw)
        key, eq, value = body.partition("=")
        if not eq or not key:
            raise TlaunchError(f"--ros-{kind} '{raw}': expected {ROS_CLI_SHAPE[kind]}")
        if kind == "param":
            try:
                parsed = yaml.safe_load(value)
            except yaml.YAMLError as exc:
                raise TlaunchError(
                    f"--ros-param '{raw}': cannot parse '{value}' as YAML: {exc}"
                ) from None
            sections.append(rc.Section(pattern, {key: parsed}, {}, source="cli"))
        else:
            sections.append(rc.Section(pattern, {}, {key: value}, source="cli"))
    return sections


def layer_config(model: type, files: list[str], cli: dict[str, Any], own: dict):
    """-> (merged launch_args, {leaf path: source label}, RosConfig)"""
    specs = schema.walk(model)
    sources: dict[tuple[str, ...], str] = {}
    merged: dict = {}
    sections: list[rc.Section] = []

    # Two passes on purpose. The first reads and checks every file against the
    # schema; the second expands $(...) and merges. So a key that is not part of
    # this launch is always reported before a substitution that cannot be
    # resolved -- the schema mismatch is the more fundamental error, and it can
    # be in a different file than the bad substitution.
    known = {".".join(spec.path) for spec in specs}
    # The CLI's own sections are parsed up front so their $(var ...) names are
    # checked with everyone else's; they are still applied last.
    cli_sections = parse_ros_cli(own["ros_param"], "param") + parse_ros_cli(
        own["ros_remap"], "remap"
    )
    problems: list[str] = []
    loaded: list[tuple[str, dict, list[rc.Section]]] = []
    for ref in files:
        data = cfg.load_config_file(cfg.resolve_config_ref(ref))
        launch_args, file_sections = cfg.split_file(data, ref)
        loaded.append((ref, launch_args, file_sections))
        issues = schema.unknown_keys(model, launch_args)
        strays = cfg.unknown_vars(file_sections, known)
        if issues or strays:
            problems.append(f"in config file '{ref}':")
            problems.extend(_issue_lines(issues, as_flags=False))
            problems.extend(f"  {line}" for line in strays)
            if issues and strays:
                # Both at once means the file is describing a different launch,
                # which is more useful to say than either line on its own.
                problems.append(
                    "  (this config looks like it was written for another launch)"
                )
    strays = cfg.unknown_vars(cli_sections, known)
    if strays:
        problems.append("on the command line:")
        problems.extend(f"  {line}" for line in strays)
    if problems:
        raise TlaunchError("\n".join(problems))

    for ref, launch_args, file_sections in loaded:
        cfg.reject_vars(launch_args, ref)
        launch_args = cfg.expand_values(launch_args, ref)
        sections.extend(file_sections)
        for spec in specs:
            if cfg.lookup(launch_args, spec.path)[0]:
                sources[spec.path] = ref
        merged = cfg.deep_merge(merged, launch_args)

    from_cli = cfg.nest(cli)
    cfg.reject_vars(from_cli, "cli")
    merged = cfg.deep_merge(merged, cfg.expand_values(from_cli, "cli"))
    for dotted in cli:
        sources[tuple(dotted.split("."))] = "cli"

    # The CLI is the last word for the untyped lanes too.
    sections.extend(cli_sections)

    ros = rc.RosConfig(sections, debug=own["debug"])
    if own["strict"] and ros:
        raise TlaunchError(
            "--strict, but config carries untyped node sections:\n"
            + "\n".join(f"  {s.pattern}  ({s.source})" for s in ros.sections)
            + "\ndeclare these as a RosParams model in the launch's schema, "
            "or drop --strict"
        )
    return merged, sources, ros


def save_effective(text: str, label: str) -> str:
    """Write the effective config where it can be read after the fact.

    Every run leaves one behind, so "what config did that actually launch with?"
    is always answerable -- and the file is a valid ``--config`` input, so the
    same run can be reproduced from it.
    """
    import tempfile

    name = re.sub(r"\W+", "_", label).strip("_") or "launch"
    directory = Path(tempfile.gettempdir()) / "lte-launch"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}-{os.getpid()}.yaml"
    path.write_text(text)
    return str(path)


# --------------------------------------------------------------------------
# 4. Run
# --------------------------------------------------------------------------
def warn_unmatched(ros: rc.RosConfig, own: dict) -> None:
    """A node pattern that reached no node is usually a typo -- but it can also be
    config for a node behind a disabled toggle, or aimed at a stock `Node` that
    does not collect config at all. So it is an error only under ``--strict``,
    and otherwise printed only under ``-d``: quiet by default, because it changes
    nothing about the launch. ``--show-config`` reports the same thing in full,
    per section, which is the place to go looking on purpose."""
    unmatched = ros.unmatched()
    if not unmatched:
        return
    message = (
        "node section(s) matched no node in this launch:\n"
        + "\n".join(f"  {s.pattern}  ({s.source})" for s in unmatched)
        + "\n  (a node is only matchable if it is created with node(...) and given "
        "a name)"
    )
    if own["strict"]:
        raise TlaunchError(message)
    if own["debug"]:
        print(f"{WARN} {message}", file=sys.stderr)


#: Any of these turns panes on.
TMUX_FLAGS = ("tmux", "tmux_session", "tmux_dash")


def run_label(target: list[str]) -> str:
    """A short name for the run: its log dir and tmux session are named after it."""
    stem = os.path.basename(target[-1] if target else "launch")
    if not stem.endswith(".py"):  # a dotted module path
        stem = stem.rsplit(".", 1)[-1]
    for suffix in (".py", "_launch"):
        stem = stem[: -len(suffix)] if stem.endswith(suffix) else stem
    # tmux reads '.' and ':' in a name as part of a target address.
    return re.sub(r"[^A-Za-z0-9_-]", "-", stem) or "launch"


def run(
    typed,
    config: Any,
    own: dict[str, Any],
    ros: rc.RosConfig,
    sources: dict,
    label: str,
    target: list[str],
) -> int:
    """Run the launch: its body starts processes as it runs, as subprocesses
    or tmux panes, in a libtmux_easy ``Processes`` group."""
    import tempfile

    from . import engine

    stem = run_label(target)
    log_dir = os.path.join(tempfile.gettempdir(), "lte-launch", f"{stem}-{os.getpid()}")
    procs = session = None
    join = False
    if not own["dry_run"]:
        try:
            from libtmux_easy.processes import Processes
        except ImportError as exc:
            raise TlaunchError(
                f"lte-launch needs libtmux_easy ({exc}): install its .deb, or "
                "PYTHONPATH=~/projects/libtmux_easy/src"
            ) from None
        # The processes' output is echoed to our stdout as it comes: line by
        # line, even into a pipe (`| tee`, a stock `ros2 launch` include).
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(line_buffering=True)
        prefix = ""
        if any(own[flag] for flag in TMUX_FLAGS):
            join = bool(os.environ.get("TMUX")) and not own["tmux_session"]
            name = own["tmux_session"] or f"lte-launch-{stem}-{os.getpid()}"
            session = engine.open_tmux(name, own["tmux_dash"], join=join)
            prefix = stem if join else ""
        procs = Processes(
            stem, tmux=session, prefix=prefix, log_dir=log_dir, echo=["*"],
            sigint_timeout=own["stop_timeout"],
        )  # fmt: skip

    run = engine.Run(
        procs,
        label=stem,
        ros=ros,
        params_dir=os.path.join(log_dir, "params"),
        log_levels=own["log_level"],
        log_commands=own["log_commands"],
        launch_prefix=own["launch_prefix"],
        launch_prefix_filter=own["launch_prefix_filter"],
    )
    if not own["dry_run"]:
        saved = save_effective(
            effective.render(typed.config_type, config, sources, ros, label), label
        )
        print(f"[INFO] [{PROG}]: effective config written to {saved}")
    hint = None
    if session is not None and not join:
        hint = f"tmux session: tmux attach -t {session.session_name}"
    code = engine.execute(typed, config, run, attach_hint=hint)
    if own["dry_run"] and code == 0:
        print(f"{PROG}: dry run, {len(run.plan)} process(es):")
        for item in run.plan:
            print(f"  {item.name}\n      {item.command}")
    if procs is not None:
        print(f"[INFO] [{PROG}]: process logs: {log_dir}")
    if session is not None:
        note = engine.close_tmux(session, keep=code != 0 or own["tmux_dash"], joined=join)
        if note:
            print(f"[INFO] [{PROG}]: {note}")
    warn_unmatched(ros, own)
    return code


def print_general_help() -> None:
    parser = argparse.ArgumentParser(
        prog=PROG,
        usage=USAGE,
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run a typed launch file. Give the launch, then -h for the "
        "options generated\nfrom its config schema.",
        epilog=f"""examples:
  {PROG} my_pkg my_launch.py -h
  {PROG} my_pkg my_launch.py --config my_pkg:config/site.yaml --robot.name r2
  {PROG} my_pkg my_launch.py --show-config
  {PROG} my_pkg my_launch.py --dry-run
  {PROG} my_pkg my_launch.py --tmux-dash""",
    )
    add_own_args(parser)
    parser.print_help()


def configure(
    typed: TypedTarget,
    files: list[str],
    cli: dict[str, Any],
    own: dict,
    *,
    as_flags: bool = True,
):
    """Layer, validate and expand: -> (config, sources, RosConfig).

    Every problem is a ``TlaunchError`` with the issues listed by flag. Shared by
    the command and by a stock include (``stock.py``)."""
    try:
        merged, sources, ros = layer_config(typed.config_type, files, cli, own)
        config = schema.validate(typed.config_type, merged)
        # Only now are the launch args final, which is what $(var NAME) in a
        # node section reads (see config.expand_sections).
        cfg.expand_sections(ros.sections, schema.to_plain(config))
    except cfg.ConfigFileError as exc:
        raise TlaunchError(str(exc)) from None
    except schema.SchemaError as exc:
        raise TlaunchError(
            "configuration error:\n"
            + "\n".join(_issue_lines(exc.issues, as_flags=as_flags))
        ) from None
    return config, sources, ros


def _main(argv: list[str], typed: TypedTarget | None = None) -> int:
    debug = "-d" in argv or "--debug" in argv

    if typed is not None:
        # Run directly as a program: the target is the file we are in, so every
        # argument is an option (see TypedTarget.main).
        target, rest = [typed.name], argv
        direct = True
    else:
        direct = False
        target, rest = split_target(argv)
        if not target:
            if "-h" in rest or "--help" in rest or not rest:
                print_general_help()
                return 0
            raise TlaunchError("no launch file given")
        try:
            typed = loader.resolve(*target)
        except loader.LaunchLookupError as exc:
            if debug:
                traceback.print_exception(exc)
            raise TlaunchError(str(exc)) from None
    parser = build_parser(" ".join(target), typed, direct=direct)

    ns = vars(parser.parse_args(rest))
    own = {k[1:]: ns.pop(k) for k in list(ns) if k.startswith("$")}
    files = own["config"]

    try:
        own["log_level"] = check_levels(own["log_level"])
    except ValueError as exc:
        raise TlaunchError(str(exc)) from None

    if own["schema"]:
        print(json.dumps(schema.json_schema(typed.config_type), indent=2))
        return 0

    config, sources, ros = configure(typed, files, ns, own)

    label = " ".join(target)
    if own["show_config"]:
        if ros:
            # A dry run is what resolves the node patterns: report which nodes
            # each section actually reached.
            typed.plan(config, ros)
            warn_unmatched(ros, own)
        print(
            effective.render(typed.config_type, config, sources, ros, label),
            end="",
        )
        return 0
    return run(typed, config, own, ros, sources, label, target)


def main(argv: list[str] | None = None, *, target: TypedTarget | None = None) -> int:
    """``lte-launch``'s entry point. ``target`` short-circuits target resolution, for
    a file run directly with ``python3`` (see ``TypedTarget.main``)."""
    try:
        import argcomplete
    except ImportError:
        pass
    else:
        # A no-op unless actually invoked by a completion hook (checks for the
        # _ARGCOMPLETE env var itself, then os._exit()s with the completions).
        argcomplete.autocomplete(_completion_parser())

    argv = sys.argv[1:] if argv is None else argv
    try:
        return _main(argv, target)
    except TlaunchError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
