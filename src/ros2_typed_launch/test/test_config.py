import pytest

from ros2_typed_launch import config as cfg
from ros2_typed_launch import ros_config as rc


def test_deep_merge():
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    over = {"a": {"y": 3, "z": 4}, "b": 2}
    assert cfg.deep_merge(base, over) == {"a": {"x": 1, "y": 3, "z": 4}, "b": 2}
    assert base == {"a": {"x": 1, "y": 2}, "b": 1}  # inputs untouched
    assert cfg.deep_merge({"t": [1, 2]}, {"t": [3]}) == {"t": [3]}  # lists replace


def test_nest_and_lookup():
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


@pytest.mark.parametrize("text", ["- 1\n- 2\n", "just a string\n", "a: [unclosed\n"])
def test_load_config_file_rejects_bad_files(write, text):
    with pytest.raises(cfg.ConfigFileError):
        cfg.load_config_file(write("bad.yaml", text, raw=True))


def test_load_config_file_missing():
    with pytest.raises(cfg.ConfigFileError, match="cannot read"):
        cfg.load_config_file("/nonexistent/file.yaml")


def test_resolve_config_ref_plain_path_wins(write):
    path = write("x.yaml", "a: 1\n")
    assert cfg.resolve_config_ref(path) == path


def test_resolve_config_ref_package_syntax(tmp_path, monkeypatch):
    share = tmp_path / "share"
    (share / "config").mkdir(parents=True)
    (share / "config" / "site.yaml").write_text("a: 1\n")
    monkeypatch.setattr(cfg, "_package_share_dir", lambda pkg: str(share))

    assert cfg.resolve_config_ref("mypkg:config/site.yaml") == str(
        share / "config" / "site.yaml"
    )
    assert cfg.resolve_config_ref("mypkg:/config/site.yaml") == str(
        share / "config" / "site.yaml"
    )
    with pytest.raises(cfg.ConfigFileError, match="no file 'config/nope.yaml'"):
        cfg.resolve_config_ref("mypkg:config/nope.yaml")


def test_resolve_config_ref_existing_file_beats_package_reading(tmp_path, monkeypatch):
    # A file literally named "pkg:thing.yaml" in the cwd is used as a path.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pkg:thing.yaml").write_text("a: 1\n")
    monkeypatch.setattr(
        cfg, "_package_share_dir", lambda pkg: pytest.fail("must not look up package")
    )
    assert cfg.resolve_config_ref("pkg:thing.yaml") == "pkg:thing.yaml"


def test_resolve_config_ref_non_reference_passes_through():
    assert cfg.resolve_config_ref("no_such_dir/file.yaml") == "no_such_dir/file.yaml"


# ------------------------------------------------- $(var ...): launch args as values
def test_variables_turns_launch_args_into_substitution_values():
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
        "turtlesim.holonomic": "false",     # launch's own spelling, not "False"
        "turtlesim.scale": "1.5",
    }
    assert unusable == {"tags": "is a list", "unset": "is not set"}


def test_unknown_vars_checks_sections_against_the_schemas_argument_names():
    known = {"namespace", "robot.name"}
    sections = [
        rc.Section("/$(var nope)/x", {"a": "$(var robot.name)"},
                   {"b": "$(var nmespace)"}, "a.yaml"),
    ]
    problems = cfg.unknown_vars(sections, known)
    assert len(problems) == 2                       # robot.name is fine
    assert "$(var nope): not an argument of this launch" in problems[0]
    assert "did you mean 'namespace'" in problems[1]
    assert cfg.unknown_vars([rc.Section("/x", {}, {}, "a.yaml")], known) == []


def test_unknown_vars_reports_a_repeated_name_once():
    """A config meant for another launch names the same argument everywhere;
    saying so four times adds nothing."""
    sections = [
        rc.Section("/$(var ns)/a", {"p": "$(var ns)"}, {"r": "$(var ns)"}, "a.yaml"),
        rc.Section("/$(var ns)/b", {}, {}, "a.yaml"),
    ]
    problems = cfg.unknown_vars(sections, {"namespace"})
    assert len(problems) == 1
    assert "used 4 times" in problems[0]


def test_check_vars_says_which_name_and_why():
    usable, unusable = cfg.variables({"namespace": "demo", "tags": ["a"]})

    cfg.check_vars("/$(var namespace)/x", "where", usable, unusable)   # fine

    with pytest.raises(cfg.ConfigFileError, match="did you mean 'namespace'"):
        cfg.check_vars("$(var nmespace)", "where", usable, unusable)
    with pytest.raises(cfg.ConfigFileError, match="'tags' is a list"):
        cfg.check_vars("$(var tags)", "where", usable, unusable)


def test_a_launch_arg_cannot_be_built_from_another_one():
    with pytest.raises(cfg.ConfigFileError, match="cannot be built from another one"):
        cfg.reject_vars({"node_name": "$(var namespace)_node"}, "a.yaml")
    cfg.reject_vars({"path": "$(env HOME)/x"}, "a.yaml")       # other substitutions are fine


def test_expand_sections_resolves_values_and_the_pattern(monkeypatch):
    pytest.importorskip("launch_ros")
    sections = [
        rc.Section("/$(var namespace)/turtlesim", {"n": "$(var turtlesim.background_r)"},
                   {"turtle1/cmd_vel": "/$(var namespace)/joy_vel"}, "a.yaml"),
    ]
    cfg.expand_sections(sections, {"namespace": "alpha", "turtlesim": {"background_r": 7}})

    section = sections[0]
    assert section.pattern == "/alpha/turtlesim"
    assert section.matches("/alpha/turtlesim")      # the recompiled pattern
    assert not section.matches("/demo/turtlesim")
    assert section.params == {"n": "7"}             # a substitution is always text
    assert section.remaps == {"turtle1/cmd_vel": "/alpha/joy_vel"}


def test_an_empty_argument_does_not_leave_a_double_slash_behind():
    """The case that makes this matter: an empty namespace substituted into a
    path. ROS would reject '//joy_vel'; the node means '/joy_vel'."""
    pytest.importorskip("launch_ros")
    sections = [
        rc.Section("/$(var namespace)/turtlesim", {},
                   {"turtle1/cmd_vel": "/$(var namespace)/joy_vel"}, "a.yaml"),
    ]
    cfg.expand_sections(sections, {"namespace": ""})

    assert sections[0].pattern == "/turtlesim"
    assert sections[0].matches("/turtlesim")
    assert sections[0].remaps == {"turtle1/cmd_vel": "/joy_vel"}


def test_a_remap_name_that_ros_would_refuse_is_a_config_error():
    pytest.importorskip("rclpy")
    sections = [rc.Section("/**", {}, {"turtle1/cmd_vel": "joy vel"}, "a.yaml")]
    with pytest.raises(cfg.ConfigFileError, match="is not a valid ROS name"):
        cfg.expand_sections(sections, {})
