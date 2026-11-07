"""Config files: loading, merging, references, and $(...) expansion."""

import pytest

from lte_launch import config as cfg
from lte_launch import ros_config as rc
from lte_launch import subst


def test_merge_nest_and_lookup():
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    over = {"a": {"y": 3, "z": 4}, "b": 2}
    assert cfg.deep_merge(base, over) == {"a": {"x": 1, "y": 3, "z": 4}, "b": 2}
    assert base == {"a": {"x": 1, "y": 2}, "b": 1}  # inputs untouched
    assert cfg.deep_merge({"t": [1, 2]}, {"t": [3]}) == {"t": [3]}  # lists replace

    nested = cfg.nest({"a.b": 1, "a.c": 2, "d": 3})
    assert nested == {"a": {"b": 1, "c": 2}, "d": 3}
    assert cfg.lookup(nested, ("a", "c")) == (True, 2)
    assert cfg.lookup(nested, ("a", "zzz")) == (False, None)
    assert cfg.lookup(nested, ("d", "x")) == (False, None)


def test_load_config_file(write):
    assert cfg.load_config_file(write("c.yaml", "a: 1\nb: {c: 2}\n", raw=True)) == {
        "a": 1,
        "b": {"c": 2},
    }
    assert cfg.load_config_file(write("empty.yaml", "", raw=True)) == {}
    for text in ("- 1\n- 2\n", "just a string\n", "a: [unclosed\n"):
        with pytest.raises(cfg.ConfigFileError):
            cfg.load_config_file(write("bad.yaml", text, raw=True))
    with pytest.raises(cfg.ConfigFileError, match="cannot read"):
        cfg.load_config_file("/nonexistent/file.yaml")


def test_resolve_config_ref(write, tmp_path, monkeypatch):
    path = write("x.yaml", "a: 1\n")
    assert cfg.resolve_config_ref(path) == path  # a plain path wins
    assert cfg.resolve_config_ref("no_such_dir/f.yaml") == "no_such_dir/f.yaml"

    share = tmp_path / "share"
    (share / "config").mkdir(parents=True)
    (share / "config" / "site.yaml").write_text("a: 1\n")
    monkeypatch.setattr(cfg, "_package_share_dir", lambda pkg: str(share))
    site = str(share / "config" / "site.yaml")
    assert cfg.resolve_config_ref("mypkg:config/site.yaml") == site
    assert cfg.resolve_config_ref("mypkg:/config/site.yaml") == site
    with pytest.raises(cfg.ConfigFileError, match="no file 'config/nope.yaml'"):
        cfg.resolve_config_ref("mypkg:config/nope.yaml")

    # A file literally named "pkg:thing.yaml" in the cwd is used as a path.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pkg:thing.yaml").write_text("a: 1\n")
    monkeypatch.setattr(
        cfg, "_package_share_dir", lambda pkg: pytest.fail("must not look up package")
    )
    assert cfg.resolve_config_ref("pkg:thing.yaml") == "pkg:thing.yaml"


# ------------------------------------------------- $(var ...): launch args as values
def test_launch_args_become_variables_and_are_checked():
    usable, unusable = cfg.variables(
        {
            "namespace": "demo",
            "turtlesim": {"background_r": 69, "holonomic": False, "scale": 1.5},
            "tags": ["a"],
            "unset": None,
        }
    )
    assert usable == {
        "namespace": "demo",
        "turtlesim.background_r": "69",
        "turtlesim.holonomic": "false",  # launch's own spelling, not "False"
        "turtlesim.scale": "1.5",
    }
    assert unusable == {"tags": "is a list", "unset": "is not set"}

    cfg.check_vars("/$(var namespace)/x", "where", usable, unusable)  # fine
    with pytest.raises(cfg.ConfigFileError, match="did you mean 'namespace'"):
        cfg.check_vars("$(var nmespace)", "where", usable, unusable)
    with pytest.raises(cfg.ConfigFileError, match="'tags' is a list"):
        cfg.check_vars("$(var tags)", "where", usable, unusable)

    with pytest.raises(cfg.ConfigFileError, match="cannot be built from another one"):
        cfg.reject_vars({"node_name": "$(var namespace)_node"}, "a.yaml")
    cfg.reject_vars({"path": "$(env HOME)/x"}, "a.yaml")  # other substitutions are fine


def test_unknown_vars_are_found_against_the_schema():
    known = {"namespace", "robot.name"}
    sections = [
        rc.Section(
            "/$(var nope)/x",
            {"a": "$(var robot.name)"},
            {"b": "$(var nmespace)"},
            "a.yaml",
        ),
    ]
    problems = cfg.unknown_vars(sections, known)
    assert len(problems) == 2  # robot.name is fine
    assert "$(var nope): not an argument of this launch" in problems[0]
    assert "did you mean 'namespace'" in problems[1]
    assert cfg.unknown_vars([rc.Section("/x", {}, {}, "a.yaml")], known) == []

    # A config meant for another launch names the same argument everywhere:
    # reported once.
    sections = [
        rc.Section("/$(var ns)/a", {"p": "$(var ns)"}, {"r": "$(var ns)"}, "a.yaml"),
        rc.Section("/$(var ns)/b", {}, {}, "a.yaml"),
    ]
    problems = cfg.unknown_vars(sections, {"namespace"})
    assert len(problems) == 1 and "used 4 times" in problems[0]


def test_expand_sections_resolves_values_and_the_pattern():
    pytest.importorskip("rclpy")
    sections = [
        rc.Section(
            "/$(var namespace)/turtlesim",
            {"n": "$(var turtlesim.background_r)"},
            {"turtle1/cmd_vel": "/$(var namespace)/joy_vel"},
            "a.yaml",
        ),
    ]
    cfg.expand_sections(
        sections, {"namespace": "alpha", "turtlesim": {"background_r": 7}}
    )
    section = sections[0]
    assert section.pattern == "/alpha/turtlesim"
    assert section.matches("/alpha/turtlesim")  # the recompiled pattern
    assert not section.matches("/demo/turtlesim")
    assert section.params == {"n": 7}  # a substituted parameter is typed again (Q25)
    assert section.substituted == {"n": "$(var turtlesim.background_r)"}
    assert section.remaps == {"turtle1/cmd_vel": "/alpha/joy_vel"}

    # An empty namespace substituted into a path: ROS would reject
    # '//joy_vel'; the node means '/joy_vel'.
    sections = [
        rc.Section(
            "/$(var namespace)/turtlesim",
            {},
            {"turtle1/cmd_vel": "/$(var namespace)/joy_vel"},
            "a.yaml",
        ),
    ]
    cfg.expand_sections(sections, {"namespace": ""})
    assert sections[0].pattern == "/turtlesim"
    assert sections[0].matches("/turtlesim")
    assert sections[0].remaps == {"turtle1/cmd_vel": "/joy_vel"}

    sections = [rc.Section("/**", {}, {"turtle1/cmd_vel": "joy vel"}, "a.yaml")]
    with pytest.raises(cfg.ConfigFileError, match="is not a valid ROS name"):
        cfg.expand_sections(sections, {})


# ------------------------------------------------------------ subst.expand
def test_expand_uses_the_stock_substitutions(monkeypatch):
    # The fast path: no "$(" anywhere means the parser is never even imported,
    # so a plain config still loads with no ROS on the path.
    data = {"a": "plain", "b": 3, "c": [True, None], "d": {"e": "also plain"}}
    assert subst.expand(data) == data

    pytest.importorskip("launch_ros")
    monkeypatch.setenv("TLAUNCH_TEST_VAR", "/from/env")
    assert subst.expand("$(env TLAUNCH_TEST_VAR)/x") == "/from/env/x"
    assert subst.expand("$(env TLAUNCH_NOT_SET fallback)") == "fallback"
    # find-pkg-* live in launch_ros, which subst imports for us -- the gotcha
    # that otherwise gives "Unknown substitution: find-pkg-share".
    assert subst.expand("$(find-pkg-share lte_launch)").endswith(
        "/share/lte_launch"
    )
    assert subst.expand("$(find-pkg-prefix lte_launch)")
    # ...and recursively, through lists and nested mappings.
    out = subst.expand({"paths": ["$(env HOME)/a", "plain"], "n": {"p": "$(env HOME)"}})
    assert out["paths"][0].endswith("/a") and out["paths"][1] == "plain"
    assert out["n"]["p"] and "$(" not in out["n"]["p"]

    variables = {"namespace": "alpha", "turtlesim.background_r": "69"}
    assert (
        subst.expand("/$(var namespace)/joy_vel", variables=variables)
        == "/alpha/joy_vel"
    )
    assert subst.expand("$(var turtlesim.background_r)", variables=variables) == "69"
    out = subst.expand({"a": ["$(var namespace)-$(env HOME)"]}, variables=variables)
    assert out["a"][0].startswith("alpha-/")

    # Q25: a substituted parameter reads as YAML again, as in stock launch_ros;
    # only a leaf that had a "$(" is touched, and ``origins`` says what it was.
    monkeypatch.setenv("TLAUNCH_RATE", "30")
    origins: dict = {}
    out = subst.expand(
        {"rate": "$(env TLAUNCH_RATE)", "on": "$(env X true)", "k": "$(env X 1.5)",
         "ids": "$(env X '[1, 2]')", "mixed": "$(env X '[1, a]')",
         "keep": "$(env X \"'007'\")", "plain": "30", "map": "$(env X '{a: 1}')"},
        "p", typed=True, origins=origins,
    )  # fmt: skip
    assert out == {"rate": 30, "on": True, "k": 1.5, "ids": [1, 2],
                   "mixed": "[1, a]", "keep": "007", "plain": "30", "map": "{a: 1}"}  # fmt: skip
    assert origins["p.rate"] == "$(env TLAUNCH_RATE)" and "p.plain" not in origins
    assert subst.expand("$(env TLAUNCH_RATE)") == "30"  # not typed: text
    with pytest.raises(subst.SubstitutionError, match="cannot be empty"):
        subst.expand({"x": "$(env TLAUNCH_NOT_SET '')"}, "p", typed=True)


def test_a_failed_substitution_names_the_key_and_the_cause():
    pytest.importorskip("launch_ros")
    with pytest.raises(subst.SubstitutionError, match=r"config\.robot\.map"):
        subst.expand({"robot": {"map": "$(find-pkg-share no_such_package_xyz)"}})
    with pytest.raises(subst.SubstitutionError) as exc:
        subst.expand("$(not-a-substitution x)")
    assert "Unknown substitution" in str(exc.value)
    assert "VisitError" not in str(exc.value)  # lark's wrapper is unwrapped
    with pytest.raises(subst.SubstitutionError, match="does not exist"):
        subst.expand("$(var namespace)")  # no variables given


def test_var_names_finds_every_variable_a_value_asks_for():
    # Scanned before expanding, so an unknown name can be reported as such.
    assert subst.var_names("/$(var namespace)/joy_vel") == ["namespace"]
    assert subst.var_names("$(var a.b) and $(var c)") == ["a.b", "c"]
    assert subst.var_names("$( var  spaced )") == ["spaced"]
    assert subst.var_names("$(env HOME)") == []
    assert subst.var_names({"k": ["$(var x)", {"j": "$(var y.z)"}]}) == ["x", "y.z"]
