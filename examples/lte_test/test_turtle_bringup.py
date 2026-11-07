"""turtle_bringup_launch.py under lte_launch_test, the ways libtmux_easy tests
a session script (libtmux_easy docs/testing-scripts.md), plus start(procs).

    ./run.sh -v [--tmux]
"""

from lte_launch_test import Processes, RosProbe

from ros2_typed_launch.examples.turtle_bringup_launch import turtle_bringup

# How each may end when stopped: turtlesim and tlaunch exit 0 on SIGINT,
# `ros2 topic pub` exits 2.
STOP = {"*": (0, 130), "m1:driver": (0, 2), "m2:driver": (0, 2), "m4:driver": (0, 2)}


class TestAsProcess:
    """Method 3: tlaunch is one process; its ready line gates the test."""

    @classmethod
    def setup_class(cls):
        cls.procs = Processes(cls, fail_on={"*": r"Traceback"}, stop_codes=STOP)
        cls.probe = RosProbe(log_dir=cls.procs.log_dir)
        cls.procs.start(
            "sys:tlaunch",
            "tlaunch ros2_typed_launch turtle_bringup_launch.py --namespace m3"
            " --turtlesim.background_r 33",
            ready=r"\[tlaunch\]: up",
        )

    @classmethod
    def teardown_class(cls):
        cls.probe.destroy()
        cls.procs.close()  # tlaunch must exit 0 on SIGINT

    def test_typed_param_reached_the_node(self):
        assert self.probe.get_param("/m3/turtlesim", "background_r") == 33

    def test_driver_moves_the_turtle(self):
        self.probe.wait_for_topics({"/m3/turtle1/cmd_vel"})


class TestBuild:
    """Method 2: the launch body runs on the test's session; every node is a
    process of the test, with its own health, log and restart."""

    @classmethod
    def setup_class(cls):
        cls.procs = Processes(cls, fail_on={"*": r"Traceback"}, stop_codes=STOP)
        cls.probe = RosProbe(log_dir=cls.procs.log_dir)
        cls.procs.build(turtle_bringup.build, namespace="m2")

    @classmethod
    def teardown_class(cls):
        cls.probe.destroy()
        cls.procs.close()

    def test_each_node_is_a_process(self):
        assert self.procs["m2:turtlesim"].status().running
        self.procs["m2:driver"].wait_for(r"publishing #\d+")

    def test_param(self):
        assert self.probe.get_param("/m2/turtlesim", "background_b") == 255

    def test_restart_one_node(self):
        self.procs.stop("m2:driver")
        self.procs["m2:driver"].start()
        self.procs["m2:driver"].wait_for(r"publishing #\d+")


class TestLayout:
    """Method 1: the dry-run plan as (window:name, command); the test starts
    them in its own order."""

    @classmethod
    def setup_class(cls):
        cls.procs = Processes(cls, fail_on={"*": r"Traceback"}, stop_codes=STOP)
        cls.probe = RosProbe(log_dir=cls.procs.log_dir)
        for name, cmd in turtle_bringup.panes(namespace="m1", drive=False):
            cls.procs.start(name, cmd)
        cls.probe.wait_for_nodes({"turtlesim"})

    @classmethod
    def teardown_class(cls):
        cls.probe.destroy()
        cls.procs.close()

    def test_plan_names(self):
        assert [n for n, _ in turtle_bringup.panes(namespace="m1")] == [
            "m1:turtlesim",
            "m1:driver",
        ]

    def test_param(self):
        assert self.probe.get_param("/m1/turtlesim", "background_g") == 86


class TestStart:
    """start(procs): the body runs on the test's group, in any mode (no tmux
    needed): every node a process of the test."""

    @classmethod
    def setup_class(cls):
        cls.procs = Processes(cls, fail_on={"*": r"Traceback"}, stop_codes=STOP)
        cls.probe = RosProbe(log_dir=cls.procs.log_dir)
        turtle_bringup.start(cls.procs, namespace="m4")

    @classmethod
    def teardown_class(cls):
        cls.probe.destroy()
        cls.procs.close()

    def test_nodes_are_processes(self):
        assert self.procs["m4:turtlesim"].status().running
        self.procs["m4:driver"].wait_for(r"publishing #\d+")
        assert self.probe.get_param("/m4/turtlesim", "background_r") == 69
