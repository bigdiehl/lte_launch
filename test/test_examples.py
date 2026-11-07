"""The installed examples (examples/), each through the real pipeline without
launching anything: target lookup, config files, validation, a dry run."""

import pytest

pytest.importorskip("launch_ros")
pytest.importorskip("lte_launch.examples")

from lte_launch import cli  # noqa: E402

PKG = "lte_launch"
CONFIG = f"{PKG}:examples/config"

# Every example, and the config files it is documented with.
EXAMPLES = {
    "turtle_demo_launch.py": [f"{CONFIG}/blue_turtle.yaml", f"{CONFIG}/quiet.yaml"],
    "turtle_teleop_launch.py": [],
    "localization_launch.py": [],
    "turtle_params_launch.py": [f"{CONFIG}/params_demo.yaml"],
    "stock_include_launch.py": [],
    "turtle_bringup_launch.py": [f"{CONFIG}/params_demo.yaml"],
    "composition_launch.py": [],
    "lifecycle_launch.py": [],
}


def test_every_example_configures_and_plans(capsys):
    from launch import LaunchDescription

    from lte_launch import loader

    for name, configs in EXAMPLES.items():
        flags = [a for c in configs for a in ("--config", c)]
        assert cli.main([PKG, name, *flags, "--show-config"]) == 0, (
            name, capsys.readouterr().err,
        )  # fmt: skip
        out = capsys.readouterr().out
        assert out.startswith(f"# Effective configuration for {PKG} {name}"), name

        # A dry run: every executable is found, every load is a command.
        assert cli.main([PKG, name, *flags, "--dry-run"]) == 0, (
            name, capsys.readouterr().err,
        )  # fmt: skip
        assert "dry run" in capsys.readouterr().out, name

        # What stock `ros2 launch` gets (the share/ stubs import this).
        typed = loader.resolve(PKG, name)
        assert isinstance(typed.generate_launch_description(), LaunchDescription)


def test_the_example_list_is_complete():
    from importlib.resources import files

    found = {
        p.name
        for p in files(f"{PKG}.examples").iterdir()
        if p.name.endswith("_launch.py")
    }
    assert found == set(EXAMPLES)
