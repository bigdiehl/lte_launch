"""The installed examples (examples/), each through the real pipeline without
launching anything: target lookup, config files, validation, building."""

import pytest

pytest.importorskip("launch_ros")
pytest.importorskip("ros2_typed_launch.examples")

from ros2_typed_launch import cli  # noqa: E402

PKG = "ros2_typed_launch"
CONFIG = f"{PKG}:examples/config"

# Every example, and the config files it is documented with.
EXAMPLES = {
    "turtle_demo_launch.py": [f"{CONFIG}/blue_turtle.yaml", f"{CONFIG}/quiet.yaml"],
    "turtle_teleop_launch.py": [],
    "localization_launch.py": [],
    "turtle_showcase_launch.py": [],
    "turtle_params_launch.py": [f"{CONFIG}/params_demo.yaml"],
    "stock_include_launch.py": [],
    "turtle_bringup_script.py": [],
    "turtle_checks_launch.py": [],
    "flaky_demo_launch.py": [],
    "tmux_demo_launch.py": [],
}


def test_every_example_configures_and_builds(capsys):
    from launch import LaunchDescription

    from ros2_typed_launch import loader
    from ros2_typed_launch.core import TypedLaunch

    for name, configs in EXAMPLES.items():
        argv = [PKG, name, *(a for c in configs for a in ("--config", c)), "--show-config"]
        assert cli.main(argv) == 0, (name, capsys.readouterr().err)
        out = capsys.readouterr().out
        assert out.startswith(f"# Effective configuration for {PKG} {name}"), name

        typed = loader.resolve(PKG, name)
        if isinstance(typed, TypedLaunch):
            # What stock `ros2 launch` gets (the share/ stubs import this).
            assert isinstance(typed.generate_launch_description(), LaunchDescription)


def test_the_example_list_is_complete():
    from importlib.resources import files

    found = {p.name for p in files(f"{PKG}.examples").iterdir()
             if p.name.endswith(("_launch.py", "_script.py"))}
    assert found == set(EXAMPLES)
