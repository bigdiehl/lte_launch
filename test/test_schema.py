from typing import Any, Literal, Optional

import pydantic
import pytest
from pydantic import BaseModel, Field

from ros2_typed_launch import schema


def test_check_pydantic_version(monkeypatch):
    monkeypatch.setattr(pydantic, "VERSION", "1.10.14")
    schema._check_pydantic_version()  # does not raise
    monkeypatch.setattr(pydantic, "VERSION", "2.13.5")
    with pytest.raises(
        ImportError, match="needs Pydantic 1.x.*shadowing the system one"
    ):
        schema._check_pydantic_version()


class Sub(BaseModel):
    """Sub docs."""

    a: int = Field(1, ge=0, le=10, description="an int")
    tol: float = Field(0.5, gt=0)


class Item(BaseModel):
    n: int = 0


class M(BaseModel):
    """Top docs."""

    name: str = "robot"
    flag: bool = False
    algo: Literal["x", "y"] = "x"
    maybe: Optional[float] = None
    req: int
    tags: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    typed: dict[str, float] = Field(default_factory=dict)
    items: list[Item] = Field(default_factory=list)
    sub: Sub = Sub(a=2)


def specs():
    return {s.dotted: s for s in schema.walk(M)}


def test_walk_flattens_and_describes_every_field():
    assert set(specs()) == {
        "name",
        "flag",
        "algo",
        "maybe",
        "req",
        "tags",
        "params",
        "typed",
        "items",
        "sub.a",
        "sub.tol",
    }
    assert specs()["sub.a"].path == ("sub", "a")
    assert specs()["sub.a"].owner is Sub

    s = specs()
    assert s["name"].kind == "scalar" and s["name"].type_name == "TEXT"
    assert s["flag"].kind == "bool" and s["flag"].is_bool
    assert s["maybe"].kind == "scalar" and s["maybe"].type_name == "FLOAT"
    assert s["tags"].kind == "list" and s["tags"].type_name == "TEXT"
    assert s["typed"].kind == "dict" and not s["typed"].infer
    assert s["typed"].type_name == "FLOAT"
    assert s["params"].kind == "dict" and s["params"].infer
    assert s["items"].kind == "other" and not s["items"].supported
    assert (
        specs()["algo"].choices == ["x", "y"] and specs()["algo"].type_name == "{x|y}"
    )
    # Pydantic v1 synthesizes a Constrained*Value subclass (e.g.
    # ConstrainedFloatValue < float) for any Field(ge=/le=/gt=/lt=...); it must
    # still classify as the plain scalar type, not fall through to "other".
    assert s["sub.a"].kind == "scalar" and s["sub.a"].type_name == "INT"
    assert s["sub.tol"].kind == "scalar" and s["sub.tol"].type_name == "FLOAT"

    s = specs()
    assert s["req"].required and s["req"].default is schema.MISSING
    assert not s["name"].required and s["name"].default == "robot"
    assert s["tags"].default == []  # from default_factory
    # M declares sub: Sub = Sub(a=2), so sub.a defaults to 2, not Sub's own 1.
    assert s["sub.a"].default == 2
    assert s["sub.tol"].default == 0.5

    s = specs()
    assert s["sub.a"].constraints == ">=0, <=10"
    assert s["sub.a"].description == "an int"
    assert s["sub.tol"].constraints == ">0"

    assert schema.summary(M) == "Top docs."
    assert schema.summary(Item) == ""


def test_unknown_keys():
    issues = schema.unknown_keys(M, {"nmae": 1, "sub": {"tolx": 1, "a": 3}, "req": 1})
    found = {".".join(i.path): i.message for i in issues}
    assert "did you mean 'name'" in found["nmae"]
    assert "unknown key" in found["sub.tolx"]
    assert len(found) == 2

    assert schema.unknown_keys(M, {"params": {"anything": 1}}) == []  # free-form dict
    issues = schema.unknown_keys(M, {"sub": 3})  # nested model given a scalar
    assert issues and "mapping" in issues[0].message


def test_validate_and_dump():
    m = schema.validate(M, {"req": "3", "sub": {"a": "4"}})
    assert m.req == 3 and m.sub.a == 4

    with pytest.raises(schema.SchemaError) as err:
        schema.validate(M, {"req": 1, "sub": {"a": 99, "tol": -1}})
    got = {".".join(i.path): i for i in err.value.issues}
    assert set(got) == {"sub.a", "sub.tol"}
    assert got["sub.a"].input == 99 and not got["sub.a"].missing

    with pytest.raises(schema.SchemaError) as err:
        schema.validate(M, {})
    assert [(".".join(i.path), i.missing) for i in err.value.issues] == [("req", True)]

    sch = schema.json_schema(M)
    assert sch["properties"]["req"]["type"] == "integer"
    assert schema.to_plain(schema.validate(M, {"req": 1}))["sub"]["a"] == 2
