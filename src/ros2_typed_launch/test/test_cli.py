import argparse
import json

import pytest

from ros2_typed_launch import cli


def run(argv, capsys):
    """-> (exit code, stdout, stderr)"""
    code = cli.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def show(launch_file, extra, capsys):
    """Run --show-config and parse `dotted = json  (source)` lines."""
    code, out, err = run([launch_file, *extra, "--show-config"], capsys)
    assert code == 0, err
    values, sources = {}, {}
    for line in out.splitlines():
        left, _, right = line.partition(" = ")
        value, _, source = right.rpartition("  (")
        values[left.strip()] = json.loads(value)
        sources[left.strip()] = source.rstrip(")")
    return values, sources


# ------------------------------------------------------------------ targets
def test_split_target(sample_launch):
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


# --------------------------------------------------------------- completion
def test_complete_launch_file(fake_package):
    # Completions are full filenames (".py" included), matching what tlaunch
    # now requires you to type.
    parsed_args = argparse.Namespace(package=fake_package)
    assert set(cli._complete_launch_file("", parsed_args)) == {
        "child_launch.py", "main_launch.py", "nested_launch.py", "not_a_launch.py",
    }


def test_complete_package_or_path(monkeypatch):
    pytest.importorskip("ament_index_python")
    import ament_index_python.packages as amt

    monkeypatch.setattr(amt, "get_packages_with_prefixes", lambda: {"my_pkg": "/x"})
    assert "my_pkg" in cli._complete_package_or_path("", argparse.Namespace())


def test_no_arguments_prints_help(capsys):
    code, out, _ = run([], capsys)
    assert code == 0 and "usage: tlaunch" in out


def test_untyped_launch_file_is_rejected(tmp_path, capsys):
    path = tmp_path / "plain_launch.py"
    path.write_text("def generate_launch_description():\n    return None\n")
    code, _, err = run([str(path), "--show-config"], capsys)
    assert code == 2 and "not a typed launch file" in err


def test_unknown_package_is_a_clean_error(capsys):
    code, _, err = run(["definitely_not_a_package", "x_launch"], capsys)
    assert code == 2 and "tlaunch: error:" in err


def test_resolves_by_package_and_module(fake_package, capsys):
    code, out, err = run([fake_package, "main_launch.py", "--show-config"], capsys)
    assert code == 0, err
    assert "n = 5" in out


# --------------------------------------------------------------- flags/help
def test_help_groups_flags_by_nested_model(sample_launch, capsys):
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


def test_schema_flag_prints_json_schema(sample_launch, capsys):
    code, out, _ = run([sample_launch, "--schema"], capsys)
    assert code == 0 and "map" in json.loads(out)["properties"]


# ---------------------------------------------------------------- layering
def test_defaults_and_outer_default_instance(sample_launch, capsys):
    values, sources = show(sample_launch, ["--map", "m"], capsys)
    assert values["planner.tolerance"] == 0.9          # Planner(tolerance=0.9)
    assert values["planner.algorithm"] == "smac"
    assert sources["planner.tolerance"] == "default"
    assert sources["map"] == "cli"


def test_later_config_files_override_earlier_ones(sample_launch, write, capsys):
    a = write("a.yaml", "map: from_a\nrobot: {name: alice, use_sim_time: true}\n")
    b = write("b.yaml", "robot: {name: bob}\n")
    values, sources = show(sample_launch, ["--config", a, "--config", b], capsys)
    assert values["robot.name"] == "bob" and sources["robot.name"] == b
    assert values["robot.use_sim_time"] is True        # deep merge keeps a's value
    assert sources["robot.use_sim_time"] == a
    assert values["map"] == "from_a"


def test_cli_overrides_every_file(sample_launch, write, capsys):
    a = write("a.yaml", "map: m\nplanner: {tolerance: 0.1}\n")
    values, sources = show(
        sample_launch, ["--config", a, "--planner.tolerance", "0.7"], capsys
    )
    assert values["planner.tolerance"] == 0.7 and sources["planner.tolerance"] == "cli"


def test_bool_flag_pair(sample_launch, write, capsys):
    a = write("a.yaml", "map: m\nrobot: {use_sim_time: true}\n")
    values, _ = show(sample_launch, ["--config", a, "--no-robot.use_sim_time"], capsys)
    assert values["robot.use_sim_time"] is False
    values, _ = show(sample_launch, ["--map", "m", "--robot.use_sim_time"], capsys)
    assert values["robot.use_sim_time"] is True


def test_package_relative_config_reference(sample_launch, tmp_path, monkeypatch, capsys):
    from ros2_typed_launch import config

    share = tmp_path / "share"
    (share / "config").mkdir(parents=True)
    (share / "config" / "c.yaml").write_text("map: pkg_map\n")
    monkeypatch.setattr(config, "_package_share_dir", lambda pkg: str(share))
    values, sources = show(sample_launch, ["--config", "mypkg:config/c.yaml"], capsys)
    assert values["map"] == "pkg_map"
    assert sources["map"] == "mypkg:config/c.yaml"     # shown as typed


# -------------------------------------------------------------- collections
def test_list_flag_repeats_and_replaces_file_lists(sample_launch, write, capsys):
    a = write("a.yaml", "map: m\ntags: [a, b]\n")
    b = write("b.yaml", "tags: [c]\n")
    values, _ = show(sample_launch, ["--config", a, "--config", b], capsys)
    assert values["tags"] == ["c"]                     # later file replaces
    values, _ = show(
        sample_launch, ["--config", a, "--tags", "x", "--tags", "y"], capsys
    )
    assert values["tags"] == ["x", "y"]                # CLI replaces files


def test_dict_flag_merges_per_key_with_files(sample_launch, write, capsys):
    a = write("a.yaml", "map: m\nparams: {p: 1, q: 2}\nremap: {/a: /b}\n")
    values, _ = show(
        sample_launch,
        ["--config", a, "--params", "q=3", "--params", "r=true", "--remap", "/c=/d"],
        capsys,
    )
    assert values["params"] == {"p": 1, "q": 3, "r": True}  # untyped -> YAML inference
    assert values["remap"] == {"/a": "/b", "/c": "/d"}      # typed str stays a string


def test_dict_value_is_split_on_first_equals(sample_launch, capsys):
    values, _ = show(sample_launch, ["--map", "m", "--remap", "/a=/b=c"], capsys)
    assert values["remap"] == {"/a": "/b=c"}


def test_dict_flag_requires_key_value(sample_launch, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([sample_launch, "--map", "m", "--params", "novalue"])
    assert exc.value.code == 2
    assert "expected KEY=VALUE" in capsys.readouterr().err


def test_collections_of_models_have_no_flag_but_load_from_yaml(
    sample_launch, write, capsys
):
    with pytest.raises(SystemExit):
        cli.main([sample_launch, "--map", "m", "--robots", "x"])
    capsys.readouterr()
    a = write("a.yaml", "map: m\nrobots:\n  - {name: r1}\n  - {name: r2}\n")
    values, _ = show(sample_launch, ["--config", a], capsys)
    assert [r["name"] for r in values["robots"]] == ["r1", "r2"]


# ------------------------------------------------------------------- errors
def test_missing_required_field_says_how_to_set_it(sample_launch, capsys):
    code, _, err = run([sample_launch, "--show-config"], capsys)
    assert code == 2 and "--map: required" in err and "--config" in err


def test_invalid_values_are_reported_by_flag_before_anything_starts(
    sample_launch, capsys
):
    code, _, err = run(
        [sample_launch, "--map", "m", "--planner.max_iterations", "999999",
         "--planner.tolerance", "-1"],
        capsys,
    )
    assert code == 2
    assert "--planner.max_iterations" in err and "(got '999999')" in err
    assert "--planner.tolerance" in err


def test_bad_choice_is_rejected_by_argparse(sample_launch, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([sample_launch, "--map", "m", "--planner.algorithm", "bogus"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_unknown_flag_is_rejected(sample_launch, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([sample_launch, "--map", "m", "--planer.tolerance", "1"])
    assert exc.value.code == 2


def test_typo_in_config_file_is_an_error_with_suggestion(sample_launch, write, capsys):
    a = write("a.yaml", "map: m\nplannr: {tolerance: 1}\n")
    code, _, err = run([sample_launch, "--config", a, "--show-config"], capsys)
    assert code == 2
    assert "plannr" in err and "did you mean 'planner'" in err and a in err


def test_missing_config_file_is_a_clean_error(sample_launch, capsys):
    code, _, err = run([sample_launch, "--config", "./nope.yaml", "--show-config"], capsys)
    assert code == 2 and "cannot read config file" in err


def test_schema_field_colliding_with_tlaunch_option(tmp_path, capsys):
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
