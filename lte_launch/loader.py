"""Find and load typed launch files.

A launch is addressed two ways:

- an existing file path (a standalone launch, not part of any installed
  package -- loaded with ``importlib.util``, the way ``python path/to/file.py``
  would see it: no parent package, so relative imports don't work in it);
- ``(package, name)``, e.g. ``("lte_launch", "turtle_demo_launch.py")``
  -- ``name`` is a **full filename, ``.py`` required**, found by searching the
  installed package for a ``*_launch.py`` file with that name, the way
  ``ros2 launch <package> <file>`` searches ``share/<package>/`` -- our launch
  files aren't under ``share/``, so the search walks the package's real
  ``__path__`` instead (including a top-level ``launch/`` directory installed
  as a nested subpackage; see the demo package's ``CMakeLists.txt``). The
  match is then loaded with a genuine ``importlib.import_module("package.sub.name")``,
  so relative imports between launch files work
  (``from .other_launch import ...``), and a file reached both by
  ``load_launch`` and by a plain ``import`` from elsewhere is the same module
  object (Python's own ``sys.modules`` cache), so there is exactly one
  ``TypedLaunch`` instance for it, however it was reached. ``name`` may also
  be given as an explicit dotted module path (``"examples.turtle_demo_launch"``,
  never a ``.py``) or the on-disk slash spelling with ``.py``
  (``"launch/turtle_demo_launch.py"``), to disambiguate if more than one file
  shares a name.

``resolve()`` is what ``lte-launch`` uses for its command-line target; ``load_launch``
is the same thing, for use inside a launch file to ``include`` another one.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import sys

from .core import TypedTarget

#: Filename suffixes lte-launch searches a package for.
TARGET_SUFFIXES = ("_launch.py",)


class LaunchLookupError(Exception):
    """A launch could not be found, loaded, or is not a typed launch."""


# realpath -> loaded launch, for the file-path route only. Module-based loads
# need no cache of our own: importlib.import_module already returns the same
# module (and thus the same TypedLaunch) every time.
_LOADED_BY_PATH: dict[str, TypedTarget] = {}


def _typed_target_from_module(module, where: str) -> TypedTarget:
    entry = getattr(module, "generate_launch_description", None)
    if entry is None:
        raise LaunchLookupError(
            f"'{where}' does not define generate_launch_description"
        )
    typed = getattr(entry, "typed_target", None)
    if not isinstance(typed, TypedTarget):
        raise LaunchLookupError(
            f"'{where}' is not a typed launch file (lte-launch only runs files built "
            "with @launch); use 'ros2 launch' for it"
        )
    return typed


def _load_from_path(path: str) -> TypedTarget:
    real = os.path.realpath(path)
    if real in _LOADED_BY_PATH:
        return _LOADED_BY_PATH[real]

    name = "_lte_launch_" + re.sub(r"\W", "_", real)
    spec = importlib.util.spec_from_file_location(name, real)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        if "relative import" in str(exc):
            raise LaunchLookupError(
                f"failed to load '{path}': {exc} (a file loaded by path has no "
                "parent package, so it can't use relative imports; if it's "
                "installed in a package, address it by name instead, e.g. "
                "'lte-launch lte_launch turtle_demo_launch.py')"
            ) from exc
        raise LaunchLookupError(
            f"failed to load '{path}': {type(exc).__name__}: {exc}"
        ) from exc
    except LaunchLookupError:
        raise
    except Exception as exc:
        raise LaunchLookupError(
            f"failed to load '{path}': {type(exc).__name__}: {exc}"
        ) from exc

    typed = _typed_target_from_module(module, path)
    _LOADED_BY_PATH[real] = typed
    return typed


def _find_launch_files(package_module) -> dict[str, list[str]]:
    """stem ("turtle_demo_launch") -> dotted module paths of every target file
    (``*_launch.py``) anywhere under the package, however
    deep (e.g. in a nested launch/ subpackage). A stem maps to more than one
    path only if two files in the package happen to share a name."""
    found: dict[str, list[str]] = {}
    for root in getattr(package_module, "__path__", []):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fname in filenames:
                if not fname.endswith(TARGET_SUFFIXES):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fname), root)
                dotted = f"{package_module.__name__}." + rel[:-3].replace(os.sep, ".")
                found.setdefault(fname[:-3], []).append(dotted)
    return found


def launch_file_names(package: str) -> list[str]:
    """Every target-file stem installed in `package`, sorted -- for tab completion.
    Empty (not an error) if the package doesn't exist or isn't importable."""
    try:
        module = importlib.import_module(package)
    except Exception:
        return []
    return sorted(_find_launch_files(module))


def _load_from_module(package: str, name: str) -> TypedTarget:
    try:
        package_module = importlib.import_module(package)
    except ModuleNotFoundError as exc:
        raise LaunchLookupError(
            f"package '{package}' not found: {exc} (built, and did you "
            "source install/setup.bash?)"
        ) from None

    stripped = name.strip("/")

    if stripped.endswith(".py"):
        stem = stripped[:-3]
        if "/" in stem:
            # on-disk path, to disambiguate a name shared by two files
            # (e.g. "launch/turtle_demo_launch.py").
            dotted = f"{package}.{stem.replace('/', '.')}"
        else:
            # The common case: a full filename, found by searching the
            # package for it -- like `ros2 launch <package> <file>` searches
            # share/<package>/, but over the package's real Python files.
            matches = sorted(set(_find_launch_files(package_module).get(stem, [])))
            if not matches:
                raise LaunchLookupError(
                    f"no launch file named '{name}' in package '{package}' "
                    "(looked for it anywhere in the package)"
                )
            if len(matches) > 1:
                options = ", ".join(m.removeprefix(f"{package}.") for m in matches)
                raise LaunchLookupError(
                    f"'{name}' is ambiguous in package '{package}': {options} -- "
                    "give the full dotted or on-disk path to pick one"
                )
            dotted = matches[0]
    elif "." in stripped:
        # An explicit dotted module path, for when a bare filename is
        # ambiguous (e.g. "examples.turtle_demo_launch").
        stem = stripped
        dotted = f"{package}.{stripped}"
    else:
        raise LaunchLookupError(
            f"'{name}': give the full filename, including '.py' (e.g. "
            f"'{name}.py'), or an explicit dotted module path"
        )

    try:
        module = importlib.import_module(dotted)
    except ModuleNotFoundError as exc:
        if exc.name == dotted:
            raise LaunchLookupError(
                f"no launch module '{stem}' in package '{package}'"
            ) from None
        raise LaunchLookupError(f"failed to import '{dotted}': {exc}") from exc
    except LaunchLookupError:
        raise
    except Exception as exc:
        raise LaunchLookupError(
            f"failed to load '{dotted}': {type(exc).__name__}: {exc}"
        ) from exc

    return _typed_target_from_module(module, dotted)


def resolve(*target: str) -> TypedTarget:
    """``(path,)`` or ``(package, file)`` -> the typed launch it holds."""
    if not target:
        raise LaunchLookupError("no launch given")
    if len(target) == 1:
        if os.path.isfile(target[0]):
            return _load_from_path(target[0])
        raise LaunchLookupError(
            f"'{target[0]}' is not a file; expected a path, or '<package> <file>'"
        )
    if len(target) == 2:
        return _load_from_module(*target)
    raise LaunchLookupError("expected a path, or '<package> <file>'")


def load_launch(*target: str) -> TypedTarget:
    """Load a typed launch, for use with ``include``.

        load_launch("/path/to/standalone_launch.py")
        load_launch("lte_launch", "turtle_teleop_launch.py")
        load_launch("lte_launch", "examples.turtle_teleop_launch")  # to disambiguate

    The ``(package, file)`` form is a real import: when you already know the package,
    prefer importing directly instead --
    ``from lte_launch.examples.turtle_teleop_launch import turtle_teleop``
    -- it is the same module either way. ``load_launch`` is for addressing a
    launch dynamically, by name.
    """
    return resolve(*target)
