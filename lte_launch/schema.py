"""Access to the config schema.

All Pydantic-specific code lives in this module (Pydantic **1** API -- see
working/design-notes.md). The rest of lte-launch only sees ``FieldSpec``,
``Issue`` and ``SchemaError``, so the schema backend can be replaced later
without touching the CLI or config layering.
"""

from __future__ import annotations

import collections.abc
import difflib
import enum
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NamedTuple, Union, get_args, get_origin

import pydantic
from pydantic import BaseModel


def _check_pydantic_version() -> None:
    """Fail loudly and specifically, not with a bare AttributeError three
    frames down in walk(). This project deliberately targets Pydantic **1**,
    the version Ubuntu 24.04 apt/rosdep ships (python3-pydantic) -- see
    working/design-notes.md for why. A pip-installed Pydantic 2
    earlier on PYTHONPATH (a stray venv, `pip install --user`, ...) would
    shadow it and break every v1-only attribute access here (__fields__,
    parse_obj, ...) with a confusing AttributeError."""
    if not pydantic.VERSION.startswith("1."):
        raise ImportError(
            f"lte_launch needs Pydantic 1.x (the system package, "
            f"apt install python3-pydantic), but found {pydantic.VERSION} at "
            f"{pydantic.__file__}. schema.py uses the v1 API (__fields__, "
            "parse_obj, .schema(), ...) which Pydantic 2 does not have. "
            "Check for a pip-installed pydantic (a venv, --user site-packages, "
            "or PYTHONPATH pollution) shadowing the system one."
        )


_check_pydantic_version()

MISSING = object()

SCALARS = {int: "INT", float: "FLOAT", str: "TEXT", Path: "PATH"}


class RosParams(BaseModel):
    """Base class for a config section that *is* a node's ROS parameters.

    A plain nested model is launch config -- it shapes what the launch does. A
    ``RosParams`` model says "these fields are parameters of a node", so they can
    be handed to ``node(..., params=config.section)`` and reach the node without
    being restated anywhere. They are ordinary schema fields otherwise: flags,
    help, defaults and validation all work as usual, which is the whole point --
    the same parameters an untyped ``ros__parameters:`` block would carry, but
    typed and documented.
    """


def is_ros_params(value: Any) -> bool:
    return isinstance(value, RosParams) or (
        isinstance(value, type) and issubclass(value, RosParams)
    )


@dataclass
class FieldSpec:
    """One leaf field of a (possibly nested) config model."""

    path: tuple[str, ...]
    owner: type
    kind: str  # "scalar" | "bool" | "list" | "dict" | "other"
    type_name: str  # scalar type, or element/value type for list/dict
    choices: list[str] | None  # allowed values (of the scalar / list element)
    infer: bool  # dict values are untyped (Any): parse them as YAML
    required: bool
    default: Any
    description: str
    constraints: str

    @property
    def is_bool(self) -> bool:
        return self.kind == "bool"

    @property
    def supported(self) -> bool:
        """False for fields that get no command line flag."""
        return self.kind != "other"

    @property
    def dotted(self) -> str:
        return ".".join(self.path)


@dataclass
class Issue:
    path: tuple[str, ...]
    message: str
    input: Any = None
    missing: bool = False


class SchemaError(ValueError):
    def __init__(self, issues: list[Issue]):
        super().__init__(f"{len(issues)} configuration error(s)")
        self.issues = issues


def _unwrap_optional(t: Any) -> Any:
    if get_origin(t) is Union:
        args = [a for a in get_args(t) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return t


def _is_model(t: Any) -> bool:
    return isinstance(t, type) and issubclass(t, BaseModel)


def config_model(fn: Any) -> type | None:
    """The model of ``def f(config: MyConfig)``: a function whose only
    parameter is annotated with a model. None otherwise."""
    import inspect

    params = list(inspect.signature(fn).parameters)
    if len(params) != 1:
        return None
    hint = _hints(fn).get(params[0])
    return hint if _is_model(hint) else None


def model_from_signature(fn: Any) -> type[BaseModel]:
    """A model with one field per parameter of ``fn``: its annotation is the
    type, its default (a value or ``Field(...)``) the default; no default:
    required. The docstring is the model's (help text)."""
    import inspect

    hints = _hints(fn)
    fields: dict[str, Any] = {}
    for name, param in inspect.signature(fn).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError(f"@launch {fn.__name__}: *args/**kwargs can't be launch arguments")
        if name not in hints:
            raise TypeError(
                f"@launch {fn.__name__}: parameter '{name}' needs a type annotation "
                f"(def {fn.__name__}({name}: int = 0, ...))"
            )
        default = ... if param.default is param.empty else param.default
        fields[name] = (hints[name], default)
    camel = "".join(part.capitalize() for part in fn.__name__.split("_"))
    model = pydantic.create_model(f"{camel}Config", __module__=fn.__module__, **fields)
    model.__doc__ = inspect.getdoc(fn)
    return model


def _hints(fn: Any) -> dict:
    """Annotations, strings resolved in the function's module."""
    import typing

    try:
        return typing.get_type_hints(fn)
    except Exception as exc:
        raise TypeError(f"@launch {fn.__name__}: can't resolve its annotations: {exc}")


class _Cls(NamedTuple):
    kind: str
    type_name: str
    choices: list[str] | None
    infer: bool = False


def _scalar(t: Any) -> _Cls | None:
    t = _unwrap_optional(t)
    if get_origin(t) is Literal:
        choices = [str(c) for c in get_args(t)]
        return _Cls("scalar", "{" + "|".join(choices) + "}", choices)
    if isinstance(t, type) and issubclass(t, enum.Enum):
        choices = [str(e.value) for e in t]
        return _Cls("scalar", "{" + "|".join(choices) + "}", choices)
    if t is bool:
        return _Cls("bool", "BOOL", None)
    if t in SCALARS:
        return _Cls("scalar", SCALARS[t], None)
    # v1 synthesizes a Constrained*Value subclass for a Field with a
    # numeric/length bound (ge/gt/le/lt/min_length/max_length) -- a real
    # subclass of the plain type (ConstrainedFloatValue < float), but the
    # exact-type dict lookup above misses it. [ran]: `tolerance: float =
    # Field(0.5, gt=0)` has outer_type_ ConstrainedFloatValue, not float.
    if isinstance(t, type):
        for base, name in SCALARS.items():
            if issubclass(t, base):
                return _Cls("scalar", name, None)
    return None


def _classify(annotation: Any) -> _Cls:
    """Decide how a field maps to a command line flag."""
    t = _unwrap_optional(annotation)
    scalar = _scalar(t)
    if scalar is not None:
        return scalar

    origin = get_origin(t)
    args = get_args(t)

    # list[T], set[T], Sequence[T], tuple[T, ...] with a scalar T
    is_seq = origin in (list, set, frozenset, collections.abc.Sequence)
    if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
        is_seq, args = True, args[:1]
    if is_seq and len(args) == 1:
        elem = _scalar(args[0])
        if elem is not None:
            return _Cls("list", elem.type_name, elem.choices)

    # dict[str, T] with a scalar T, or dict[str, Any]
    if origin in (dict, collections.abc.Mapping) and len(args) == 2:
        if args[0] is str:
            if args[1] is Any or args[1] is object:
                return _Cls("dict", "YAML", None, infer=True)
            val = _scalar(args[1])
            if val is not None:
                return _Cls("dict", val.type_name, val.choices)

    name = getattr(t, "__name__", str(t)).upper()
    return _Cls("other", name, None)


def _constraints(field: Any) -> str:
    # v1 keeps ge/gt/le/lt/min_length/max_length directly on field_info
    # (v2 scatters them across a field.metadata list of annotated_types objects).
    info = field.field_info
    parts = []
    for attr, sym in (("ge", ">="), ("gt", ">"), ("le", "<="), ("lt", "<")):
        value = getattr(info, attr, None)
        if value is not None:
            parts.append(f"{sym}{value}")
    for attr, label in (("min_length", "len>="), ("max_length", "len<=")):
        value = getattr(info, attr, None)
        if value is not None:
            parts.append(f"{label}{value}")
    return ", ".join(parts)


def _field_default(field: Any) -> Any:
    """v1 has no get_default(); a factory-having field's plain .default is None."""
    if field.default_factory is not None:
        return field.default_factory()
    return field.default


def walk(
    model: type[BaseModel],
    path: tuple[str, ...] = (),
    defaults: BaseModel | None = None,
) -> list[FieldSpec]:
    """Return every leaf field of ``model``, recursing into nested models.

    ``defaults`` is the default *instance* of ``model`` supplied by the parent
    (e.g. ``sub: Sub = Sub(a=2)``); it takes precedence over ``Sub``'s own
    field defaults.
    """
    out: list[FieldSpec] = []
    for name, field in model.__fields__.items():
        # v1's outer_type_ already strips Optional[...] (verified for scalar,
        # model, list and dict fields), unlike v2's field.annotation.
        ann = field.outer_type_

        if _is_model(ann):
            sub = getattr(defaults, name, None) if defaults is not None else None
            if sub is None and not field.required:
                sub = _field_default(field)
            sub = sub if isinstance(sub, BaseModel) else None
            out.extend(walk(ann, path + (name,), sub))
            continue

        cls = _classify(ann)
        if defaults is not None and hasattr(defaults, name):
            required, default = False, getattr(defaults, name)
        elif field.required:
            required, default = True, MISSING
        else:
            required = False
            default = _field_default(field)

        out.append(
            FieldSpec(
                path=path + (name,),
                owner=model,
                kind=cls.kind,
                type_name=cls.type_name,
                choices=cls.choices,
                infer=cls.infer,
                required=required,
                default=default,
                description=field.field_info.description or "",
                constraints=_constraints(field),
            )
        )
    return out


def summary(model: type) -> str:
    """First non-empty line of the model's own docstring."""
    for line in (model.__doc__ or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


def unknown_keys(
    model: type[BaseModel], data: dict, prefix: tuple[str, ...] = ()
) -> list[Issue]:
    """Report keys in ``data`` that the schema does not have.

    Pydantic silently ignores unknown keys by default, which would let a typo
    in a config file pass unnoticed.
    """
    issues: list[Issue] = []
    fields = model.__fields__
    for key, value in data.items():
        key = str(key)
        if key not in fields:
            close = difflib.get_close_matches(key, list(fields), n=1)
            hint = f" (did you mean '{close[0]}'?)" if close else ""
            issues.append(Issue(prefix + (key,), f"unknown key{hint}"))
            continue
        ann = fields[key].outer_type_
        if _is_model(ann):
            if isinstance(value, dict):
                issues.extend(unknown_keys(ann, value, prefix + (key,)))
            else:
                issues.append(Issue(prefix + (key,), "expected a mapping"))
    return issues


def _lookup(data: dict, loc: tuple) -> Any:
    """v1's error dicts have no "input" key (unlike v2's); find the offending
    value ourselves by walking the original input along the error's loc path."""
    cur: Any = data
    for key in loc:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def validate(model: type[BaseModel], data: dict) -> BaseModel:
    try:
        return model.parse_obj(data)
    except pydantic.ValidationError as exc:
        raise SchemaError(
            [
                Issue(
                    path=tuple(str(p) for p in err["loc"]),
                    message=err["msg"],
                    input=_lookup(data, err["loc"]),
                    missing=err["type"] == "value_error.missing",
                )
                for err in exc.errors()
            ]
        ) from None


def json_schema(model: type[BaseModel]) -> dict:
    return model.schema()


def to_plain(instance: BaseModel) -> dict:
    """Validated config -> JSON-compatible nested dict. v1's .dict() leaves
    Path/enum/etc. as Python objects; round-tripping through .json() (which
    uses v1's own JSON encoder) gives the same JSON-compatible shape v2's
    model_dump(mode="json") does."""
    return json.loads(instance.json())
