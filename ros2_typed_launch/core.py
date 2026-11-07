from __future__ import annotations

from typing import Any, Callable

from . import ros_config as rc


class TypedTarget:
    """What ``tlaunch`` can run: a function plus the config model it takes."""

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
    """A launch whose body starts processes as it runs (``engine.py``).

    Besides ``tlaunch``, lte_launch_test runs it three ways:

    * as one process: ``procs.start("sys:up", "tlaunch pkg f_launch.py", ready=r"\\[tlaunch\\]: up")``
    * ``procs.build(target.build, **fields)`` or ``target.start(procs, **fields)``:
      every process of the body is a process of the test's group
    * ``for name, cmd in target.panes(**fields): procs.start(name, cmd)``
    """

    def start(self, procs, config: Any = None, /, **fields):
        """Run the body on ``procs`` (a libtmux_easy ``Processes``): each
        process it starts is one of the group's, with its own health, log and
        restart. Returns once the body has; the group's owner stops them
        (``procs.close()``). -> the ``engine.Run``."""
        from . import engine

        config = engine.make_config(self.config_type, config, fields)
        run = engine.Run(procs, label=self.name)
        try:
            with engine.active(run):
                self.factory(config)
        finally:
            run.detach()  # from here on the group's owner watches health
        return run

    def build(self, session, config: Any = None, /, **fields):
        """``procs.build(target.build, **fields)``: as ``start()``, on the
        group behind a libtmux_easy build session."""
        return self.start(session.processes, config, **fields)

    def panes(self, config: Any = None, /, **fields) -> list[tuple[str, str]]:
        """``[("window:name", command), ...]``: a dry run, nothing starts."""
        from . import engine

        config = engine.make_config(self.config_type, config, fields)
        return [(p.name, p.command) for p in self.plan(config)]

    def plan(self, config: Any, ros: rc.RosConfig | None = None) -> list:
        """Dry-run the body with ``config``; -> the processes it would start."""
        from . import engine

        run = engine.Run(None, label=self.name, ros=ros)
        with engine.active(run):
            self.factory(config)
        return run.plan


def launch(*, config: type):
    """Decorator: a typed launch. The function takes the validated config and
    starts what it needs (``node()``, ``include()``, ...).

    Also exports ``generate_launch_description`` from the module (unless it has
    one), which is how ``tlaunch`` finds the launch, and which lets a stock
    launch file include it (``stock.py``).
    """

    def decorator(factory: Callable) -> TypedLaunch:
        typed = TypedLaunch(config_type=config, factory=factory)

        def generate_launch_description():
            from .stock import description

            return description(typed)

        generate_launch_description.typed_target = typed
        typed.generate_launch_description = generate_launch_description
        factory.__globals__.setdefault(
            "generate_launch_description", generate_launch_description
        )
        return typed

    return decorator
