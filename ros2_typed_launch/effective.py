"""The effective configuration, rendered as the config file it came from.

One renderer, two destinations: what ``--show-config`` prints and what every run
writes to a file are the same text, so the output is a config you can feed
straight back in with ``--config`` and get the same launch. Provenance and
matched nodes ride along as YAML comments, which a loader ignores.

Values are emitted as JSON, which is a subset of YAML -- unambiguous, and no
guessing about quoting.
"""

from __future__ import annotations

import json
from typing import Any

from . import config as cfg
from . import ros_config as rc
from . import schema

COMMENT_COLUMN = 36


def _line(indent: int, text: str, note: str = "") -> str:
    body = " " * indent + text
    if not note:
        return body
    # Always at least two spaces, so a comment is distinguishable from a value
    # that happens to contain " #".
    pad = max(COMMENT_COLUMN - len(body), 2)
    return f"{body}{' ' * pad}# {note}"


def _value(value: Any) -> str:
    """A YAML scalar/flow value. JSON is valid YAML, so this needs no quoting
    rules of its own."""
    return json.dumps(value)


def _launch_args(model: type, config: Any, sources: dict) -> list[str]:
    """The typed part, in schema order, nested exactly as it is written."""
    plain = schema.to_plain(config)
    lines = [_line(0, f"{rc.LAUNCH_ARGS_KEY}:")]
    emitted: set[tuple[str, ...]] = set()

    for spec in schema.walk(model):
        # Open any parent models this field sits under.
        for depth in range(1, len(spec.path)):
            parent = spec.path[:depth]
            if parent not in emitted:
                emitted.add(parent)
                lines.append(_line(2 * depth, f"{parent[-1]}:"))
        found, value = cfg.lookup(plain, spec.path)
        if not found:
            continue
        lines.append(
            _line(
                2 * len(spec.path),
                f"{spec.path[-1]}: {_value(value)}",
                sources.get(spec.path, "default"),
            )
        )
    return lines


def _merge_by_pattern(ros: rc.RosConfig) -> dict[str, dict]:
    """One entry per node pattern, however many sources set it.

    Sections sharing a pattern (a config file and a `--ros-param`, say) are
    merged in order, later winning, with each surviving value keeping its own
    source. Emitting them as separate blocks would put duplicate keys in the
    output -- which reloads correctly by last-wins, but is a YAML smell, and
    would be rejected outright once Q6's duplicate-key check exists. Since
    provenance now sits on each value, merging loses nothing and shows the
    layering better.
    """
    merged: dict[str, dict] = {}
    for section in ros.sections:
        entry = merged.setdefault(
            section.pattern, {"params": {}, "remaps": {}, "matched": []}
        )
        for name, value in section.params.items():
            note = section.source
            if name in section.shadowed:
                note += ", shadowed by a typed field"
            entry["params"][name] = (value, note)
        for src, dst in section.remaps.items():
            entry["remaps"][src] = (dst, section.source)
        entry["matched"] += section.matched
    return merged


def _sections(ros: rc.RosConfig) -> list[str]:
    """The untyped part: one block per node pattern, with ``ros__parameters``
    and ``ros__remappings`` kept firmly apart -- they are different things, and
    one of them is a lane ROS itself has no wildcard for.

    The ``->`` comment is which nodes *this build* matched, which is not always
    the node's eventual runtime name: see `_matched`.
    """
    lines: list[str] = []
    for pattern, entry in _merge_by_pattern(ros).items():
        lines.append("")
        lines.append(_line(0, f'"{pattern}":', _matched(entry["matched"])))
        for key, items in (
            (rc.PARAMS_KEY, entry["params"]),
            (rc.REMAPS_KEY, entry["remaps"]),
        ):
            if not items:
                continue
            lines.append(_line(2, f"{key}:"))
            quote = key == rc.REMAPS_KEY  # a topic name is not an identifier
            for name, (value, note) in items.items():
                shown = _value(name) if quote else name
                lines.append(_line(4, f"{shown}: {_value(value)}", note))
    return lines


def _matched(matched: list[str]) -> str:
    """``-> /ns/node`` for a pattern that reached something, else ``NO MATCH``.

    This is *build-time* knowledge, from each node's declared name and namespace.
    A node whose name is decided at runtime -- no ``name=`` (rcl then uses the
    executable's default), a name built from a substitution, or a namespace pushed
    by an enclosing ``PushRosNamespace`` -- is not matched here, and says
    ``NO MATCH`` rather than pretending otherwise.
    """
    return "-> " + (", ".join(dict.fromkeys(matched)) if matched else "NO MATCH")


def render(
    model: type, config: Any, sources: dict, ros: rc.RosConfig, label: str = ""
) -> str:
    """The whole effective config as a loadable YAML document."""
    header = ["# Effective configuration" + (f" for {label}" if label else "")]
    header.append(
        "# Comments show where each value came from; they are not part of the data."
    )
    if ros:
        header.append(
            "# '->' on a node pattern is what it matched in this build, from each "
            "node's"
        )
        header.append(
            "# declared name/namespace -- a name only known at runtime cannot be "
            "matched here."
        )
    lines = header + [""] + _launch_args(model, config, sources)
    if ros:
        lines += _sections(ros)
    return "\n".join(lines) + "\n"
