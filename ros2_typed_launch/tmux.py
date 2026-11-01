"""Putting a launch's processes into tmux panes, one pane each (Q28).

A launch merges every process's output into one stream, so a dozen nodes
interleave in one terminal and reading any one of them means grepping. tmux
already solves that -- a pane per process, side by side, each with its own
scrollback and its own terminal.

The problem is ownership. Launch owns the processes it starts: it knows their
pids, signals them to shut down and reads their exit codes. A command handed to
tmux is owned by the tmux *server* instead, and launch would see nothing of it.
So the command launch starts is not the real one: it is `tlaunch-pane`, a small
script that runs the real command in a pane and then reports the pane's exit
status as its own. Launch still has a process to hold on to, and that process
tells the truth about what happened.

This module is the other half: it makes the session, makes the panes and
rewrites the commands. It hooks ``Executable.prepare``, which is where launch
turns an action's substitutions into the argument list it will actually run --
the one place every process passes through, so a node, a plain
``ExecuteProcess`` and anything inside an *included* launch file (typed or
stock) are all covered by the same few lines.

Decided 2026-10-02:

* opt-in per run (``--tmux``), and per process (``--tmux-only GLOB``). A pane
  is a window-manager decision, not a property of the launch, so this is a
  command-line concern and launch files say nothing about it;
* **grouping is by environment variable** (``TLAUNCH_TMUX_WINDOW``): processes
  that carry the same value share a window. A scoped ``GroupAction`` setting it
  therefore puts an included launch's processes in their own window, using only
  stock launch constructs and no new API (see `window` in actions.py);
* **nothing is ever killed to make room.** An existing session is joined, not
  replaced, and windows get a unique name, because the session may well be the
  one the user is sitting in;
* a pane whose process **failed** stays open to be read (``remain-on-exit``);
  one that finished cleanly is closed when the space is needed.

A process in a pane writes to the pane's terminal, not to launch, so
tlaunch-pane has ``lte-run --tee`` copy the pane's output back and prints it as
its own: launch's log files, the death report and ``Process.wait_output()``
see it as before. What changes is the *screen*: the pane is where it is read,
so `off_screen` drops "screen" from the process's output setting and this
terminal does not show it twice. stdout and stderr arrive as one stream (a
terminal has only one), as stdout.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess

#: The script launch runs in place of the real command (scripts/tlaunch-pane).
PANE_SCRIPT = "tlaunch-pane"

#: What runs the command inside its pane and reports its exact exit status
#: (libtmux_easy's wrapper: ``uv tool install libtmux_easy``).
RUNNER = "lte-run"

#: The pane options tlaunch-pane records, as ``@tlaunch_<field>``
#: (scripts/tlaunch-pane, OPTION). Read back with `pane_rows`.
OPTION_PREFIX = "@tlaunch_"
FIELDS = ("name", "window", "command", "shim", "target", "status", "stopped")

#: The ROS log viewer --tmux-logs opens (ros2-term); TLAUNCH_LOG_VIEWER overrides.
LOG_VIEWER = "ros2-log-view"
LOG_VIEWER_ENV = "TLAUNCH_LOG_VIEWER"

#: Session option holding the log window's id.
LOGS_OPTION = "@tlaunch_logs"

#: Prefix keys borrowed for the run (libtmux_easy's lte-key): to the log window
#: and back, and back to the dashboard. In any other session they keep their
#: usual binding, and they are given back at the end, so ~/.tmux.conf needs
#: nothing. The environment variables override them; empty borrows nothing.
KEY_TOOL = "lte-key"
LOGS_KEY, LOGS_KEY_ENV = "v", "TLAUNCH_LOGS_KEY"
DASH_KEY, DASH_KEY_ENV = "g", "TLAUNCH_DASH_KEY"
LOGS_KEY_COMMAND = (
    f"if-shell -F '#{{==:#{{window_id}},#{{{LOGS_OPTION}}}}}' {{ last-window }} "
    f"{{ run-shell \"tmux -S '#{{socket_path}}' select-window -t '#{{{LOGS_OPTION}}}'\" }}"
)

#: A process carrying this in its environment goes in the window it names.
#: Set it with `ros2_typed_launch.window(...)`, or a stock SetEnvironmentVariable.
WINDOW_ENV = "TLAUNCH_TMUX_WINDOW"

#: Where a process goes when nothing says otherwise: one window for the launch.
MAIN = ""

LAYOUT = "tiled"


class TmuxError(Exception):
    """tmux is missing, or refused something we cannot work around."""


def need(tool: str, hint: str) -> str:
    """The path of a tool on PATH, or a TmuxError saying where to get it."""
    path = shutil.which(tool)
    if path is None:
        raise TmuxError(f"{tool} is not on PATH -- {hint}")
    return path


def tmux(*args: str, check: bool = True) -> str:
    """Run one tmux command and return its stdout."""
    result = subprocess.run(
        ("tmux",) + args, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise TmuxError(f"tmux {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def log():
    """tlaunch's launch logger."""
    import launch.logging

    return launch.logging.get_logger("tlaunch")


def env_args() -> list[str]:
    """This process's environment as tmux ``-e NAME=VALUE`` arguments: a
    pane's process is forked by the tmux server and would get its own."""
    return [part for name, value in os.environ.items()
            for part in ("-e", f"{name}={value}")]


def session_exists(name: str) -> bool:
    return subprocess.run(("tmux", "has-session", "-t", f"={name}"),
                          capture_output=True).returncode == 0


def pane_rows(session: str, extra: tuple[str, ...] = ()
              ) -> list[tuple[str, str, dict[str, str]]]:
    """(pane id, pane_dead, values) for every pane in the session. 'values'
    holds the `FIELDS` options tlaunch-pane recorded, plus the 'extra' tmux
    formats (``pane_dead_status``) under their own names."""
    keys = FIELDS + extra
    fmt = "\t".join(["#{pane_id}", "#{pane_dead}"]
                    + [f"#{{{OPTION_PREFIX}{f}}}" for f in FIELDS]
                    + [f"#{{{f}}}" for f in extra])
    out = tmux("list-panes", "-s", "-t", f"={session}:", "-F", fmt, check=False)
    rows = []
    for line in out.split("\n"):
        # Padded: tmux() strips its output, and with it the empty fields that
        # end the last line.
        parts = line.split("\t") + [""] * (2 + len(keys))
        if parts[0]:
            rows.append((parts[0], parts[1], dict(zip(keys, parts[2:]))))
    return rows


def current_session() -> str | None:
    """The session tlaunch is itself running in, if it was started inside tmux.

    Joining it is friendlier than opening another: the panes appear where the
    user is already looking, one window away.
    """
    pane = os.environ.get("TMUX_PANE")
    if not os.environ.get("TMUX") or not pane:
        return None
    return tmux("display-message", "-p", "-t", pane, "#{session_name}",
                check=False) or None


def selected(patterns, name: str, node: str | None) -> bool:
    """Does this process go in a pane?

    With no patterns, every process does (that is plain ``--tmux``). A pattern
    is matched against the process name, that name without launch's ``-N``
    counter, and the node's fully qualified name, so ``--tmux-only turtlesim*``
    and ``--tmux-only '/demo/*'`` both pick what you would expect.
    """
    if not patterns:
        return True
    names = [name, name.rsplit("-", 1)[0], node]
    return any(
        fnmatch.fnmatch(candidate, pattern)
        for pattern in patterns
        for candidate in names
        if candidate
    )


class Panes:
    """The tmux session holding a run's panes, and the windows inside it.

    Created lazily: a run where nothing ends up in a pane leaves no session
    behind. Every method is called from launch's own thread (out of
    ``Executable.prepare``), so there is no locking here and no race to create
    the same window twice.
    """

    def __init__(self, label: str, *, session: str | None = None,
                 layout: str = LAYOUT, dash: bool = False, logs: bool = False):
        self.label = label or "launch"
        self.want_dash, self.want_logs = dash, logs
        self.dash = None                       # dashboard.Dashboard, once open
        self.extras: dict[str, str] = {}       # "dash"/"logs" -> window id
        self.viewer: list[str] = []
        self.logs_key = os.environ.get(LOGS_KEY_ENV, LOGS_KEY) if logs else ""
        self.dash_key = os.environ.get(DASH_KEY_ENV, DASH_KEY)
        self.borrowed = ""                     # the logs key, while borrowed
        self.wanted = session
        self.layout = layout
        self.script: str | None = None
        self.runner: str | None = None
        self.session: str | None = None
        self.joined = False
        self.windows: dict[str, str] = {}      # grouping key -> tmux window
        self.spare: dict[str, str] = {}        # window -> its unused first pane

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        """Check that this can work at all, before any process is started."""
        lte = "it comes from libtmux_easy: uv tool install --editable " \
              "'~/projects/libtmux_easy[dash]'"
        need("tmux", "install tmux")
        self.script = need(PANE_SCRIPT, "source install/setup.bash, or rebuild "
                           "the workspace if this is a fresh checkout")
        self.runner = need(RUNNER, lte)
        if self.want_dash:
            from .dashboard import DASH

            need(DASH, lte)
        if self.want_logs:
            import shlex

            self.viewer = shlex.split(os.environ.get(LOG_VIEWER_ENV, LOG_VIEWER))
            need(self.viewer[0] if self.viewer else LOG_VIEWER,
                 f"it comes from ros2-term (pipx), or set {LOG_VIEWER_ENV}")
        if self.logs_key:
            need(KEY_TOOL, f"{lte}; or set {LOGS_KEY_ENV}= to go without the key")
        here = current_session()
        if self.wanted:
            self.session = self.wanted
            self.joined = session_exists(self.wanted)
        elif here:
            self.session, self.joined = here, True
        else:
            self.session, self.joined = self._unused_session(), False

    def open_extras(self) -> None:
        """The dashboard and log windows, if asked for. Before anything runs,
        so the home log has the run's output from its first line."""
        if not (self.want_dash or self.want_logs):
            return
        boot = None
        if not self._session_made():
            # A session cannot be made without a window: make one to be
            # closed once the real ones exist.
            boot = tmux("new-session", "-d", "-s", self.session, "-n", "tlaunch-boot",
                        "-P", "-F", "#{window_id}")
        if self.want_logs:
            self.extras["logs"] = tmux(
                "new-window", "-d", "-t", f"{self.session}:", "-n", "logs",
                "-P", "-F", "#{window_id}", "-c", os.getcwd(), *env_args(),
                *self.viewer)
            tmux("set-option", "-q", "-t", self.session, LOGS_OPTION, self.extras["logs"])
            if self.logs_key:
                self._borrow_key()
        if self.want_dash:
            from .dashboard import Dashboard

            self.dash = Dashboard(self.session, key=self.dash_key)
            self.extras["dash"] = self.dash.open()
            if self.joined:
                # tlaunch was started in this session: show the dashboard
                # where the user is, its home being this very output.
                tmux("select-window", "-t", self.extras["dash"], check=False)
        if boot:
            tmux("kill-window", "-t", boot, check=False)

    def _borrow_key(self) -> None:
        result = subprocess.run(
            [KEY_TOOL, "borrow", self.logs_key, LOGS_OPTION,
             "--message", "no tlaunch log window in this session",
             "--", LOGS_KEY_COMMAND],
            capture_output=True, text=True)
        if result.returncode == 0:
            self.borrowed = self.logs_key
        else:
            log().warning(f"prefix {self.logs_key} not bound: {result.stderr.strip()}")

    def _return_key(self) -> None:
        if self.borrowed:
            subprocess.run([KEY_TOOL, "return", self.borrowed], capture_output=True)
            self.borrowed = ""

    def attach(self, service, monitor) -> None:
        """The run in progress, for the dashboard's stop and shutdown."""
        if self.dash is not None:
            self.dash.attach(service, monitor)

    def _unused_session(self) -> str:
        """A session name of our own that is not taken yet."""
        base = f"tlaunch-{self.label}"
        for name in self._candidates(base):
            if not session_exists(name):
                return name
        raise TmuxError(f"no free session name based on {base!r}")

    @staticmethod
    def _candidates(base: str):
        yield base
        for suffix in range(2, 100):
            yield f"{base}-{suffix}"

    # ---------------------------------------------------------------- panes
    def wrap(self, cmd: list[str], key: str, title: str) -> list[str]:
        """The command launch should run instead of `cmd`.

        The pane itself is made by `tlaunch-pane`, not here, because the tmux
        server forks the pane's process and would give it the server's
        environment. Only that script has the environment launch computed for
        this process. So what this decides is the *window*.
        """
        window = self._window(key)
        retire = self.spare.pop(window, "")
        return [
            self.script,
            "--window", self._target(window),
            "--runner", self.runner,
            "--title", title,
            "--layout", self.layout,
            *(("--retire", retire) if retire else ()),
            "--", *cmd,
        ]

    def holds(self, cmd: list[str]) -> bool:
        """Is this command already one of ours? (Guards a second patch.)"""
        return bool(cmd) and cmd[0] == self.script

    # --------------------------------------------------------------- windows
    def _target(self, window: str) -> str:
        return f"{self.session}:{window}"

    def _window(self, key: str) -> str:
        """The tmux window for a grouping key, made the first time it is asked
        for."""
        if key in self.windows:
            return self.windows[key]
        name = self._unused_window(key or self.label)
        if self.session is not None and not self._session_made():
            tmux("new-session", "-d", "-s", self.session, "-n", name)
        else:
            tmux("new-window", "-d", "-t", f"{self.session}:", "-n", name)
        # On the *window*, and before anything runs in it: a pane that dies
        # keeps its exit status and its last output only if this is already
        # set, and setting it per pane afterwards is a race a fast-failing
        # process wins.
        tmux("set-option", "-w", "-t", self._target(name), "remain-on-exit", "on")
        self.windows[key] = name
        # The window came with a pane running a shell, which no process will
        # ever use: the shim splits off its own. Remember it, to be closed once
        # there is something real beside it (a window with no panes is gone).
        panes = tmux("list-panes", "-t", self._target(name), "-F", "#{pane_id}",
                     check=False).split("\n")
        if panes and panes[0]:
            self.spare[name] = panes[0]
        return name

    def _session_made(self) -> bool:
        return self.joined or bool(self.windows) or bool(self.extras)

    def _unused_window(self, base: str) -> str:
        """A window name not already in the session -- which may be one the
        user is working in, and may hold a previous run's windows."""
        if not self._session_made():
            return base
        taken = set(
            tmux("list-windows", "-t", f"{self.session}:", "-F", "#{window_name}",
                 check=False).split("\n")
        )
        for name in self._candidates(base):
            if name not in taken:
                return name
        raise TmuxError(f"no free window name based on {base!r}")

    # ---------------------------------------------------------------- report
    def attached(self) -> bool:
        """Is anyone looking at this session?"""
        return tmux("list-sessions", "-F", "#{session_attached}",
                    "-f", f"#{{==:#{{session_name}},{self.session}}}",
                    check=False).startswith("1")

    def failures(self) -> int:
        """Dead panes of ours whose process did not finish cleanly.

        A pane that exited 0 is a job done; one with any other status, or that
        was killed by a signal, is something to go and read -- unless it was
        stopped on purpose (launch's shutdown, or the dashboard's stop). The
        status is the one tlaunch-pane recorded on the pane; tmux's own is
        only the fallback, since tmux now and then loses it.
        """
        return sum(
            1 for _, dead, v in pane_rows(self.session, ("pane_dead_status",))
            if dead == "1" and v["name"] and not v["stopped"]
            and (v["status"] or v["pane_dead_status"]) != "0"
        )

    def finish(self) -> str:
        """Tidy up after the run, and say where to look. Returns one line.

        A session of our own that nobody is watching and nothing failed in is
        closed: it would otherwise pile up, one per run. If something *did*
        fail, the panes are what is left of its output, so they stay -- and so
        does the session, with the news of how to reach it.
        """
        if not self.windows and not self.extras:
            return ""
        where = ", ".join(sorted(self.windows.values()) + sorted(self.extras))
        self._return_key()
        failed = self.failures()
        watched = not self.joined and self.attached()
        if self.dash is not None:
            # Its status file and home log go only with the window.
            self.dash.close(keep=bool(failed) or watched)
        if failed:
            return (
                f"{failed} failed pane(s) kept in tmux session '{self.session}' "
                f"({where}) -- read them with: tmux attach -t {self.session}"
            )
        if self.joined:
            # Someone else's session: close the windows we added to it, and
            # nothing else, whether or not anyone is watching.
            from .dashboard import SESSION_OPTIONS

            targets = [self._target(w) for w in self.windows.values()]
            for window in targets + list(self.extras.values()):
                tmux("kill-window", "-t", window, check=False)
            for option in (LOGS_OPTION, *SESSION_OPTIONS):
                tmux("set-option", "-uq", "-t", self.session, option, check=False)
            return f"tmux windows closed ({where}); nothing failed in them"
        if watched:
            return f"panes in tmux session '{self.session}': {where}"
        tmux("kill-session", "-t", f"={self.session}", check=False)
        return f"tmux session '{self.session}' closed (nothing failed in it)"


def off_screen(action, context) -> None:
    """Take "screen" out of a process's ``output`` setting, keeping its log
    files. Called before launch reads the setting (``execute`` sets up the
    output loggers after ``prepare``)."""
    from launch.logging import _normalize_output_configuration
    from launch.utilities import perform_substitutions

    output = action._ExecuteLocal__output
    if not isinstance(output, dict):
        output = perform_substitutions(context, output)
    config = _normalize_output_configuration(output)
    for destinations in config.values():
        destinations.discard("screen")
    if not any(config.values()):
        config["both"] = {"log"}
    action._ExecuteLocal__output = config


def patch(panes: Panes, only=()) -> callable:
    """Send matching processes to tmux panes. Returns an undo function.

    ``Executable.prepare`` is where launch resolves an action's substitutions
    into the argument list it is about to run, so this runs *after* the
    original and replaces that list. Hooking here rather than walking the
    launch description is what covers processes launch creates on its own --
    from a timer, a condition or an included launch file -- since there is no
    description to walk until the moment they start.
    """
    from launch.descriptions import Executable

    from .monitor import node_name_of

    original = Executable.prepare

    def prepare(description, context, action):
        original(description, context, action)
        cmd = description.final_cmd
        if not cmd or panes.holds(cmd):
            return
        name = description.final_name or cmd[0]
        node = node_name_of(action)
        if not selected(only, name, node):
            return
        key = (description.final_env or {}).get(WINDOW_ENV, MAIN)
        try:
            wrapped = panes.wrap(cmd, key, node or name)
        except TmuxError as exc:
            # A pane is a convenience; losing one is not worth losing the run.
            log().warning(f"{name}: no tmux window ({exc}); its output stays in this log")
            return
        # final_cmd is read again when the process is actually started, so
        # replacing it here is all it takes.
        description._Executable__final_cmd = wrapped
        off_screen(action, context)

    global _active
    Executable.prepare = prepare
    _active = panes

    def undo():
        global _active
        Executable.prepare = original
        _active = None

    return undo


#: The Panes of the run in progress, if it has any (set by patch).
_active: Panes | None = None


def attach_run(service, monitor) -> None:
    """Tell the run's panes which launch service and monitor are running, for
    the dashboard's commands. A no-op without --tmux. Called by run_launch and
    run_session, the two places a service is made."""
    if _active is not None:
        _active.attach(service, monitor)
