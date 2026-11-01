"""``$(...)`` substitutions in config values.

Reuses the **stock** launch parser -- ``launch.frontend.parse_substitution``, the
lark grammar behind the XML and YAML launch frontends -- so the vocabulary is
ROS's own, with no grammar of ours to keep in sync:
``$(find-pkg-share pkg)``, ``$(find-pkg-prefix pkg)``, ``$(env VAR [default])``,
``$(command '...')``, ``$(eval '...')``, and the rest.

Two differences from how stock launch uses it:

* expansion happens **at config load time**, to a real Python string, not a
  deferred ``Substitution`` object (principle 5: config values are real values);
* it is applied **per string leaf** of the already-parsed YAML, not to the file's
  raw text the way ``ParameterFile(allow_substs=True)`` does. That keeps keys and
  structure untouched, needs no temporary files, and lets an error name the key it
  came from instead of "the substituted parameter file is not a valid yaml file".

See working/design-notes.md, Q25.
"""

from __future__ import annotations

import re
from typing import Any

MARKER = "$("

#: ``$(var NAME)`` -- the one substitution whose meaning is ours to supply: it
#: reads a *launch configuration*, and we seed those from the launch's own
#: validated arguments (see ``config.expand_sections``).
VAR = re.compile(r"\$\(\s*var\s+([^\s)]+)")


class SubstitutionError(ValueError):
    """A ``$(...)`` that could not be expanded: printed without a traceback."""


def var_names(value: Any) -> list[str]:
    """Every name used in a ``$(var NAME)`` anywhere in ``value``, in order.

    Checked before expanding, so an unknown name is reported as what it is
    rather than as launch's "launch configuration 'x' does not exist".
    """
    if isinstance(value, str):
        return VAR.findall(value)
    if isinstance(value, dict):
        return [name for item in value.values() for name in var_names(item)]
    if isinstance(value, list):
        return [name for item in value for name in var_names(item)]
    return []


def _expand_text(text: str, where: str, variables: dict[str, str] | None) -> str:
    # Imported lazily, and only for a value that actually needs it, so that
    # config loading (and most tests) still work with no ROS on the path.
    try:
        import launch_ros.substitutions  # noqa: F401  (registers find-pkg-*)
        from launch import LaunchContext
        from launch.frontend.parse_substitution import parse_substitution
        from launch.utilities import perform_substitutions
    except ImportError as exc:
        raise SubstitutionError(
            f"{where}: '{text}' uses a $(...) substitution, which needs the ROS "
            f"'launch' packages ({exc}); source your ROS setup, or write the "
            "value out in full"
        ) from None

    context = LaunchContext()
    if variables:
        # What $(var NAME) reads. A launch file would push these itself; we fill
        # them in from the launch's own arguments.
        context.launch_configurations.update(variables)
    try:
        return perform_substitutions(context, parse_substitution(text))
    except Exception as exc:
        # Unwrap lark's VisitError, whose str() buries the real cause under a
        # "Error trying to process rule" preamble.
        cause = getattr(exc, "orig_exc", exc)
        raise SubstitutionError(f"{where}: cannot expand '{text}': {cause}") from None


def expand(
    value: Any, where: str = "config", variables: dict[str, str] | None = None
) -> Any:
    """Expand ``$(...)`` in every string in ``value`` (recursively).

    ``variables`` are what ``$(var NAME)`` can read. Anything without a ``$(``
    is returned untouched, so a config with no substitutions never loads the
    parser at all.
    """
    if isinstance(value, str):
        return _expand_text(value, where, variables) if MARKER in value else value
    if isinstance(value, dict):
        return {
            k: expand(v, f"{where}.{k}" if where else str(k), variables)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [expand(v, f"{where}[{i}]", variables) for i, v in enumerate(value)]
    return value
