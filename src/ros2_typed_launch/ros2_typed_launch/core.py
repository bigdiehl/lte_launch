from __future__ import annotations

from typing import Any, Callable


class TypedTarget:
    """What ``tlaunch`` can run: a function plus the config model it takes.

    Two kinds, sharing their whole command line (schema-generated flags,
    ``--config`` layering, validation): a **launch**, which returns actions for
    launch to run, and a **script**, which drives a session imperatively.
    """

    def __init__(self, config_type: type, factory: Callable):
        self.config_type = config_type
        self.factory = factory

    @property
    def name(self) -> str:
        return self.factory.__name__

    def main(self, argv: list[str] | None = None):
        """Run this target as a program -- ``python3 my_file.py [options]``.

        The same command line as ``tlaunch <package> <file>``; exits when done,
        so it is only useful under ``if __name__ == "__main__":``.
        """
        from .cli import main as cli_main

        raise SystemExit(cli_main(argv, target=self))


class TypedLaunch(TypedTarget):
    """A launch function together with the config model it takes."""

    def build(self, config: Any) -> list:
        """Run the launch function: config object -> list of launch actions."""
        return list(self.factory(config))


class TypedScript(TypedTarget):
    """A script function together with the config model it takes.

    The function takes ``(session, config)`` and runs top to bottom on its own
    thread while a launch service runs on the main one; see ``session.py``.
    """

    def run(self, session: Any, config: Any) -> Any:
        return self.factory(session, config)


def _entry_point(target: TypedTarget, kind: str) -> Callable:
    """The ``generate_launch_description`` tlaunch finds a target through.

    Stock ``ros2 launch`` looks for the same name, so it finds something and can
    be told why it will not work, rather than reporting an empty launch file.
    """

    def generate_launch_description():
        raise RuntimeError(
            f"'{target.name}' is a typed {kind} and cannot run under "
            "'ros2 launch'; run it with 'tlaunch'"
        )

    generate_launch_description.typed_target = target
    return generate_launch_description


def launch(*, config: type):
    """Decorator turning a typed Python function into a launch.

    The function receives the validated config object and returns a list of
    launch actions. Run the file with ``tlaunch``.
    """

    def decorator(factory: Callable) -> TypedLaunch:
        typed = TypedLaunch(config_type=config, factory=factory)
        typed.generate_launch_description = _entry_point(typed, "launch")
        return typed

    return decorator


def script(*, config: type):
    """Decorator turning a typed Python function into an imperative launch script.

        @script(config=BringupConfig)
        def bringup(session, config):
            stage = session.start(some_launch, SomeConfig())
            stage.wait_started()
            ...

        if __name__ == "__main__":
            bringup.main()

    The function receives a ``Session`` and the validated config object, and runs
    top to bottom: it starts things, waits for them, and stops them. Run it
    either way -- ``tlaunch <package> <file>`` or ``python3 <file>`` (that is
    what ``main()`` is for). Returning an int sets the exit code.
    """

    def decorator(factory: Callable) -> TypedScript:
        typed = TypedScript(config_type=config, factory=factory)
        typed.generate_launch_description = _entry_point(typed, "script")
        return typed

    return decorator
