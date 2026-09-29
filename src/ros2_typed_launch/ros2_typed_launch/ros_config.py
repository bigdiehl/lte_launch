"""The ``ros__parameters`` / ``ros__remappings`` lanes of a config file.

A config file has a typed part (under ``launch_args:``, validated against the
launch's schema) and an untyped part: sections keyed by **node pattern**, in
ROS's own spelling, carrying ROS parameters and remappings straight to the
matching nodes::

    launch_args:
      turtlesim: {background_r: 99}
    /**:
      ros__parameters: {use_sim_time: true}
    /my_ns/planner:
      ros__parameters: {tolerance: 0.3}
      ros__remappings: {/scan: /lidar/scan}

Wildcards follow the ROS parameter-file rules -- ``*`` is one token, ``**`` is
zero or more -- so ``/**`` is every node, ``/**/planner`` is that node in any
namespace, ``/ns/*`` is any node directly in ``/ns``. rcl already matches these
for parameters, but **refuses them for remappings** ("Wildcard '**' is not
implemented"), so we do the matching ourselves for both lanes and hand each node
concrete values. See working/design-notes.md, Q8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PARAMS_KEY = "ros__parameters"
REMAPS_KEY = "ros__remappings"
LAUNCH_ARGS_KEY = "launch_args"
SECTION_KEYS = (PARAMS_KEY, REMAPS_KEY)


class RosConfigError(ValueError):
    """A malformed node section: printed without a traceback."""


def is_node_pattern(key: str, value: Any) -> bool:
    """Is this top-level config key a node pattern rather than a stray key?

    A pattern either looks like one (holds a ``/`` or ``*``) or is proved to be
    one by its contents. Anything else stays an unknown-key error, so a typo at
    the top level is still caught (principle 4).
    """
    if "/" in key or "*" in key:
        return True
    return isinstance(value, dict) and any(k in value for k in SECTION_KEYS)


def pattern_regex(pattern: str) -> re.Pattern:
    """A node pattern -> a regex matching fully-qualified node names.

    ``*`` matches one token, ``**`` zero or more, as in a ROS parameter file.
    """
    parts = [p for p in pattern.strip("/").split("/") if p]
    out = ""
    for part in parts:
        if part == "**":
            out += r"(?:/[^/]+)*"
        elif part == "*":
            out += r"/[^/]+"
        else:
            out += "/" + re.escape(part)
    return re.compile(f"^{out}$")


@dataclass
class Section:
    """One node-pattern section of one config file."""

    pattern: str
    params: dict[str, Any]
    remaps: dict[str, str]
    source: str  # config file path, or "cli"
    matched: list[str] = field(default_factory=list)
    shadowed: set[str] = field(default_factory=set)  # params a typed field won

    def __post_init__(self):
        self._regex = pattern_regex(self.pattern)

    def matches(self, fqn: str) -> bool:
        return self._regex.match(fqn) is not None

    @property
    def universal(self) -> bool:
        """Does this pattern match every node, whatever its name? Only these can
        be applied to a node whose name we don't know (see RosConfig.for_node)."""
        return self.pattern.strip("/") == "**"


def parse_sections(data: dict, source: str) -> list[Section]:
    """The node-pattern sections of one already-loaded config file."""
    sections = []
    for key, value in data.items():
        if key == LAUNCH_ARGS_KEY or not is_node_pattern(key, value):
            continue
        if not isinstance(value, dict):
            raise RosConfigError(
                f"in '{source}': node section '{key}' must be a mapping with "
                f"'{PARAMS_KEY}' and/or '{REMAPS_KEY}'"
            )
        unknown = [k for k in value if k not in SECTION_KEYS]
        if unknown:
            hint = ""
            if "remappings" in unknown:
                hint = f" (remappings are spelled '{REMAPS_KEY}')"
            raise RosConfigError(
                f"in '{source}': node section '{key}' has unknown key(s) "
                f"{', '.join(repr(u) for u in unknown)}; expected "
                f"{' and/or '.join(SECTION_KEYS)}{hint}"
            )
        params = value.get(PARAMS_KEY) or {}
        remaps = value.get(REMAPS_KEY) or {}
        for name, got in ((PARAMS_KEY, params), (REMAPS_KEY, remaps)):
            if not isinstance(got, dict):
                raise RosConfigError(
                    f"in '{source}': '{key}' -> '{name}' must be a mapping"
                )
        sections.append(
            Section(pattern=key, params=flatten_params(params), remaps=dict(remaps),
                    source=source)
        )
    return sections


def flatten_params(params: dict, prefix: str = "") -> dict[str, Any]:
    """ROS nests parameter names with dots, so ``{a: {b: 1}}`` in YAML is the
    parameter ``a.b`` -- exactly how rcl reads a parameter file."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten_params(value, f"{name}."))
        else:
            out[name] = value
    return out


@dataclass
class RosConfig:
    """Every node section from every config file (and the CLI), in order.

    Later sources win, matching the typed layering: config files in the order
    given, then the CLI. Nothing is applied until a node asks for its share
    through `for_node`, which is also what records who matched what.

    ``debug`` decides whether the advisory warnings (a pattern that matched
    nothing, a typed field shadowing an untyped one) are printed at all: they are
    quiet by default and only appear under ``-d``, since neither changes what the
    launch does. ``--strict`` turns the same situations into errors regardless.
    """

    sections: list[Section] = field(default_factory=list)
    debug: bool = False

    def __bool__(self) -> bool:
        return bool(self.sections)

    def matching(self, fqn: str | None) -> list[Section]:
        """The sections that apply to this node, in order.

        ``fqn`` is None for a node whose name we cannot know (no ``name=`` given,
        so rcl picks the executable's own default at startup); only patterns that
        match every node can apply to it.
        """
        if fqn is None:
            return [s for s in self.sections if s.universal]
        return [s for s in self.sections if s.matches(fqn)]

    def for_node(self, fqn: str | None) -> tuple[dict[str, Any], dict[str, str]]:
        """-> (parameters, remappings) for the node with this fully-qualified name."""
        params: dict[str, Any] = {}
        remaps: dict[str, str] = {}
        for section in self.matching(fqn):
            section.matched.append(fqn or "<unnamed>")
            params.update(section.params)
            remaps.update(section.remaps)
        return params, remaps

    def unmatched(self) -> list[Section]:
        """Sections that reached no node -- a typo, or a node behind a disabled
        toggle. Warned about, not fatal (see design-notes.md, Q8 decision 3)."""
        return [s for s in self.sections if not s.matched]

    def note_shadowed(self, fqn: str | None, names: list[str]) -> None:
        """Record that a typed field beat these untyped parameters, so the
        effective config can say so where the value is printed."""
        for section in self.matching(fqn):
            section.shadowed |= {n for n in names if n in section.params}


# --------------------------------------------------------------------------
# The active config, for the duration of one build
# --------------------------------------------------------------------------
# `node()` needs the collected sections, but a launch function only receives its
# own typed config -- so the CLI makes them ambient for the build, the way
# launch's own LaunchContext is ambient inside an action.
_active: RosConfig | None = None


class active:
    """Context manager making a RosConfig the one `node()` reads."""

    def __init__(self, ros_config: RosConfig):
        self.ros_config = ros_config

    def __enter__(self) -> RosConfig:
        global _active
        self.previous = _active
        _active = self.ros_config
        return self.ros_config

    def __exit__(self, *exc) -> None:
        global _active
        _active = self.previous


def current() -> RosConfig:
    """The active RosConfig, or an empty one outside a build."""
    return _active if _active is not None else RosConfig()
