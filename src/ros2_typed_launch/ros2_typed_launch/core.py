from __future__ import annotations

from typing import Any, Callable


class TypedLaunch:
    """A launch function together with the config model it takes."""

    def __init__(self, config_type: type, factory: Callable):
        self.config_type = config_type
        self.factory = factory

    @property
    def name(self) -> str:
        return self.factory.__name__

    def build(self, config: Any) -> list:
        """Run the launch function: config object -> list of launch actions."""
        return list(self.factory(config))


def launch(*, config: type):
    """Decorator turning a typed Python function into a launch.

    The function receives the validated config object and returns a list of
    launch actions. Run the file with ``tlaunch``.
    """

    def decorator(factory: Callable) -> TypedLaunch:
        typed = TypedLaunch(config_type=config, factory=factory)

        def generate_launch_description():
            raise RuntimeError(
                f"'{typed.name}' is a typed launch and cannot run under "
                "'ros2 launch'; run it with 'tlaunch'"
            )

        # tlaunch finds the launch in a file through the file's exported
        # generate_launch_description, exactly as ros2 launch does.
        generate_launch_description.typed_launch = typed
        typed.generate_launch_description = generate_launch_description
        return typed

    return decorator
