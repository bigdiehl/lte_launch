"""Processes in tmux panes (Q28).

Two things are worth pinning down here. The first is *which* processes go in a
pane and what the command launch ends up running looks like -- pure decisions,
tested directly. The second is the contract the whole idea rests on: that a
process in a pane still reports its real exit code to launch, and that its
output still reaches launch. Those are
tested against a real tmux server, because they are exactly the part that cannot
be reasoned about from the code -- tmux's own reporting of a dead pane turned
out to be unreliable, and a shell in the pane blurred signals into 128+N, which
is why the command runs under libtmux_easy's `lte-run` and reports over a socket.
"""

import pathlib
import shutil
import signal
import subprocess
import sys

import pytest

from ros2_typed_launch import monitor as mon
from ros2_typed_launch import tmux as tm

SHIM = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "tlaunch-pane"

needs_tmux = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux is not installed"
)
needs_runner = pytest.mark.skipif(
    shutil.which(tm.RUNNER) is None or shutil.which("tmux") is None,
    reason=f"tmux or {tm.RUNNER} (libtmux_easy) is not installed",
)


# --------------------------------------------------------- what gets a pane
def test_which_processes_get_a_pane():
    assert tm.selected((), "turtlesim_node-1", "/demo/turtlesim")   # no patterns: all

    only = ("turtlesim*",)
    assert tm.selected(only, "turtlesim_node-1", None)      # the process name
    assert not tm.selected(only, "chatter-2", None)
    # launch's -N counter must not get in the way of an exact name
    assert tm.selected(("chatter",), "chatter-2", None)
    # and a node is addressable by its fully qualified name
    assert tm.selected(("/demo/*",), "turtlesim_node-1", "/demo/turtlesim")
    assert not tm.selected(("/other/*",), "turtlesim_node-1", "/demo/turtlesim")


# ------------------------------------------------------ the command and label
def test_the_wrapped_command_names_a_window_and_keeps_the_original():
    panes = tm.Panes("demo")
    panes.script = "/usr/bin/tlaunch-pane"      # no tmux needed for this
    panes.runner = "/usr/bin/lte-run"
    panes.session = "tlaunch-demo"
    panes.windows[""] = "demo"                  # pretend the window exists
    panes.spare["demo"] = "%0"                  # and its placeholder shell
    wrapped = panes.wrap(["turtlesim_node", "--ros-args"], "", "/demo/turtlesim")

    assert wrapped[0] == panes.script
    assert wrapped[wrapped.index("--window") + 1] == "tlaunch-demo:demo"
    assert wrapped[wrapped.index("--title") + 1] == "/demo/turtlesim"
    assert wrapped[wrapped.index("--runner") + 1] == "/usr/bin/lte-run"
    # the real command survives untouched, after the -- separator
    assert wrapped[wrapped.index("--") + 1:] == ["turtlesim_node", "--ros-args"]
    assert panes.holds(wrapped) and not panes.holds(["turtlesim_node"])

    # tmux will not make a window without a pane, and that pane runs a shell
    # no process will use -- so the first process is told to close it, once.
    assert wrapped[wrapped.index("--retire") + 1] == "%0"
    assert "--retire" not in panes.wrap(["b"], "", "b")

    # The death report is about the node, not about how its output got to a
    # screen, so monitor.py takes the wrapper back off.
    assert mon.real_cmd(wrapped) == ["turtlesim_node", "--ros-args"]
    assert mon.real_cmd(["turtlesim_node", "--x"]) == ["turtlesim_node", "--x"]
    assert mon.real_cmd([]) == []


def test_grouping_and_output_settings():
    """`window(...)` only sets an environment variable; this is the other end
    of that, and why it works around an include. And a process in a pane keeps
    its log files but leaves the screen to the pane."""
    from launch import LaunchContext
    from launch.actions import ExecuteProcess, SetEnvironmentVariable

    from ros2_typed_launch import window

    group = window("teleop", [])
    setters = [
        entity for entity in group.get_sub_entities()
        if isinstance(entity, SetEnvironmentVariable)
    ]
    assert len(setters) == 1
    assert "".join(part.text for part in setters[0].name) == tm.WINDOW_ENV
    assert "".join(part.text for part in setters[0].value) == "teleop"

    for output, expected in [
        ("screen", {"both": {"log"}, "stdout": set(), "stderr": set()}),
        ("both", {"both": {"log"}, "stdout": set(), "stderr": set()}),
        ("log", {"both": {"log"}, "stdout": set(), "stderr": set()}),
        ("own_log", {"both": {"own_log"}, "stdout": {"own_log"}, "stderr": {"own_log"}}),
    ]:
        action = ExecuteProcess(cmd=["x"], output=output)
        tm.off_screen(action, LaunchContext())
        assert action._ExecuteLocal__output == expected, output


# ------------------------------------------------------------ against tmux
#: A session name of our own on the default tmux server -- which is the one
#: the shim talks to, so the real path is what gets tested.
SESSION = "tlaunch-selftest"


@pytest.fixture
def window_target():
    """An empty tmux window to split, torn down afterwards."""
    def tmux(*args):
        return subprocess.run(("tmux",) + args, text=True, capture_output=True)

    tmux("kill-session", "-t", f"={SESSION}")
    tmux("new-session", "-d", "-s", SESSION, "-n", "w", "-x", "200", "-y", "60")
    tmux("set-option", "-w", "-t", f"{SESSION}:w", "remain-on-exit", "on")
    yield f"{SESSION}:w"
    tmux("kill-session", "-t", f"={SESSION}")


def run_in_pane(target, *command):
    return subprocess.run(
        [sys.executable, str(SHIM), "--window", target, "--title", "probe",
         "--runner", shutil.which(tm.RUNNER), "--", *command],
        capture_output=True, text=True,
    )


def dead_pane_option(target, option, field=None) -> list[str]:
    """An option the shim wrote on its (dead) panes, for tlaunch to read; or
    any other format 'field' of them."""
    field = field or f"{tm.OPTION_PREFIX}{option}"
    out = subprocess.run(("tmux", "list-panes", "-t", target, "-F",
                          f"#{{pane_dead}}|#{{{field}}}"),
                         capture_output=True, text=True).stdout
    return [line.split("|")[1] for line in out.split("\n") if line.startswith("1|")]


@needs_runner
def test_a_process_in_a_pane_reports_its_real_exit_code(window_target):
    """The contract the whole thing rests on: launch sees what really happened.

    Launch cannot be handed a process it does not own, so without this a run
    whose node died in a pane would look like a run that went fine.
    """
    for script, expected in [
        ("exit 0", 0),
        ("exit 7", 7),
        ("exit 137", 137),         # an exit code, not a SIGKILL: no shell guessing
        ("kill -15 $$", -15),      # launch records a signal as -N, as it would its own
    ]:
        result = run_in_pane(window_target, "sh", "-c", script)
        assert "running it here instead" not in result.stderr   # it really used a pane
        assert result.returncode == expected, script
        assert dead_pane_option(window_target, "status") == [str(expected)]
        for pane in dead_pane_option(window_target, "", field="pane_id"):
            subprocess.run(("tmux", "kill-pane", "-t", pane))


@needs_runner
def test_a_forwarded_signal_reaches_the_command_alone(window_target, tmp_path):
    """Launch stops a process with SIGINT, then SIGTERM. A node that catches it
    and exits 3 must be reported as exit 3 -- not as killed by the signal,
    which is what happened while a shell sat in the pane and took it too."""
    started = tmp_path / "started"
    code = (
        "import signal, sys, time, pathlib\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(3))\n"
        f"pathlib.Path({str(started)!r}).touch()\n"
        "time.sleep(30)\n"
    )
    shim = subprocess.Popen(
        [sys.executable, str(SHIM), "--window", window_target,
         "--runner", shutil.which(tm.RUNNER), "--", sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
    )
    for _ in range(100):
        if started.exists():
            break
        subprocess.run(["sleep", "0.1"])
    shim.send_signal(signal.SIGTERM)
    assert shim.wait(timeout=10) == 3
    # the real status, and that it was stopped on purpose: not a failure to
    # keep the session open for
    assert dead_pane_option(window_target, "status") == ["3"]
    assert dead_pane_option(window_target, "stopped") == ["SIGTERM"]


@needs_runner
def test_arguments_go_in_and_output_comes_back_intact(window_target, tmp_path):
    """A ROS parameter value is full of spaces and braces. The output comes
    back on the shim's stdout -- where launch reads it -- from the first line
    (the command is held until the copy is in place), without the terminal's
    colour codes, and stderr with it."""
    out = tmp_path / "out"
    result = run_in_pane(window_target, "sh", "-c", f'printf %s "$1" > {out}',
                         "sh", "{linear: {x: 1.2}}")
    assert result.returncode == 0
    assert out.read_text() == "{linear: {x: 1.2}}"

    result = run_in_pane(
        window_target, "sh", "-c",
        'echo first; printf "\\033[31mred\\033[0m\\n"; echo oops >&2; echo last')
    assert result.stdout.splitlines() == ["first", "red", "oops", "last"]


@needs_tmux
def test_a_pane_cannot_be_had_so_the_process_runs_anyway(tmp_path):
    """A pane is a convenience. If it cannot be made, the command still runs --
    with this script's own pid, so launch is none the wiser."""
    marker = tmp_path / "ran"
    result = subprocess.run(
        [sys.executable, str(SHIM), "--window", "no-such-session:no-such-window",
         "--", "sh", "-c", f"touch {marker}; exit 5"],
        capture_output=True, text=True,
    )
    assert result.returncode == 5          # the command's own code, not an error
    assert marker.exists()
    assert "running it here instead" in result.stderr


# ------------------------------------------------------------------- the flags
def own(**overrides):
    flags = {"tmux": False, "tmux_only": [], "tmux_session": None,
             "tmux_dash": False, "tmux_logs": False}
    return {**flags, **overrides}


def test_the_tmux_flags():
    """Each flag is enough on its own: having to pass --tmux *as well* would
    only be a way to get silently no panes."""
    from ros2_typed_launch import cli

    assert cli.tmux_label(["pkg", "turtle_demo_launch.py"]) == "turtle_demo"
    assert cli.tmux_label(["pkg", "turtle_bringup_script.py"]) == "turtle_bringup"
    # tmux reads '.' and ':' in a name as part of a target address
    assert cli.tmux_label(["/a/b/my.odd-name_launch.py"]) == "my-odd-name"
    assert cli.tmux_label([]) == "launch"

    with cli.panes_for(own(), ["pkg", "demo_launch.py"]) as panes:
        assert panes is None

    if shutil.which("tmux") is None or shutil.which(tm.RUNNER) is None:
        pytest.skip(f"tmux or {tm.RUNNER} is not installed")
    for flags in (own(tmux=True),
                  own(tmux_only=["turtlesim*"]),     # picking processes asks for panes
                  own(tmux_session="mine")):         # so does naming a session
        with cli.panes_for(flags, ["pkg", "demo_launch.py"]) as panes:
            assert panes is not None                 # no session is made yet
        assert panes.finish() == ""                  # nothing was ever opened
