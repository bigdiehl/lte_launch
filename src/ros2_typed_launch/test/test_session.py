"""Imperative launching (Q26): the session, its handles and its waits.

The waits and the bookkeeping are driven here with a fake service and fake
launch events -- fast, and they can assert the shutdown/timeout edges that are
hard to provoke for real. One test at the end runs a real ``LaunchService``
end to end, so the threading contract itself is covered too.
"""

import re
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("launch")

from launch.actions import ExecuteProcess  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from ros2_typed_launch import launch as launch_decorator  # noqa: E402
from ros2_typed_launch import script, session as sess  # noqa: E402


class FakeService:
    """Stands in for LaunchService: records instead of running."""

    def __init__(self):
        self.included = []
        self.events = []
        self.shutdowns = 0

    def include_launch_description(self, description):
        self.included.append(description)

    def emit_event(self, event):
        self.events.append(event)

    def shutdown(self):
        self.shutdowns += 1


def make_session() -> tuple[sess.Session, FakeService]:
    service = FakeService()
    return sess.Session(service), service


def started(action, pid=111, name="proc-1", env=None):
    return SimpleNamespace(
        action=action, pid=pid, process_name=name, cmd=["a", "b"], env=env or {}
    )


def exited(action, returncode=0, name="proc-1", env=None):
    event = started(action, name=name, env=env)
    event.returncode = returncode
    return event


def io(action, text, name="proc-1", env=None):
    return SimpleNamespace(
        action=action, process_name=name, env=env or {}, text=text.encode()
    )


# --------------------------------------------------------------- small helpers
def test_matcher_takes_a_substring_or_a_regex():
    assert sess._matcher("ready")("node is ready now")
    assert not sess._matcher("ready")("still waiting")
    assert sess._matcher(re.compile(r"rc=\d+"))("exit rc=3")
    with pytest.raises(TypeError, match="string or a compiled regex"):
        sess._matcher(3)


def test_declared_name_reads_through_launchs_normalization():
    # ExecuteProcess turns name="driver" into [TextSubstitution("driver")].
    assert sess._declared_name(ExecuteProcess(name="driver", cmd=["x"])) == "driver"
    assert sess._declared_name(ExecuteProcess(cmd=["x"])) is None

    from launch.substitutions import LaunchConfiguration
    from launch_ros.actions import Node

    assert sess._declared_name(Node(package="p", executable="e", name="planner")) == "planner"
    # Unnamed, or named at runtime: no key until it starts.
    assert sess._declared_name(Node(package="p", executable="e")) is None
    runtime = Node(package="p", executable="e", name=LaunchConfiguration("n"))
    assert sess._declared_name(runtime) is None


def test_execute_processes_walks_into_groups_only_as_far_as_it_can():
    from launch.actions import GroupAction, IncludeLaunchDescription

    inner = ExecuteProcess(name="inner", cmd=["x"])
    outer = ExecuteProcess(name="outer", cmd=["x"])
    found = sess._execute_processes([outer, GroupAction([inner])])
    assert found == [outer, inner]

    # An include has not been read yet, so its processes are not knowable here;
    # they are attributed at runtime instead (STAGE_ENV).
    include = IncludeLaunchDescription.__new__(IncludeLaunchDescription)
    assert sess._execute_processes([include]) == []


# ------------------------------------------------------------------ the waits
def test_wait_returns_what_the_predicate_found():
    session, _ = make_session()
    assert session._wait(lambda: "value", 1.0, "something") == "value"


def test_wait_times_out_with_a_message_naming_what_it_wanted():
    session, _ = make_session()
    with pytest.raises(sess.WaitTimeout, match="waiting for the moon"):
        session._wait(lambda: False, 0.1, "the moon")


def test_wait_gives_up_as_soon_as_shutdown_starts():
    """The Ctrl-C contract: a wait raises at once so the script's cleanup runs."""
    session, _ = make_session()
    threading.Timer(0.05, lambda: session._on_shutdown(SimpleNamespace(), None)).start()
    start = time.monotonic()
    with pytest.raises(sess.SessionShutdown, match="waiting for a signal"):
        session._wait(lambda: False, 30.0, "a signal")   # would block for 30s
    assert time.monotonic() - start < 5.0


def test_sleep_is_interruptible_but_otherwise_just_returns():
    session, _ = make_session()
    session.sleep(0.05)                                  # no exception
    session._on_shutdown(SimpleNamespace(due_to_sigint=True), None)
    with pytest.raises(sess.SessionShutdown):
        session.sleep(0.05)
    assert session.shutting_down and session._interrupted


# --------------------------------------------------------------- stage/process
class DemoConfig(BaseModel):
    n: int = 1


@launch_decorator(config=DemoConfig)
def demo_launch(config: DemoConfig):
    return [
        ExecuteProcess(name="first", cmd=["echo", str(config.n)]),
        ExecuteProcess(name="second", cmd=["echo", "2"]),
    ]


def test_start_builds_a_stage_keyed_by_declared_name():
    session, service = make_session()
    stage = session.start(demo_launch, DemoConfig(n=5))

    assert sorted(stage.processes) == ["first", "second"]
    assert len(service.included) == 1          # queued with the service, once
    assert stage["first"].started is False     # nothing has run
    with pytest.raises(KeyError, match="has no process named 'third'"):
        stage["third"]


def test_start_rejects_the_wrong_config_type():
    class Other(BaseModel):
        pass

    session, _ = make_session()
    with pytest.raises(TypeError, match="takes DemoConfig, got Other"):
        session.start(demo_launch, Other())


def test_start_accepts_actions_a_factory_or_a_launch_description():
    import launch

    session, _ = make_session()
    assert session.start([ExecuteProcess(name="a", cmd=["x"])]).processes.keys() == {"a"}
    assert session.start(ExecuteProcess(name="b", cmd=["x"])).processes.keys() == {"b"}
    assert session.start(
        launch.LaunchDescription([ExecuteProcess(name="c", cmd=["x"])])
    ).processes.keys() == {"c"}
    assert session.start(
        lambda: [ExecuteProcess(name="d", cmd=["x"])], name="made"
    ).name == "made"
    with pytest.raises(TypeError, match="start\\(\\) takes"):
        session.start("not an action")


def test_duplicate_names_stay_addressable():
    session, _ = make_session()
    stage = session.start(
        [ExecuteProcess(name="same", cmd=["x"]), ExecuteProcess(name="same", cmd=["x"])]
    )
    assert sorted(stage.processes) == ["same", "same#2"]


def test_only_a_rebuildable_stage_can_restart():
    session, _ = make_session()
    assert session.start(demo_launch, DemoConfig()).rebuildable
    assert session.start(lambda: [ExecuteProcess(name="a", cmd=["x"])]).rebuildable

    from_actions = session.start([ExecuteProcess(name="b", cmd=["x"])])
    assert not from_actions.rebuildable
    with pytest.raises(sess.SessionError, match="will not run an action twice"):
        from_actions.restart()


def test_restart_rebuilds_and_keeps_the_same_keys():
    session, service = make_session()
    stage = session.start(demo_launch, DemoConfig())
    first, second = stage["first"], stage["second"]
    session._on_start(started(first.action), None)

    # restart() stops the stage and waits for it to be gone before rebuilding;
    # with a fake service, the exits have to be delivered by hand.
    def die():
        session._on_exit(exited(first.action), None)
        session._on_exit(exited(second.action), None)

    threading.Timer(0.05, die).start()
    stage.restart(timeout=5.0)

    assert sorted(stage.processes) == ["first", "second"]   # same keys
    assert stage["first"] is not first                      # new process
    assert stage["first"].action is not first.action        # freshly built action
    assert len(service.included) == 2                       # started again
    assert service.events                                   # the old one was stopped


# ------------------------------------------------------- events -> handle state
def test_events_fill_in_pid_output_and_exit_status():
    session, _ = make_session()
    stage = session.start(demo_launch, DemoConfig())
    action = stage["first"].action

    session._on_start(started(action, pid=4242), None)
    assert stage["first"].pid == 4242 and stage["first"].running
    assert stage.wait_started.__doc__          # (documented)

    session._on_io(io(action, "line one\nnode is ready\n"))
    assert stage["first"].output() == ["line one", "node is ready"]
    assert stage["first"].wait_output("ready", timeout=0.1) == "node is ready"
    assert stage.wait_output("line one", timeout=0.1) == "line one"

    session._on_exit(exited(action, returncode=3), None)
    assert stage["first"].wait_exited(0.1) == 3
    assert not stage["first"].running


def test_a_process_from_an_included_launch_file_lands_in_its_stage():
    """We never see that action, so the stage marker in the environment is what
    attributes it (that is what STAGE_ENV is for)."""
    session, _ = make_session()
    stage = session.start(demo_launch, DemoConfig())
    surprise = ExecuteProcess(cmd=["x"])     # not built by us

    session._on_start(
        started(surprise, name="helper-7", env={sess.STAGE_ENV: stage.id}), None
    )

    assert stage["helper-7"].pid == 111
    assert session.processes                 # also in the session-wide registry


def test_an_unattributable_process_is_still_tracked():
    session, _ = make_session()
    orphan = ExecuteProcess(cmd=["x"])
    session._on_start(started(orphan, name="orphan-1"), None)
    assert [p.key for p in session.processes] == ["orphan-1"]
    assert session.stages == []


def test_stage_wait_started_needs_every_process():
    session, _ = make_session()
    stage = session.start(demo_launch, DemoConfig())
    session._on_start(started(stage["first"].action), None)
    with pytest.raises(sess.WaitTimeout, match="stage 'demo_launch' to start"):
        stage.wait_started(timeout=0.1)
    session._on_start(started(stage["second"].action), None)
    stage.wait_started(timeout=0.1)


def test_stop_emits_one_shutdown_event_per_running_process():
    from launch.events.process import ShutdownProcess

    session, service = make_session()
    stage = session.start(demo_launch, DemoConfig())
    session._on_start(started(stage["first"].action), None)

    stage.stop(wait=False)

    assert len(service.events) == 1          # only the running one
    event = service.events[0]
    assert isinstance(event, ShutdownProcess)
    assert event.process_matcher(stage["first"].action)
    assert not event.process_matcher(stage["second"].action)


def test_a_process_from_an_include_can_still_be_stopped_on_its_own():
    """We did not build its action, but the start event handed it to us."""
    from launch.events.process import ShutdownProcess

    session, service = make_session()
    stage = session.start(demo_launch, DemoConfig())
    surprise = ExecuteProcess(cmd=["x"])
    session._on_start(
        started(surprise, name="helper-7", env={sess.STAGE_ENV: stage.id}), None
    )

    stage["helper-7"].stop(wait=False)

    event = service.events[-1]
    assert isinstance(event, ShutdownProcess) and event.process_matcher(surprise)
    assert session.monitor.get(surprise).stop_requested   # so it is not a "death"


def test_process_restart_points_at_the_stage():
    session, _ = make_session()
    stage = session.start(demo_launch, DemoConfig())
    with pytest.raises(sess.SessionError, match="restart its stage"):
        stage["first"].restart()


# ----------------------------------------------------------- the real service
class ScriptConfig(BaseModel):
    word: str = "hello"


@script(config=ScriptConfig)
def demo_script(session, config: ScriptConfig):
    stage = session.start(
        lambda: [ExecuteProcess(name="talker", cmd=["echo", config.word], output="screen")],
    )
    stage.wait_started(timeout=20.0)
    assert stage["talker"].pid > 0
    line = stage["talker"].wait_output(config.word, timeout=20.0)
    assert stage["talker"].wait_exited(timeout=20.0) == 0
    return 0 if line.strip() == config.word else 1


def test_run_session_drives_a_real_launch_service():
    """The whole contract, for real: service on the main thread, script on a
    worker, output and exit status observed through the handles."""
    assert sess.run_session(
        lambda session: demo_script.run(session, ScriptConfig(word="banana"))
    ) == 0


def test_a_script_that_raises_stops_the_session_and_propagates():
    def body(session):
        stage = session.start(
            lambda: [ExecuteProcess(name="sleeper", cmd=["sleep", "60"])]
        )
        stage.wait_started(timeout=20.0)
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        sess.run_session(body)
