"""$(...) expansion in config values -- via the stock launch parser."""

import pytest

from ros2_typed_launch import subst


def test_values_without_a_substitution_are_untouched():
    # The fast path: no "$(" anywhere means the parser is never even imported,
    # so a plain config still loads with no ROS on the path.
    data = {"a": "plain", "b": 3, "c": [True, None], "d": {"e": "also plain"}}
    assert subst.expand(data) == data


def test_expands_the_documented_substitutions(monkeypatch):
    pytest.importorskip("launch_ros")
    monkeypatch.setenv("TLAUNCH_TEST_VAR", "/from/env")

    assert subst.expand("$(env TLAUNCH_TEST_VAR)/x") == "/from/env/x"
    assert subst.expand("$(env TLAUNCH_NOT_SET fallback)") == "fallback"
    # find-pkg-* live in launch_ros, which subst imports for us -- the gotcha
    # that otherwise gives "Unknown substitution: find-pkg-share".
    assert subst.expand("$(find-pkg-share ros2_typed_launch)").endswith(
        "/share/ros2_typed_launch"
    )
    assert subst.expand("$(find-pkg-prefix ros2_typed_launch)")
    # ...and recursively, through lists and nested mappings.
    out = subst.expand({"paths": ["$(env HOME)/a", "plain"], "n": {"p": "$(env HOME)"}})
    assert out["paths"][0].endswith("/a") and out["paths"][1] == "plain"
    assert out["n"]["p"] and "$(" not in out["n"]["p"]


def test_a_failed_substitution_names_the_key_and_the_cause():
    pytest.importorskip("launch_ros")
    with pytest.raises(subst.SubstitutionError, match=r"config\.robot\.map"):
        subst.expand({"robot": {"map": "$(find-pkg-share no_such_package_xyz)"}})

    with pytest.raises(subst.SubstitutionError) as exc:
        subst.expand("$(not-a-substitution x)")
    assert "Unknown substitution" in str(exc.value)
    assert "VisitError" not in str(exc.value)   # lark's wrapper is unwrapped


def test_var_names_finds_every_variable_a_value_asks_for():
    # Scanned before expanding, so an unknown name can be reported as such.
    assert subst.var_names("/$(var namespace)/joy_vel") == ["namespace"]
    assert subst.var_names("$(var a.b) and $(var c)") == ["a.b", "c"]
    assert subst.var_names("$( var  spaced )") == ["spaced"]
    assert subst.var_names("$(env HOME)") == []
    assert subst.var_names({"k": ["$(var x)", {"j": "$(var y.z)"}]}) == ["x", "y.z"]


def test_var_reads_the_variables_it_is_given():
    pytest.importorskip("launch_ros")
    variables = {"namespace": "alpha", "turtlesim.background_r": "69"}
    assert subst.expand("/$(var namespace)/joy_vel", variables=variables) == "/alpha/joy_vel"
    assert subst.expand("$(var turtlesim.background_r)", variables=variables) == "69"
    # Mixed with another substitution, and nested.
    out = subst.expand({"a": ["$(var namespace)-$(env HOME)"]}, variables=variables)
    assert out["a"][0].startswith("alpha-/")


def test_var_without_variables_fails_like_any_missing_configuration():
    pytest.importorskip("launch_ros")
    with pytest.raises(subst.SubstitutionError, match="does not exist"):
        subst.expand("$(var namespace)")
