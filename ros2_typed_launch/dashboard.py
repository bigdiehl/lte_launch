"""The libtmux_easy dashboard (``lte-dash``) over a tlaunch run (Q29).

``--tmux-dash`` opens a window with a sidebar listing every process that is in
a pane, with its live status, and a viewport showing the selected one -- the
real pane, swapped in. Its *home* entry is this run's own output: launch's log
and every process that is not in a pane of its own.

libtmux_easy's ``TmuxSession`` cannot be used here (launch owns the processes;
see tmux.py), so this module does the three jobs it would otherwise do:

* opens the window, with ``lte-dash --open``;
* writes the status file the sidebar reads, from the pane options each
  ``tlaunch-pane`` records (one ``tmux list-panes`` per tick, for the session);
* answers the sidebar's commands on the control socket, through launch: a stop
  is launch's own ``ShutdownProcess`` (so the monitor knows it was asked for),
  ``Q`` is launch's ``Shutdown``, exactly like a Ctrl-C.

Home is fed by copying this process's stdout and stderr, at the file
descriptor, to a log file the home pane follows. The terminal still gets
everything.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

from .tmux import TmuxError, log, pane_rows

#: libtmux_easy's sidebar, on PATH (``uv tool install 'libtmux_easy[dash]'``).
DASH = "lte-dash"

#: Session options lte-dash sets, to be unset when a joined session is left.
SESSION_OPTIONS = ("@lte_dash_window", "@lte_dash_sidebar")

#: How often the status file is rewritten. lte-dash polls it at 0.5 s.
TICK = 0.5


def entry(pane: str, dead: str, values: dict[str, str]) -> dict | None:
    """One pane, in lte-dash's status-file shape (libtmux_easy PaneStatus).
    None for a pane that is not one of ours (the dashboard's own, a shell)."""
    if not values.get("name"):
        return None
    status = values.get("status", "")
    number = int(status) if status.lstrip("-").isdigit() else None
    exit_status = {"code": None, "signal": None, "inferred": False}
    if number is not None and number < 0:
        exit_status["signal"] = -number
    elif number is not None:
        exit_status["code"] = number
    running = number is None and dead != "1"
    target = values.get("target", "")
    return {
        "name": f"{values.get('window', '')}:{values['name']}",
        "pane_id": pane,
        "mode": "tlaunch",
        "command": values.get("command", ""),
        "target_pids": [int(target)] if target.isdigit() else [],
        "running": running,
        "exit_status": exit_status,
        "stopped": None if running else (values.get("stopped") or None),
    }


class HomeTee:
    """Copies fd 1 and 2 to a file as well as to where they went before.

    At the descriptor, not ``sys.stdout``: launch's log handlers hold their
    own reference to the stream, and anything else writing to the inherited
    descriptors would be missed.
    """

    def __init__(self, path: str):
        self.path = path
        self.log = open(path, "ab", buffering=0)
        self.lock = threading.Lock()
        self.saved: dict[int, int] = {}
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        for fd in (1, 2):
            (sys.stdout if fd == 1 else sys.stderr).flush()
            read, write = os.pipe()
            self.saved[fd] = os.dup(fd)
            os.dup2(write, fd)
            os.close(write)
            thread = threading.Thread(
                target=self._copy,
                args=(read, self.saved[fd]),
                name=f"tlaunch-home-{fd}",
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)

    def _copy(self, read: int, out: int) -> None:
        while True:
            chunk = os.read(read, 65536)
            if not chunk:
                break
            os.write(out, chunk)
            with self.lock:
                self.log.write(chunk)
        os.close(read)

    def stop(self) -> None:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except (OSError, ValueError):
                pass
        for fd, saved in self.saved.items():
            os.dup2(saved, fd)  # closes the pipe's last write end
        for thread in self.threads:
            thread.join(timeout=2.0)
        for saved in self.saved.values():
            os.close(saved)
        self.saved.clear()
        self.log.close()


class Dashboard:
    """The dashboard window of one run, and the two threads that feed it."""

    def __init__(self, session: str, key: str = "g"):
        self.session = session
        self.key = key  # the prefix key lte-dash borrows; "" for none
        self.dir = tempfile.mkdtemp(prefix="tlaunch-dash-")
        self.status_file = os.path.join(self.dir, "status.json")
        self.home_log = os.path.join(self.dir, "home.log")
        self.ctl = os.path.join(self.dir, "ctl.sock")
        self.window: str | None = None
        self.tee = HomeTee(self.home_log)
        self.server: socket.socket | None = None
        self.stopping = threading.Event()
        self.threads: list[threading.Thread] = []
        self.service = None
        self.monitor = None

    def open(self) -> str:
        """Open the window and start feeding it. Returns the window id."""
        self.write_status()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.ctl)
        self.server.listen(4)
        result = subprocess.run(
            [
                DASH,
                "--open",
                self.session,
                "--status",
                self.status_file,
                "--ctl",
                self.ctl,
                "--home-log",
                self.home_log,
                "--key",
                self.key,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not result.stdout.split():
            raise TmuxError(f"{DASH} --open: {result.stderr.strip()}")
        self.window = result.stdout.split()[0]
        self.tee.start()
        for target, name in ((self._write_loop, "status"), (self._serve, "ctl")):
            thread = threading.Thread(
                target=target, name=f"tlaunch-dash-{name}", daemon=True
            )
            thread.start()
            self.threads.append(thread)
        return self.window

    def attach(self, service, monitor) -> None:
        """The run the sidebar's commands act on."""
        self.service, self.monitor = service, monitor

    def close(self, keep: bool) -> None:
        """Stop feeding the window. 'keep': the session stays to be read, so
        leave the status file and home log where the window can find them."""
        self.stopping.set()
        if self.server is not None:
            self.server.close()
        for thread in self.threads:
            thread.join(timeout=2.0)
        if self.window is not None:
            self.write_status()
            self.tee.stop()
        if not keep:
            shutil.rmtree(self.dir, ignore_errors=True)

    # ---------------------------------------------------------------- status
    def panes(self) -> list[tuple[str, str, dict[str, str]]]:
        """(pane id, dead, our options) for every pane in the session."""
        return pane_rows(self.session)

    def write_status(self) -> None:
        entries = [e for e in (entry(*row) for row in self.panes()) if e]
        tmp = f"{self.status_file}.tmp"
        try:
            with open(tmp, "w") as handle:
                json.dump(entries, handle, indent=2)
            os.replace(tmp, self.status_file)  # lte-dash never sees half a file
        except OSError:
            pass

    def _write_loop(self) -> None:
        while not self.stopping.wait(TICK):
            self.write_status()

    # --------------------------------------------------------------- control
    def _serve(self) -> None:
        while not self.stopping.is_set():
            try:
                conn, _ = self.server.accept()
            except OSError:
                return  # closed by close()
            with conn:
                conn.settimeout(1.0)
                data = b""
                try:
                    while chunk := conn.recv(4096):
                        data += chunk
                except OSError:
                    pass
            for line in data.split(b"\n"):
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                self.command(message.get("cmd"), message.get("pane"))

    def command(self, cmd: str | None, pane: str | None) -> None:
        """One action from the sidebar: x, X, Q (r cannot be done; see below)."""
        if self.service is None:
            return
        if cmd == "shutdown":
            from launch.events import Shutdown

            log().info("shutdown requested from the dashboard")
            self.service.emit_event(Shutdown(reason="dashboard Q"))
        elif cmd == "stop":
            self.stop([pane])
        elif cmd == "stop_all":
            self.stop([row[0] for row in self.panes() if row[2].get("name")])
        elif cmd == "respawn":
            # Launch will not execute an action twice, so a stopped process
            # cannot be started again from here.
            log().warning("rerun is not possible under launch; restart the run")

    def stop(self, panes: list[str]) -> None:
        """Stop the processes in these panes, the way launch stops one."""
        from launch.events import matches_action
        from launch.events.process import ShutdownProcess

        shims = {row[2].get("shim") for row in self.panes() if row[0] in panes}
        for info in self.monitor.all() if self.monitor else []:
            if info.running and str(info.pid) in shims:
                self.monitor.note_stop(info.action)  # asked for: not a death
                self.service.emit_event(
                    ShutdownProcess(process_matcher=matches_action(info.action))
                )
