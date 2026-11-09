"""lte-launch's tmux modes end to end, on a private tmux server (its own
TMUX_TMPDIR, so no session of the user's is ever seen or touched):
--tmux-dash (the dashboard window, kept at the end), and --tmux from inside
a tmux pane (our windows join that session, named '<launch>/<window>';
only they are closed at a clean end, and kept on a failure)."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import textwrap

import pytest

from _live import Reader, until

pytestmark = pytest.mark.live

LAUNCH = """
from pydantic import BaseModel
from lte_launch import launch, process


class TmuxLiveConfig(BaseModel):
    mode: str = "finish"


@launch
def tmuxlive(config: TmuxLiveConfig):
    if config.mode == "finish":    # both processes up, then a clean end
        process("side", "echo side up; sleep 60")
        process("main", "echo main up; sleep 4", required=True)
    elif config.mode == "serve":   # runs until Ctrl-C
        process("serve", "echo serving; sleep 60")
    elif config.mode == "die":     # a death: exit 1, kept
        process("main", "sleep 1; exit 3")
"""


@pytest.fixture
def target(tmp_path):
    if not shutil.which("lte-launch"):
        pytest.skip("lte-launch not on PATH (source install/setup.bash)")
    if not (shutil.which("tmux") and shutil.which("lte-run")):
        pytest.skip("--tmux needs tmux and libtmux_easy's lte-run on PATH")
    pytest.importorskip("libtmux_easy.processes")
    path = tmp_path / "tmuxlive_launch.py"
    path.write_text(textwrap.dedent(LAUNCH))
    return str(path)


class Server:
    """A tmux server of our own: TMUX_TMPDIR in a fresh dir (short: a
    socket path), and no TMUX/TMUX_PANE, so neither this test nor the
    lte-launch it runs can reach the user's server."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="tgtmux_", dir="/tmp")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("TMUX", "TMUX_PANE", "LTE_HEADLESS")}  # fmt: skip
        self.env["TMUX_TMPDIR"] = self.dir

    def tmux(self, *args, check=False) -> str:
        p = subprocess.run(["tmux", *args], env=self.env, capture_output=True,
                           text=True, timeout=10)  # fmt: skip
        if check and p.returncode:
            raise AssertionError(f"tmux {' '.join(args)}: {p.stderr}")
        return p.stdout

    def has(self, session: str) -> bool:
        return subprocess.run(["tmux", "has-session", "-t", f"={session}"],
                              env=self.env, capture_output=True).returncode == 0  # fmt: skip

    def windows(self, session: str) -> list[str]:
        out = self.tmux("list-windows", "-t", f"={session}", "-F", "#{window_name}")
        return out.split()

    def pane_commands(self, session: str) -> str:
        """Every pane's start command in the session."""
        return self.tmux("list-panes", "-s", "-t", f"={session}", "-F",
                         "#{window_name} #{pane_start_command}")  # fmt: skip

    def screens(self, window: str) -> str:
        """What every pane of ``window`` shows (wrap mode types the command
        into a shell, so this is where its output is)."""
        ids = self.tmux("list-panes", "-t", window, "-F", "#{pane_id}").split()
        return "\n".join(self.tmux("capture-pane", "-p", "-t", i) for i in ids)

    def close(self):
        self.tmux("kill-server")
        shutil.rmtree(self.dir, ignore_errors=True)


def shown(screens: str, *lines: str) -> bool:
    """Each line is on screen as output, not just in the command typed in."""
    return all(re.search(rf"(?m)^{line}$", screens) for line in lines)


@pytest.fixture
def server():
    s = Server()
    yield s
    s.close()


def test_tmux_dash(target, server, spawn):
    if not shutil.which("lte-dash"):
        pytest.skip("--tmux-dash needs libtmux_easy's lte-dash on PATH")
    name = f"tg-dash-{os.getpid()}"

    # A clean end (the required process ends): exit 0, the session kept.
    out = Reader(spawn(["lte-launch", target, "--tmux-dash", "--tmux-session", name],
                       env=server.env, stdin=subprocess.DEVNULL))  # fmt: skip
    out.wait_for(r"\[lte-launch\]: up: 2 process", 30)
    assert f"tmux session: tmux attach -t {name}" in out.text
    # The dashboard window (sidebar + viewport) and our processes, in panes.
    until(lambda: "dash" in server.windows(name), 10, "the dash window")
    assert "tmuxlive" in server.windows(name)
    panes = server.pane_commands(name)
    assert "lte-dash" in panes or "libtmux_easy.dash" in panes, panes
    assert len(server.tmux("list-panes", "-t", f"={name}:dash").split()) >= 2
    until(lambda: shown(server.screens(f"={name}:tmuxlive"), "side up", "main up"),
          10, "both processes' output in their panes")  # fmt: skip
    assert out.finish(30) == 0, out.text
    assert "shutdown: tmuxlive:main (required) ended" in out.text
    assert f"tmux session kept: tmux attach -t {name}" in out.text
    assert server.has(name) and "dash" in server.windows(name)
    server.tmux("kill-session", "-t", f"={name}")

    # Ctrl-C: exit 0, still kept (the dashboard is there to look at).
    out = Reader(spawn(["lte-launch", target, "--mode", "serve", "--tmux-dash",
                        "--tmux-session", name], env=server.env,
                       stdin=subprocess.DEVNULL))  # fmt: skip
    out.wait_for(r"\[lte-launch\]: up: 1 process", 30)
    until(lambda: "dash" in server.windows(name), 10, "the dash window")
    out.proc.send_signal(signal.SIGINT)
    assert out.finish(30) == 0, out.text
    assert "interrupted; stopping" in out.text
    assert f"tmux session kept: tmux attach -t {name}" in out.text and server.has(name)


def test_tmux_joins_the_current_session(target, server, tmp_path):
    host = f"tg-host-{os.getpid()}"
    server.tmux("new-session", "-d", "-s", host, "-n", "keep", "sleep 600", check=True)

    def inside(*args):
        """Run lte-launch in a new window of ``host``, as a user would from
        a shell there ($TMUX set by tmux); -> (exit code file, output file)."""
        log, code = tmp_path / "out.log", tmp_path / "code"
        for f in (log, code):
            f.unlink(missing_ok=True)
        cmd = (f"lte-launch --tmux {shlex.join(args)} </dev/null >{log} 2>&1; "
               f"echo $? >{code}.tmp; mv {code}.tmp {code}")  # fmt: skip
        server.tmux("new-window", "-d", "-t", f"={host}:", "-n", "runner", cmd,
                    check=True)  # fmt: skip
        return code, log

    # A clean end: our window '<launch>/<window>' was in the host session
    # while it ran, and only it is closed; the session and 'keep' stay.
    code, log = inside(target)
    until(lambda: "tmuxlive/tmuxlive" in server.windows(host), 30, "our window")
    assert server.windows(host).count("tmuxlive/tmuxlive") == 1
    until(lambda: shown(server.screens(f"={host}:tmuxlive/tmuxlive"), "side up",
                        "main up"), 10, "both processes' output in our panes")  # fmt: skip
    until(code.exists, 40, "lte-launch to end")
    text = log.read_text()
    assert code.read_text().strip() == "0", text
    assert "shutdown: tmuxlive:main (required) ended" in text
    assert "tmux attach" not in text  # no session of its own to attach to
    assert server.has(host)
    until(lambda: "tmuxlive/tmuxlive" not in server.windows(host), 10, "ours closed")
    assert "keep" in server.windows(host)
    assert server.tmux("list-sessions", "-F", "#{session_name}").split() == [host]

    # A failure: our window is kept to look at, in the same session.
    code, log = inside(target, "--mode", "die")
    until(code.exists, 40, "lte-launch to end")
    text = log.read_text()
    assert code.read_text().strip() == "1", text
    assert "died unexpectedly: exit code 3" in text and "tmux windows kept" in text
    windows = server.windows(host)
    assert "tmuxlive/tmuxlive" in windows and "keep" in windows, windows
    assert server.tmux("list-sessions", "-F", "#{session_name}").split() == [host]
