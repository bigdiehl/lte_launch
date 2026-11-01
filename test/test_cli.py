import argparse
import json
import re
from pathlib import Path

import pytest
import yaml

from ros2_typed_launch import cli


def run(argv, capsys):
    """-> (exit code, stdout, stderr)"""
    code = cli.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def show(launch_file, extra, capsys):
    """Run --show-config -> ({dotted: value}, {dotted: source}).

    The output is a YAML config file (it can be fed back in with --config), so
    the values come from parsing it as one; the per-value provenance rides along
    in trailing comments, which YAML ignores and this reads separately.
    """
    code, out, err = run([launch_file, *extra, "--show-config"], capsys)
    assert code == 0, err

    values, sources, stack = {}, {}, []
    for line in out.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = re.split(r"\s{2,}#\s?", line, maxsplit=1)
        body, note = parts[0], parts[1] if len(parts) > 1 else ""
        indent = len(body) - len(body.lstrip())
        key, _, value = body.strip().partition(":")
        stack = stack[: indent // 2] + [key.strip('"')]
        # A line with a value is a leaf (collections are emitted in flow style,
        # so they stay on one line); a bare "key:" opens a nested model.
        if value.strip():
            dotted = ".".join(stack[1:])        # drop the launch_args/pattern root
            values[dotted] = json.loads(value.strip())
            sources[dotted] = note.strip()
    return values, sources


# ------------------------------------------------------------------ targets
def test_split_target_and_completion(sample_launch, fake_package, monkeypatch):
    assert cli.split_target([sample_launch, "--map", "m.yaml"]) == (
        [sample_launch], ["--map", "m.yaml"],
    )
    assert cli.split_target(["pkg", "x_launch", "--a.b", "1"]) == (
        ["pkg", "x_launch"], ["--a.b", "1"],
    )
    # tlaunch's own options may appear before or between the target's parts.
    assert cli.split_target(["--config", "c.yaml", "-d", "pkg", "--schema", "x_launch"]) == (
        ["pkg", "x_launch"], ["--config", "c.yaml", "-d", "--schema"],
    )
    # ...but the target must end before the first schema flag.
    assert cli.split_target(["pkg", "--x", "1", "x_launch"]) == (
        ["pkg"], ["--x", "1", "x_launch"],
    )

    # Completions are full filenames (".py" included), matching what tlaunch
    # now requires you to type.
    parsed_args = argparse.Namespace(package=fake_package)
    assert set(cli._complete_launch_file("", parsed_args)) == {
        "child_launch.py", "main_launch.py", "nested_launch.py", "not_a_launch.py",
    }

    pytest.importorskip("ament_index_python")
    import ament_index_python.packages as amt

    monkeypatch.setattr(amt, "get_packages_with_prefixes", lambda: {"my_pkg": "/x"})
    assert "my_pkg" in cli._complete_package_or_path("", argparse.Namespace())


# --------------------------------------------------------------- completion
def test_resolving_the_target(capsys, tmp_path, fake_package):
    code, out, _ = run([], capsys)
    assert code == 0 and "usage: tlaunch" in out

    path = tmp_path / "plain_launch.py"
    path.write_text("def generate_launch_description():\n    return None\n")
    code, _, err = run([str(path), "--show-config"], capsys)
    assert code == 2 and "not a typed launch file" in err

    code, _, err = run(["definitely_not_a_package", "x_launch"], capsys)
    assert code == 2 and "tlaunch: error:" in err

    code, out, err = run([fake_package, "main_launch.py", "--show-config"], capsys)
    assert code == 0, err
    assert "n: 5" in out


# --------------------------------------------------------------- flags/help
def test_help_and_schema(sample_launch, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([sample_launch, "-h"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "robot  [Robot]" in out and "planner  [Planner]" in out
    assert "--planner.max_iterations" in out          # snake_case, dotted
    assert "--robot.use_sim_time, --no-robot.use_sim_time" in out
    assert "{navfn|smac}" in out                       # Literal -> choices
    assert "(>=1, <=100000)" in out                    # bounds shown
    assert "[required]" in out and "[default: 0.9]" in out
    assert "(repeatable)" in out
    assert "robots (robot)" in out.replace("\n", " ") or "robots" in out  # epilog
    # The launch's own arguments come first, then tlaunch's, in three groups.
    assert out.index("planner  [Planner]") < out.index("tlaunch: config") \
        < out.index("tlaunch: running") < out.index("tlaunch: tmux")

    # tlaunch's options are exactly the ones target splitting knows about.
    parser = argparse.ArgumentParser()
    cli.add_own_args(parser)
    assert {o for a in parser._actions for o in a.option_strings} == cli.RESERVED

    # No launch given: the general help still lists every option.
    assert cli.main(["-h"]) == 0
    out = capsys.readouterr().out
    assert "tlaunch: tmux" in out and "--log-level" in out and "examples:" in out

    code, out, _ = run([sample_launch, "--schema"], capsys)
    assert code == 0 and "map" in json.loads(out)["properties"]


# ---------------------------------------------------------------- layering
def test_layering_defaults_files_then_cli(
    sample_launch, capsys, write, tmp_path, monkeypatch
):
    values, sources = show(sample_launch, ["--map", "m"], capsys)
    assert values["planner.tolerance"] == 0.9          # Planner(tolerance=0.9)
    assert values["planner.algorithm"] == "smac"
    assert sources["planner.tolerance"] == "default"
    assert sources["map"] == "cli"

    a = write("a.yaml", "map: from_a\nrobot: {name: alice, use_sim_time: true}\n")
    b = write("b.yaml", "robot: {name: bob}\n")
    values, sources = show(sample_launch, ["--config", a, "--config", b], capsys)
    assert values["robot.name"] == "bob" and sources["robot.name"] == b
    assert values["robot.use_sim_time"] is True        # deep merge keeps a's value
    assert sources["robot.use_sim_time"] == a
    assert values["map"] == "from_a"

    a = write("a.yaml", "map: m\nplanner: {tolerance: 0.1}\n")
    values, sources = show(
        sample_launch, ["--config", a, "--planner.tolerance", "0.7"], capsys
    )
    assert values["planner.tolerance"] == 0.7 and sources["planner.tolerance"] == "cli"

    a = write("a.yaml", "map: m\nrobot: {use_sim_time: true}\n")
    values, _ = show(sample_launch, ["--config", a, "--no-robot.use_sim_time"], capsys)
    assert values["robot.use_sim_time"] is False
    values, _ = show(sample_launch, ["--map", "m", "--robot.use_sim_time"], capsys)
    assert values["robot.use_sim_time"] is True

    from ros2_typed_launch import config

    share = tmp_path / "share"
    (share / "config").mkdir(parents=True)
    (share / "config" / "c.yaml").write_text("launch_args:\n  map: pkg_map\n")
    monkeypatch.setattr(config, "_package_share_dir", lambda pkg: str(share))
    values, sources = show(sample_launch, ["--config", "mypkg:config/c.yaml"], capsys)
    assert values["map"] == "pkg_map"
    assert sources["map"] == "mypkg:config/c.yaml"     # shown as typed


# -------------------------------------------------------------- collections
def test_collection_flags(sample_launch, write, capsys):
    a = write("a.yaml", "map: m\ntags: [a, b]\n")
    b = write("b.yaml", "tags: [c]\n")
    values, _ = show(sample_launch, ["--config", a, "--config", b], capsys)
    assert values["tags"] == ["c"]                     # later file replaces
    values, _ = show(
        sample_launch, ["--config", a, "--tags", "x", "--tags", "y"], capsys
    )
    assert values["tags"] == ["x", "y"]                # CLI replaces files

    a = write("a.yaml", "map: m\nparams: {p: 1, q: 2}\nremap: {/a: /b}\n")
    values, _ = show(
        sample_launch,
        ["--config", a, "--params", "q=3", "--params", "r=true", "--remap", "/c=/d"],
        capsys,
    )
    assert values["params"] == {"p": 1, "q": 3, "r": True}  # untyped -> YAML inference
    assert values["remap"] == {"/a": "/b", "/c": "/d"}      # typed str stays a string

    values, _ = show(sample_launch, ["--map", "m", "--remap", "/a=/b=c"], capsys)
    assert values["remap"] == {"/a": "/b=c"}

    with pytest.raises(SystemExit) as exc:
        cli.main([sample_launch, "--map", "m", "--params", "novalue"])
    assert exc.value.code == 2
    assert "expected KEY=VALUE" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        cli.main([sample_launch, "--map", "m", "--robots", "x"])
    capsys.readouterr()
    a = write("a.yaml", "map: m\nrobots:\n  - {name: r1}\n  - {name: r2}\n")
    values, _ = show(sample_launch, ["--config", a], capsys)
    assert [r["name"] for r in values["robots"]] == ["r1", "r2"]


# ------------------------------------------------------------------- errors
def test_bad_values_are_reported_before_anything_starts(
    sample_launch, capsys, tmp_path
):
    code, _, err = run([sample_launch, "--show-config"], capsys)
    assert code == 2 and "--map: required" in err and "--config" in err

    code, _, err = run(
        [sample_launch, "--map", "m", "--planner.max_iterations", "999999",
         "--planner.tolerance", "-1"],
        capsys,
    )
    assert code == 2
    assert "--planner.max_iterations" in err and "(got '999999')" in err
    assert "--planner.tolerance" in err

    with pytest.raises(SystemExit) as exc:
        cli.main([sample_launch, "--map", "m", "--planner.algorithm", "bogus"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc:
        cli.main([sample_launch, "--map", "m", "--planer.tolerance", "1"])
    assert exc.value.code == 2

    code, _, err = run([sample_launch, "--config", "./nope.yaml", "--show-config"], capsys)
    assert code == 2 and "cannot read config file" in err

    path = tmp_path / "c_launch.py"
    path.write_text(
        "from pydantic import BaseModel\n"
        "from ros2_typed_launch import launch\n"
        "class C(BaseModel):\n    config: str = 'x'\n"
        "@launch(config=C)\ndef c(config):\n    return []\n"
        "generate_launch_description = c.generate_launch_description\n"
    )
    code, _, err = run([str(path), "--show-config"], capsys)
    assert code == 2 and "collides with tlaunch's own option --config" in err


def test_launch_args_is_checked_against_the_schema_but_node_sections_are_not(
    sample_launch, write, capsys
):
    """The two lanes have different contracts, on purpose: `launch_args` is this
    launch's interface, so an unknown or mistyped key there is an error; node
    parameters/remappings are a node's business, so anything goes."""
    good = write(
        "good.yaml",
        "launch_args: {map: m}\n"
        "/**:\n"
        "  ros__parameters: {totally_made_up: 1, nested: {a: [1, 2]}}\n"
        "  ros__remappings: {whatever: /else}\n",
        raw=True,
    )
    code, _, err = run([sample_launch, "--config", good, "--show-config"], capsys)
    assert code == 0 and err == ""          # the untyped lanes are never checked

    for text, expected in [
        ("map: m\nnot_a_field: 1\n", "not_a_field: unknown key"),
        ("map: m\nplanner: {bogus: 1}\n", "planner.bogus: unknown key"),
        ("map: m\nplanner: 5\n", "planner: expected a mapping"),
        ("map: m\nplanner: {tolerance: nope}\n", "not a valid float"),
    ]:
        path = write("bad.yaml", text)
        code, _, err = run([sample_launch, "--config", path, "--show-config"], capsys)
        assert code == 2 and expected in err

    a = write("a.yaml", "map: m\nplannr: {tolerance: 1}\n")
    code, _, err = run([sample_launch, "--config", a, "--show-config"], capsys)
    assert code == 2
    assert "plannr" in err and "did you mean 'planner'" in err and a in err

    path = write("c.yaml", "map: m\n", raw=True)
    code, _, err = run([sample_launch, "--config", path, "--show-config"], capsys)
    assert code == 2 and "go under 'launch_args:'" in err

    path = write("c.yaml", "map: m\ntags: ['$(var map)']\n")
    code, _, err = run([sample_launch, "--config", path, "--show-config"], capsys)
    assert code == 2 and "cannot be built from another one" in err


def test_substitutions_and_var(sample_launch, write, capsys, monkeypatch):
    # Both answer "is this config for this launch?", so they are checked against
    # the schema in the same pass and reported together, before any expansion.
    path = write(
        "c.yaml",
        "launch_args: {map: m, not_a_field: 1}\n"
        "/$(var nope)/x:\n"
        "  ros__parameters: {a: $(var robo.name)}\n",
        raw=True,
    )
    code, _, err = run([sample_launch, "--config", path, "--show-config"], capsys)
    assert code == 2
    assert "not_a_field: unknown key" in err
    assert "$(var nope): not an argument of this launch" in err
    assert "$(var robo.name): not an argument of this launch" in err
    assert "did you mean 'robot.name'" in err
    assert "written for another launch" in err  # both kinds at once says why
    assert "cannot expand" not in err          # nothing was expanded

    code, _, err = run(
        [sample_launch, "--map", "m", "--ros-param", "t=$(var nope)", "--show-config"],
        capsys,
    )
    assert code == 2
    assert "on the command line:" in err
    assert "$(var nope): not an argument of this launch" in err

    # Which key belongs to this launch is the more fundamental question, so it is
    # answered first -- even when the bad substitution is in another file.
    pytest.importorskip("launch")
    bad_key = write("a.yaml", "map: m\nnot_a_field: 1\n")
    bad_subst = write(
        "b.yaml",
        "launch_args: {map: m}\n/**:\n  ros__parameters: {x: $(var nope)}\n",
        raw=True,
    )
    code, _, err = run(
        [sample_launch, "--config", bad_subst, "--config", bad_key, "--show-config"],
        capsys,
    )
    assert code == 2
    assert "not_a_field: unknown key" in err and "cannot expand" not in err

    monkeypatch.setenv("TLAUNCH_TEST_MAP", "office.yaml")
    path = write("c.yaml", "map: maps/$(env TLAUNCH_TEST_MAP)\n")
    values, _ = show(sample_launch, ["--config", path], capsys)
    assert values["map"] == "maps/office.yaml"

    # The point of the two-stage expansion: node config can be written once and
    # follow whatever the launch argument ends up being, including from the CLI.
    pytest.importorskip("launch_ros")
    path = write(
        "c.yaml",
        "launch_args: {map: m, robot: {name: rover}}\n"
        "/$(var robot.name)/driver:\n"
        "  ros__parameters: {tag: $(var robot.name)-1}\n"
        "  ros__remappings: {cmd: /$(var robot.name)/cmd}\n",
        raw=True,
    )
    _, out, _ = run([sample_launch, "--config", path, "--show-config"], capsys)
    assert '"/rover/driver":' in out
    assert 'tag: "rover-1"' in out
    assert '"cmd": "/rover/cmd"' in out

    # ... and the command line is still the last word, pattern included.
    _, out, _ = run(
        [sample_launch, "--config", path, "--robot.name", "buggy", "--show-config"],
        capsys,
    )
    assert '"/buggy/driver":' in out and '"cmd": "/buggy/cmd"' in out


# ------------------------------------------------------- ros params/remaps
def test_ros_params_and_remaps(sample_launch, write, capsys):
    # The pattern is optional, defaulting to every node...
    [section] = cli.parse_ros_cli(["use_sim_time=true"], "param")
    assert section.pattern == "/**" and section.params == {"use_sim_time": True}
    # ...values are parsed as YAML, so types survive...
    [section] = cli.parse_ros_cli(["/ns/planner:tolerance=0.3"], "param")
    assert section.pattern == "/ns/planner" and section.params == {"tolerance": 0.3}
    # ...and a remapping takes the same shape.
    [section] = cli.parse_ros_cli(["/**/talker:/chatter=/out"], "remap")
    assert section.pattern == "/**/talker" and section.remaps == {"/chatter": "/out"}

    with pytest.raises(cli.TlaunchError, match="expected"):
        cli.parse_ros_cli(["no_equals_sign"], "param")

    path = write(
        "c.yaml",
        """
        launch_args:
          map: m
        /**:
          ros__parameters: {use_sim_time: true}
        """,
        raw=True,
    )
    code, out, err = run([sample_launch, "--config", path, "--show-config"], capsys)
    assert code == 0, err
    # The output is a config file: the two lanes stay under their own keys, and
    # it can be fed straight back in.
    assert "launch_args:" in out and 'map: "m"' in out
    assert '"/**":' in out and "ros__parameters:" in out
    assert "use_sim_time: true" in out
    # The sample launch builds no node(), so the section reports NO MATCH rather
    # than hiding it -- this is where that shows up, not in a warning.
    assert "-> NO MATCH" in out
    assert yaml.safe_load(out)["/**"]["ros__parameters"] == {"use_sim_time": True}

    argv = [sample_launch, "--map", "m", "--ros-param", "/nope/x:a=1", "--show-config"]

    # Quiet by default: it changes nothing about the launch, and config aimed at
    # a stock Node (or a node behind a disabled toggle) legitimately matches
    # nothing. --show-config still reports it per section, below.
    code, out, err = run(argv, capsys)
    assert code == 0 and "matched no node" not in err
    assert "/nope/x" in out and "NO MATCH" in out

    code, _, err = run(argv + ["-d"], capsys)
    assert code == 0 and "matched no node" in err and "/nope/x" in err

    code, _, err = run(
        [sample_launch, "--map", "m", "--strict", "--ros-param", "a=1", "--show-config"],
        capsys,
    )
    assert code == 2
    assert "--strict" in err and "untyped node sections" in err


# ---------------------------------------------------- effective config output
def test_effective_config(sample_launch, write, capsys, tmp_path):
    path = write(
        "c.yaml",
        """
        launch_args:
          map: m
          tags: [a, b]
          params: {p: 1}
        /**:
          ros__parameters: {use_sim_time: true}
          ros__remappings: {/scan: /lidar/scan}
        """,
        raw=True,
    )
    _, first, _ = run([sample_launch, "--config", path, "--show-config"], capsys)

    # Feed the output back in: same values out, so the printed config really is
    # the config (comments change, since the source is now this file).
    round_trip = write("second.yaml", first, raw=True)
    _, second, _ = run([sample_launch, "--config", round_trip, "--show-config"], capsys)

    def data(text):
        return yaml.safe_load(text)

    assert data(first) == data(second)
    # The two untyped lanes stay separate, never merged into one blob.
    assert data(first)["/**"]["ros__parameters"] == {"use_sim_time": True}
    assert data(first)["/**"]["ros__remappings"] == {"/scan": "/lidar/scan"}

    # A config file and a --ros-param can both target /**. Emitting two blocks
    # would mean duplicate YAML keys; they are merged, each value keeping its own
    # source, and the later one (the CLI) wins.
    path = write(
        "c.yaml",
        """
        launch_args: {map: m}
        /**:
          ros__parameters: {use_sim_time: false, other: 1}
        """,
        raw=True,
    )
    _, out, _ = run(
        [sample_launch, "--config", path, "--ros-param", "use_sim_time=true",
         "--show-config"],
        capsys,
    )
    assert out.count('"/**":') == 1
    params = yaml.safe_load(out)["/**"]["ros__parameters"]
    assert params == {"use_sim_time": True, "other": 1}
    # Provenance is per value, so the two sources are both visible.
    assert "use_sim_time: true" in out and "# cli" in out
    assert re.search(r"other: 1\s+#\s+\S+c\.yaml", out)

    text = "launch_args:\n  map: m\n"
    path = cli.save_effective(text, "pkg some_launch.py")
    assert Path(path).read_text() == text
    # Named after the launch, so two runs of different launches don't collide.
    assert "pkg_some_launch_py" in path and path.endswith(".yaml")


