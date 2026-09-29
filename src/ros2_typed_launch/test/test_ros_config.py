"""The untyped lanes: node patterns, matching, and the config file's two parts."""

import pytest

from ros2_typed_launch import config as cfg
from ros2_typed_launch import ros_config as rc


# ------------------------------------------------------------------ patterns
@pytest.mark.parametrize(
    "pattern,fqn,expected",
    [
        ("/**", "/talker", True),                  # ** is zero or more tokens
        ("/**", "/ns/sub/talker", True),
        ("/**/planner", "/planner", True),
        ("/**/planner", "/a/b/planner", True),
        ("/**/planner", "/planner2", False),       # not a prefix match
        ("/ns/*", "/ns/talker", True),             # * is exactly one token
        ("/ns/*", "/ns/sub/talker", False),
        ("/ns/*", "/other/talker", False),
        ("/ns/talker", "/ns/talker", True),
        ("/ns/talker", "/ns/talker2", False),
    ],
)
def test_pattern_matching(pattern, fqn, expected):
    assert bool(rc.pattern_regex(pattern).match(fqn)) is expected


def test_is_node_pattern():
    # Looks like a pattern...
    assert rc.is_node_pattern("/**", None)
    assert rc.is_node_pattern("/ns/node", None)
    # ...or proves it by its contents.
    assert rc.is_node_pattern("talker", {"ros__parameters": {}})
    # A stray top-level key is neither, so it stays an unknown-key error.
    assert not rc.is_node_pattern("turtlesim", {"background_r": 1})


# ------------------------------------------------------------------ sections
def test_parse_sections_flattens_nested_param_names():
    # ROS nests parameter names with dots, exactly as rcl reads a param file.
    sections = rc.parse_sections(
        {"/**": {"ros__parameters": {"qos": {"depth": 10}, "flat": 1}}}, "f.yaml"
    )
    assert sections[0].params == {"qos.depth": 10, "flat": 1}


def test_parse_sections_rejects_unknown_keys_and_names_the_rename():
    with pytest.raises(rc.RosConfigError, match=r"spelled 'ros__remappings'"):
        rc.parse_sections({"/**": {"remappings": {"/a": "/b"}}}, "f.yaml")
    with pytest.raises(rc.RosConfigError, match="unknown key"):
        rc.parse_sections({"/**": {"ros__params": {}}}, "f.yaml")


def test_for_node_later_sections_win_and_matches_are_recorded():
    ros = rc.RosConfig([
        rc.Section("/**", {"a": 1, "b": 2}, {}, "first.yaml"),
        rc.Section("/**/talker", {"b": 3}, {"/in": "/out"}, "second.yaml"),
        rc.Section("/other", {"c": 9}, {}, "third.yaml"),
    ])
    params, remaps = ros.for_node("/ns/talker")
    assert params == {"a": 1, "b": 3}          # second.yaml wins on b
    assert remaps == {"/in": "/out"}
    # Only the section that reached nothing is reported.
    assert [s.pattern for s in ros.unmatched()] == ["/other"]


def test_for_node_without_a_name_only_gets_universal_patterns():
    # No name= means rcl picks the executable's default at startup, which we
    # cannot know, so a targeted pattern must not silently apply.
    ros = rc.RosConfig([
        rc.Section("/**", {"a": 1}, {}, "f.yaml"),
        rc.Section("/**/talker", {"b": 2}, {}, "f.yaml"),
    ])
    assert ros.for_node(None) == ({"a": 1}, {})


# --------------------------------------------------------------- file shape
def test_split_file_separates_the_two_parts(write):
    path = write(
        "c.yaml",
        """
        launch_args:
          turtlesim: {background_r: 99}
        /**:
          ros__parameters: {use_sim_time: true}
        /ns/planner:
          ros__remappings: {/scan: /lidar/scan}
        """,
        raw=True,
    )
    launch_args, sections = cfg.split_file(cfg.load_config_file(path), path)
    assert launch_args == {"turtlesim": {"background_r": 99}}
    assert [s.pattern for s in sections] == ["/**", "/ns/planner"]
    assert sections[0].params == {"use_sim_time": True}
    assert sections[1].remaps == {"/scan": "/lidar/scan"}


def test_split_file_rejects_the_old_bare_root_shape(write):
    # The pre-launch_args file shape: a schema key at the top level. Must say so,
    # not silently ignore the file's contents.
    path = write("c.yaml", "turtlesim: {background_r: 99}\n", raw=True)
    with pytest.raises(cfg.ConfigFileError, match=r"go under 'launch_args:'"):
        cfg.split_file(cfg.load_config_file(path), path)
