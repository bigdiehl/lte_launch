"""tlaunch: run a typed launch file with a schema-generated command line.

    tlaunch <package> <file> [options] [launch arguments]
    tlaunch <path/to/standalone_launch.py> [options] [launch arguments]

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
import sys
import traceback
from typing import Any

import yaml

from . import config as cfg
from . import loader
from . import schema
from .core import TypedLaunch

PROG = "tlaunch"
USAGE = (
    f"{PROG} [launch options] <package> <file> [launch arguments]\n"
    f"       {PROG} [launch options] <path/to/standalone_launch.py> [launch arguments]"
)

# tlaunch's own options; schema fields must not collide with these.
OWN_WITH_VALUE = {"--config", "--launch-prefix", "--launch-prefix-filter"}
OWN_FLAGS = {
    "-h", "--help", "--schema", "--show-config",
    "-n", "--noninteractive", "-d", "--debug",
}
RESERVED = OWN_WITH_VALUE | OWN_FLAGS


class TlaunchError(Exception):
    """A user-facing error: printed without a traceback."""


# --------------------------------------------------------------------------
# 1. Find and load the launch
# --------------------------------------------------------------------------
def split_target(argv: list[str]) -> tuple[list[str], list[str]]:
    """Separate the launch target from the remaining options.

    The target is one existing file path, or ``<package> <file>``, and
    must come before the first schema flag (tlaunch's own options may appear
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

        completions += FilesCompleter()(prefix=prefix, parsed_args=parsed_args, **kwargs)
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
                parser.error(f"argument {option_string}: cannot parse '{raw}' as YAML: {exc}")
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
                f"tlaunch's own option {flag}; rename the field"
            )
        if not spec.supported:
            skipped.append(spec)
            continue

        owner_path = spec.path[:-1]
        if owner_path not in groups:
            title = ".".join(owner_path) or "(top level)"
            groups[owner_path] = parser.add_argument_group(
                f"{title}  [{spec.owner.__name__}]",
                _esc(schema.summary(spec.owner)) or None,
            )
        group = groups[owner_path]

        bits = [spec.description]
        if spec.constraints:
            bits.append(f"({spec.constraints})")
        bits.append(
            "[required]" if spec.required else f"[default: {_fmt_default(spec.default)}]"
        )
        help_ = _esc("  ".join(b for b in bits if b))

        kwargs = dict(dest=spec.dotted, default=argparse.SUPPRESS)
        if spec.is_bool:
            group.add_argument(
                flag, action=argparse.BooleanOptionalAction, help=help_, **kwargs
            )
        elif spec.kind == "list":
            group.add_argument(
                flag, action="append", metavar=spec.type_name,
                choices=spec.choices, help=_esc(_join(bits, "repeatable")), **kwargs
            )
        elif spec.kind == "dict":
            note = "repeatable"
            if spec.infer:
                note += "; values are parsed as YAML, quote to force a string"
            group.add_argument(
                flag, action=_KeyValueAction, metavar="KEY=" + spec.type_name,
                infer=spec.infer, choices=spec.choices,
                help=_esc(_join(bits, note)), **kwargs
            )
        else:
            group.add_argument(
                flag, metavar=spec.type_name, choices=spec.choices,
                help=help_, **kwargs
            )
    return skipped


def add_own_args(parser: argparse.ArgumentParser):
    g = parser.add_argument_group("launch options (tlaunch itself)")
    g.add_argument(
        "--config", action="append", default=[], metavar="FILE", dest="$config",
        help="YAML config file, or <package>:<path> for a file in a package's "
        "share directory; repeatable, later files override earlier ones "
        "(deep merge, lists are replaced); command line flags override all files",
    )
    g.add_argument(
        "--schema", action="store_true", dest="$schema",
        help="print the JSON Schema of this launch's config and exit",
    )
    g.add_argument(
        "--show-config", action="store_true", dest="$show_config",
        help="print the effective config with the source of each value, "
        "and exit without launching",
    )
    g.add_argument(
        "-n", "--noninteractive", action="store_true", dest="$noninteractive",
        default=not sys.stdin.isatty(),
        help="run without a terminal (default when stdin is not a tty)",
    )
    g.add_argument(
        "-d", "--debug", action="store_true", dest="$debug",
        help="verbose launch output and Python tracebacks",
    )
    g.add_argument(
        "--launch-prefix", metavar="CMD", dest="$launch_prefix",
        help="prefix command for all executables",
    )
    g.add_argument(
        "--launch-prefix-filter", metavar="REGEX", dest="$launch_prefix_filter",
        help="only apply --launch-prefix to executables matching this regex",
    )


def build_parser(label: str, typed: TypedLaunch):
    summary = schema.summary(typed.config_type)
    parser = argparse.ArgumentParser(
        prog=PROG,
        allow_abbrev=False,
        usage=USAGE,
        description=_esc(f"{label}\n{summary}".strip()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_own_args(parser)
    skipped = add_schema_args(parser, typed.config_type)
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


def layer_config(model: type, files: list[str], cli: dict[str, Any]):
    """-> (merged dict, {leaf path: source label})"""
    specs = schema.walk(model)
    sources: dict[tuple[str, ...], str] = {}
    merged: dict = {}

    problems: list[str] = []
    for ref in files:
        data = cfg.load_config_file(cfg.resolve_config_ref(ref))
        issues = schema.unknown_keys(model, data)
        if issues:
            problems.append(f"in config file '{ref}':")
            problems.extend(_issue_lines(issues, as_flags=False))
        for spec in specs:
            if cfg.lookup(data, spec.path)[0]:
                sources[spec.path] = ref
        merged = cfg.deep_merge(merged, data)
    if problems:
        raise TlaunchError("\n".join(problems))

    merged = cfg.deep_merge(merged, cfg.nest(cli))
    for dotted in cli:
        sources[tuple(dotted.split("."))] = "cli"
    return merged, sources


def print_effective(model: type, config: Any, sources: dict) -> None:
    plain = schema.to_plain(config)
    specs = schema.walk(model)
    width = max((len(s.dotted) for s in specs), default=0)
    for spec in specs:
        _, value = cfg.lookup(plain, spec.path)
        src = sources.get(spec.path, "default")
        print(f"{spec.dotted:<{width}} = {json.dumps(value)}  ({src})")


# --------------------------------------------------------------------------
# 4. Run
# --------------------------------------------------------------------------
def run_launch(typed: TypedLaunch, config: Any, own: dict[str, Any]) -> int:
    import launch
    from launch.actions import SetLaunchConfiguration

    actions = []
    if own["launch_prefix"]:
        actions.append(SetLaunchConfiguration("launch-prefix", own["launch_prefix"]))
    if own["launch_prefix_filter"]:
        actions.append(
            SetLaunchConfiguration("launch-prefix-filter", own["launch_prefix_filter"])
        )
    actions.extend(typed.build(config))

    service = launch.LaunchService(
        noninteractive=own["noninteractive"], debug=own["debug"]
    )
    service.include_launch_description(launch.LaunchDescription(actions))
    return service.run()


def print_general_help() -> None:
    print(
        f"usage: {USAGE}\n\n"
        "Run a typed launch file. Give a launch first, then add -h to see\n"
        "the options generated from its config schema."
    )


def _main(argv: list[str]) -> int:
    debug = "-d" in argv or "--debug" in argv
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
    parser = build_parser(" ".join(target), typed)

    ns = vars(parser.parse_args(rest))
    own = {k[1:]: ns.pop(k) for k in list(ns) if k.startswith("$")}
    files = own["config"]

    if own["schema"]:
        print(json.dumps(schema.json_schema(typed.config_type), indent=2))
        return 0

    try:
        merged, sources = layer_config(typed.config_type, files, ns)
        config = schema.validate(typed.config_type, merged)
    except cfg.ConfigFileError as exc:
        raise TlaunchError(str(exc)) from None
    except schema.SchemaError as exc:
        raise TlaunchError(
            "configuration error:\n"
            + "\n".join(_issue_lines(exc.issues, as_flags=True))
        ) from None

    if own["show_config"]:
        print_effective(typed.config_type, config, sources)
        return 0

    return run_launch(typed, config, own)


def main(argv: list[str] | None = None) -> int:
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
        return _main(argv)
    except TlaunchError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
